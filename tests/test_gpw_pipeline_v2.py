from __future__ import annotations

import math
import os
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import gpw_daily_pick as gpw
from scripts import gpw_expected_value as ev
from scripts import gpw_pipeline_v2 as pipeline

WARSAW = ZoneInfo("Europe/Warsaw")


def config() -> dict:
    return {
        "policy_version": "test",
        "publication_cutoff": "09:10",
        "minimum_data_completeness": 0.8,
        "minimum_composite_score": 72,
        "minimum_reward_risk": 1.5,
        "weights": {
            "catalyst": 25,
            "relative_momentum": 20,
            "volume_liquidity": 15,
            "market_context": 15,
            "risk_reward": 15,
            "historical_expectancy": 10,
        },
        "top_candidates_for_news": 2,
        "opening_confirmation": {
            "enabled": True,
            "engine": "gpw-opening-confirmation-v1",
            "top_candidates": 2,
            "weight": 0.25,
            "role": "bounded_reranking_overlay_not_hard_gate",
        },
        "universe": [
            {"symbol": "AAA.WA", "name": "AAA", "sector": "x"},
            {"symbol": "BBB.WA", "name": "BBB", "sector": "x"},
        ],
    }


def ev_config() -> dict:
    value = config()
    value["expected_value"] = {
        "enabled": True,
        "engine": "gpw-empirical-ev-v1",
        "horizon_sessions": 2,
        "rr_grid": [1.5, 1.75, 2.0, 2.25, 2.5],
        "minimum_analogs": 16,
        "maximum_analogs": 32,
        "confidence_sample": 28,
        "cost_assumption_percent": 0.38,
        "uncertainty_penalty": 0.35,
        "score_weight": 0.2,
    }
    return value


def bars() -> list[gpw.Bar]:
    return [
        gpw.Bar(date(2026, 8, 18), 100.0, 102.0, 99.0, 101.0, 100_000)
        for _ in range(70)
    ]


def empirical_bars(count: int = 110) -> list[gpw.Bar]:
    rows: list[gpw.Bar] = []
    price = 100.0
    start = date(2026, 1, 2)
    for index in range(count):
        cycle = index % 12
        session_move = 0.008 if cycle < 7 else -0.004
        gap = 0.0015 * math.sin(index / 3.0)
        open_price = price * (1.0 + gap)
        close = open_price * (1.0 + session_move)
        high = max(open_price, close) * (1.0 + 0.012 + (0.002 if cycle < 4 else 0.0))
        low = min(open_price, close) * (1.0 - 0.009)
        volume = 100_000 + (index % 9) * 8_000
        rows.append(
            gpw.Bar(
                start + timedelta(days=index),
                open_price,
                high,
                low,
                close,
                volume,
            )
        )
        price = close
    return rows


def candidate(symbol: str, quant_score: float) -> dict:
    return {
        "symbol": symbol,
        "name": symbol.removesuffix(".WA"),
        "sector": "x",
        "reference_price": 100.0,
        "stop": 97.0,
        "quant_pre_score": quant_score,
        "scores": {
            "relative_momentum": quant_score,
            "volume_liquidity": quant_score,
            "market_context": quant_score,
            "risk_reward": quant_score,
            "historical_expectancy": quant_score,
        },
    }


def opening_snapshot(symbol: str, now: datetime) -> dict:
    if symbol == "AAA.WA":
        return {
            "provider": "Yahoo",
            "symbol": symbol,
            "date": now.date().isoformat(),
            "observed_at": now.isoformat(),
            "open": 104.0,
            "high": 104.2,
            "low": 100.0,
            "last": 100.5,
            "volume": 10000,
            "crosscheck": {"status": "confirmed"},
        }
    return {
        "provider": "Yahoo",
        "symbol": symbol,
        "date": now.date().isoformat(),
        "observed_at": now.isoformat(),
        "open": 100.5,
        "high": 102.0,
        "low": 100.4,
        "last": 101.9,
        "volume": 10000,
        "crosscheck": {"status": "confirmed"},
    }


class GpwPipelineV2Tests(unittest.TestCase):
    def test_recovery_cutoff_is_date_bound(self):
        cfg = config()
        with patch.dict(os.environ, {"GPW_RECOVERY_DATE": "2026-08-19", "GPW_RECOVERY_CUTOFF": "12:30"}, clear=False):
            self.assertEqual(pipeline.cutoff_for(date(2026, 8, 19), cfg).strftime("%H:%M"), "12:30")
            self.assertEqual(pipeline.cutoff_for(date(2026, 8, 20), cfg).strftime("%H:%M"), "09:10")

    def test_opening_confirmation_reranks_primary_candidates(self):
        now = datetime(2026, 8, 19, 9, 6, tzinfo=WARSAW)
        eligible = [
            (80.0, candidate("AAA.WA", 80.0), {"catalyst_score": 50}),
            (76.0, candidate("BBB.WA", 76.0), {"catalyst_score": 50}),
        ]

        with patch.object(
            pipeline.market,
            "opening_snapshot",
            side_effect=lambda symbol, now: opening_snapshot(symbol, now),
        ):
            reranked, diagnostics = pipeline._rerank_with_opening(
                eligible,
                config=config(),
                now=now,
            )

        self.assertEqual(reranked[0][1]["symbol"], "BBB.WA")
        self.assertGreater(
            reranked[0][1]["opening_confirmation"]["score"],
            reranked[1][1]["opening_confirmation"]["score"],
        )
        self.assertEqual(reranked[0][1]["legacy_composite_score"], 76.0)
        self.assertEqual(reranked[0][1]["opening_confirmation_engine"], "gpw-opening-confirmation-v1")
        self.assertEqual(diagnostics["evaluated_candidates"], 2)
        self.assertEqual(diagnostics["selected_symbol"], "BBB.WA")

    def test_expected_value_selects_dynamic_rr_and_probabilities_sum_to_one(self):
        history = empirical_bars()
        reference = float(history[-1].close)
        probe = candidate("AAA.WA", 80.0)
        probe["reference_price"] = reference
        probe["stop"] = reference * 0.97

        model = ev.estimate(probe, history, ev_config())

        self.assertEqual(model["status"], "ready")
        self.assertEqual(model["engine"], "gpw-empirical-ev-v1")
        self.assertIn(model["selected_reward_risk"], [1.5, 1.75, 2.0, 2.25, 2.5])
        self.assertNotEqual(model["selected_reward_risk"], 1.8)
        probability_sum = (
            model["tp_before_sl_probability"]
            + model["sl_before_tp_probability"]
            + model["time_exit_probability"]
        )
        self.assertAlmostEqual(probability_sum, 1.0, places=3)
        self.assertGreaterEqual(model["effective_sample_size"], 1.0)
        self.assertTrue(math.isfinite(model["expected_net_r"]))
        self.assertTrue(math.isfinite(model["conservative_ev_r"]))

        enriched = ev.apply_dynamic_target(probe, model)
        expected_target = reference + (reference - probe["stop"]) * model["selected_reward_risk"]
        self.assertAlmostEqual(enriched["target"], round(expected_target + 1e-10, 2), places=2)
        self.assertEqual(enriched["target_method"], "gpw-empirical-ev-v1")
        blended, weight = ev.blend_score(70.0, model, ev_config())
        self.assertGreater(weight, 0.0)
        self.assertLessEqual(weight, 0.2)
        self.assertTrue(math.isfinite(blended))

    def test_same_bar_tp_and_sl_is_resolved_stop_first(self):
        history = empirical_bars(60)
        anchor = 55
        entry = float(history[anchor + 1].open)
        risk_fraction = ev._risk_fraction_at(history, anchor)
        stop = entry * (1.0 - risk_fraction)
        target = entry * (1.0 + risk_fraction * 1.5)
        original = history[anchor + 1]
        history[anchor + 1] = gpw.Bar(
            original.day,
            original.open,
            max(original.high, target * 1.01),
            min(original.low, stop * 0.99),
            original.close,
            original.volume,
        )
        result = ev._simulate(
            history,
            anchor,
            rr=1.5,
            horizon=2,
            cost_fraction=0.0038,
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["outcome"], "stop")

    def test_screening_rejection_is_not_market_data_failure(self):
        now = datetime(2026, 8, 19, 9, 6, tzinfo=WARSAW)
        cfg = config()
        def payload(_now, _cfg, decision, reason):
            return {
                "schema_version": "x", "policy_version": "test", "date": _now.date().isoformat(),
                "generated_at": _now.isoformat(), "timezone": "Europe/Warsaw",
                "publication_cutoff": "09:10", "decision": decision, "reason": reason,
                "locked": False, "selection": None, "data_quality": {}, "methodology": {},
                "metrics": {}, "disclaimer": "x",
            }
        with (
            patch.object(gpw, "load_config", return_value=cfg),
            patch.object(gpw, "is_session_day", return_value=True),
            patch.object(gpw, "previous_session", return_value=date(2026, 8, 18)),
            patch.object(gpw, "all_history", return_value=[]),
            patch.object(gpw, "build_quant_candidate", return_value=None),
            patch.object(gpw, "common_payload", side_effect=payload),
        ):
            result = pipeline.generate(now=now, market_fetcher=lambda _symbol: bars())
        self.assertEqual(result["decision"], "BRAK_TRANSAKCJI")
        self.assertEqual(result["data_quality"]["complete_ratio"], 1.0)
        self.assertEqual(len(result["data_quality"]["screened_out"]), 2)

    def test_true_provider_failure_still_fails_closed(self):
        now = datetime(2026, 8, 19, 9, 6, tzinfo=WARSAW)
        cfg = config()
        def failure(_now, _cfg, _reason, stage):
            return {
                "schema_version": "x", "policy_version": "test", "date": _now.date().isoformat(),
                "generated_at": _now.isoformat(), "timezone": "Europe/Warsaw",
                "publication_cutoff": "09:10", "decision": "AWARIA_DANYCH", "reason": "x",
                "locked": False, "selection": None, "data_quality": {"status": "failed", "failed_stage": stage},
                "methodology": {}, "metrics": {}, "disclaimer": "x",
            }
        with (
            patch.object(gpw, "load_config", return_value=cfg),
            patch.object(gpw, "is_session_day", return_value=True),
            patch.object(gpw, "previous_session", return_value=date(2026, 8, 18)),
            patch.object(gpw, "all_history", return_value=[]),
            patch.object(gpw, "failure_payload", side_effect=failure),
        ):
            result = pipeline.generate(
                now=now,
                market_fetcher=lambda symbol: (_ for _ in ()).throw(gpw.PublicationError(f"down {symbol}")),
            )
        self.assertEqual(result["decision"], "AWARIA_DANYCH")
        self.assertEqual(result["data_quality"]["complete_ratio"], 0.0)
        self.assertEqual(len(result["data_quality"]["provider_failures"]), 2)


if __name__ == "__main__":
    unittest.main()
