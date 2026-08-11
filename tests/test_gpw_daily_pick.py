from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import gpw_daily_pick as gpw


WARSAW = ZoneInfo("Europe/Warsaw")


def fixture_bars(last_day: date, *, count: int = 90, rising: bool = True) -> list[gpw.Bar]:
    bars = []
    first = last_day - timedelta(days=count - 1)
    for index in range(count):
        base = 80 + (index * 0.5 if rising else 0)
        bars.append(
            gpw.Bar(
                day=first + timedelta(days=index),
                open=base - 0.2,
                high=base + 0.8,
                low=base - 0.8,
                close=base,
                volume=100_000,
            )
        )
    return bars


class GpwDailyPickTests(unittest.TestCase):
    def setUp(self):
        self.config = gpw.load_config()
        self.now = datetime(2026, 8, 11, 7, 40, tzinfo=WARSAW)
        self.expected = gpw.previous_session(self.now.date(), self.config)

    def test_config_has_complete_weight_contract_and_broad_universe(self):
        self.assertEqual(sum(self.config["weights"].values()), 100)
        self.assertGreaterEqual(len(self.config["universe"]), 40)
        self.assertFalse(self.config["learning"]["automatic_weight_changes"])
        self.assertFalse(gpw.is_session_day(date(2027, 3, 26), self.config))
        self.assertFalse(gpw.is_session_day(date(2027, 12, 24), self.config))

    def test_quant_candidate_enforces_freshness_and_liquidity(self):
        company = self.config["universe"][0]
        valid = gpw.build_quant_candidate(
            company,
            fixture_bars(self.expected),
            self.expected,
            self.config,
            [],
        )
        stale = gpw.build_quant_candidate(
            company,
            fixture_bars(self.expected - timedelta(days=1)),
            self.expected,
            self.config,
            [],
        )
        illiquid_bars = [
            gpw.Bar(bar.day, bar.open, bar.high, bar.low, bar.close, 50)
            for bar in fixture_bars(self.expected)
        ]
        illiquid = gpw.build_quant_candidate(
            company, illiquid_bars, self.expected, self.config, []
        )
        self.assertIsNotNone(valid)
        self.assertIsNone(stale)
        self.assertIsNone(illiquid)

    def test_learning_stays_neutral_until_configured_sample_exists(self):
        sector = "banki"
        row = {
            "selection": {"sector": sector},
            "outcome": {"status": "RESOLVED", "r_multiple": 1.0},
        }
        score_29, sample_29 = gpw.history_expectancy_score([row] * 29, sector, 30)
        score_30, sample_30 = gpw.history_expectancy_score([row] * 30, sector, 30)
        self.assertEqual((score_29, sample_29), (50.0, 29))
        self.assertGreater(score_30, 50)
        self.assertEqual(sample_30, 30)

    def test_generate_trade_requires_sources_gemini_and_second_review(self):
        source = {
            "id": "src-primary",
            "title": "Oficjalna aktualizacja emitenta",
            "url": "https://example.test/source",
            "publisher": "PAP Biznes",
            "published_at": "2026-08-11T07:00+02:00",
            "age_hours": 0.7,
            "quality": "pierwotne",
        }

        def analysis(candidates):
            return {
                candidate["symbol"]: {
                    "catalyst_score": 100,
                    "thesis": "Świeży, potwierdzony katalizator wspiera krótkoterminowy scenariusz.",
                    "why_now": "Informacja pojawiła się przed dzisiejszą sesją.",
                    "risk_factors": ["Rynek może zanegować reakcję."],
                    "source_ids": ["src-primary"],
                }
                for candidate in candidates
            }

        review = {
            "approved": True,
            "reason": "Teza ma pokrycie w źródle.",
            "supported_source_ids": ["src-primary"],
            "contradictions": [],
            "provider": "gemini",
            "model": "fixture",
        }
        with patch.object(gpw, "all_history", return_value=[]), patch.object(
            gpw, "gemini_analysis", side_effect=analysis
        ), patch.object(gpw, "gemini_review", return_value=review), patch.object(
            gpw, "now_warsaw", return_value=self.now
        ):
            payload = gpw.generate(
                now=self.now,
                market_fetcher=lambda _symbol: fixture_bars(self.expected),
                news_fetcher=lambda _company, now: [source],
            )

        gpw.validate_payload(payload, require_today=True, now=self.now)
        self.assertEqual(payload["decision"], "TRANSAKCJA")
        self.assertGreaterEqual(payload["selection"]["score"], 72)
        self.assertTrue(payload["selection"]["review"]["approved"])
        self.assertEqual(payload["selection"]["sources"][0]["id"], "src-primary")

    def test_no_fresh_sources_means_no_trade_without_ai_guessing(self):
        with patch.object(gpw, "all_history", return_value=[]), patch.object(
            gpw, "gemini_analysis"
        ) as mocked_ai:
            payload = gpw.generate(
                now=self.now,
                market_fetcher=lambda _symbol: fixture_bars(self.expected),
                news_fetcher=lambda _company, now: [],
            )
        self.assertEqual(payload["decision"], "BRAK_TRANSAKCJI")
        mocked_ai.assert_not_called()

    def test_incomplete_market_data_fails_closed(self):
        allowed = {row["symbol"] for row in self.config["universe"][:20]}

        def market(symbol):
            if symbol not in allowed:
                raise gpw.PublicationError("fixture missing")
            return fixture_bars(self.expected)

        with patch.object(gpw, "all_history", return_value=[]):
            payload = gpw.generate(now=self.now, market_fetcher=market)
        self.assertEqual(payload["decision"], "AWARIA_DANYCH")
        self.assertLess(payload["data_quality"]["complete_ratio"], 0.8)

    def test_stale_publication_is_rejected(self):
        payload = gpw.failure_payload(self.now, self.config, "test", "fixture")
        tomorrow = self.now + timedelta(days=1)
        with self.assertRaises(gpw.PublicationError):
            gpw.validate_payload(payload, require_today=True, now=tomorrow)

    def test_locked_healthy_history_cannot_be_replaced_by_error(self):
        healthy = gpw.common_payload(
            self.now, self.config, "BRAK_TRANSAKCJI", "Brak układu."
        )
        error = gpw.failure_payload(self.now, self.config, "Błąd", "fixture")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            public = base / "current.json"
            history_dir = base / "history"
            metrics = base / "metrics.json"
            audit = base / "audit"
            with patch.object(gpw, "PUBLIC_PATH", public), patch.object(
                gpw, "HISTORY_DIR", history_dir
            ), patch.object(gpw, "METRICS_PATH", metrics), patch.object(
                gpw, "AUDIT_DIR", audit
            ):
                self.assertTrue(gpw.publish(healthy))
                self.assertFalse(gpw.publish(error))
                saved = json.loads(public.read_text(encoding="utf-8"))
        self.assertEqual(saved["decision"], "BRAK_TRANSAKCJI")


if __name__ == "__main__":
    unittest.main()
