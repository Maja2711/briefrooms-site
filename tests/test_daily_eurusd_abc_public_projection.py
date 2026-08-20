from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.daily_eurusd_abc_public_projection import (
    DISALLOWED_PUBLIC_KEYS,
    HORIZONS,
    build_public_projection,
    validate_public_projection,
    _walk_keys,
)

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]


def private_state() -> dict:
    horizons = {}
    for key, minutes in (("30m", 30), ("60m", 60), ("120m", 120), ("240m", 240), ("1440m", 1440)):
        outcome = None
        if key == "30m":
            outcome = {
                "resolved_at": "2026-08-20T19:30:00Z",
                "price": 1.168,
                "raw_return_bps": 3.0,
                "arms": {
                    "A": {"available": True, "direction": "LONG", "directional_correct": True, "signed_return_bps": 3.0},
                    "B": {"available": True, "direction": "FLAT", "directional_correct": None, "signed_return_bps": 0.0},
                    "C": {"available": True, "direction": "LONG", "directional_correct": True, "signed_return_bps": 3.0},
                },
            }
        horizons[key] = {"minutes": minutes, "target_at": "2026-08-20T20:00:00Z", "outcome": outcome}
    return {
        "mode": "research_shadow",
        "updated_at": "2026-08-20T19:30:00Z",
        "captures": [{
            "engine_version": "eurusd-daily-abc-v1.2.0",
            "market_observed_at": "2026-08-20T19:00:00Z",
            "captured_at": "2026-08-20T19:01:12Z",
            "reference_price": 1.16765,
            "decision_sha256": "private-hash",
            "research_boundary": {"decision_influence": False, "trade_execution": False, "belief_writeback": False},
            "arms": {
                "A": {"available": True, "direction": "LONG", "score": 64.2, "confidence": .284, "technical": {"secret": True}},
                "B": {"available": True, "direction": "FLAT", "score": 54.1, "confidence": 0.0, "belief": {"beliefs": [{"secret": True}]}},
                "C": {"available": True, "direction": "LONG", "score": 61.3, "confidence": .226, "belief_context": {"secret": True}},
            },
            "horizons": horizons,
        }],
    }


def private_report() -> dict:
    metric = {
        "matured_captures": 4,
        "available_captures": 4,
        "signals": 3,
        "decision_rate": .75,
        "hit_rate": 2 / 3,
        "mean_signed_return_bps_signal_only": 2.5,
        "mean_strategy_return_bps_all_available": 1.875,
    }
    return {
        "mode": "research_shadow",
        "decision_influence": False,
        "engine_version": "eurusd-daily-abc-v1.2.0",
        "governance": {"active_daily_engine_influence": False},
        "performance": {arm: {key: dict(metric) for key in HORIZONS} for arm in ("A", "B", "C")},
    }


class DailyEURUSDABCPublicProjectionTests(unittest.TestCase):
    def test_projection_exposes_only_sanitized_frontend_contract(self):
        payload = build_public_projection(
            private_state(),
            private_report(),
            now=datetime(2026, 8, 20, 19, 31, tzinfo=UTC),
        )
        validate_public_projection(payload)
        self.assertEqual(payload["language"], "pl")
        self.assertEqual(payload["mode"], "LIVE_SHADOW")
        self.assertEqual(payload["latest"]["signal_generated_at"], "2026-08-20T19:01:12Z")
        self.assertEqual(payload["sample"]["latest_signal_generated_at"], "2026-08-20T19:01:12Z")
        self.assertEqual(set(payload["latest"]["arms"]), {"A", "B", "C"})
        self.assertEqual(tuple(payload["latest"]["horizons"]), HORIZONS)
        self.assertEqual(payload["latest"]["horizons"]["30m"]["status"], "RESOLVED")
        self.assertEqual(payload["latest"]["horizons"]["60m"]["status"], "PENDING")
        self.assertFalse(DISALLOWED_PUBLIC_KEYS.intersection(_walk_keys(payload)))

    def test_projection_generation_time_is_stable_when_private_state_did_not_change(self):
        payload = build_public_projection(private_state(), private_report())
        self.assertEqual(payload["generated_at"], "2026-08-20T19:30:00Z")

    def test_pl_frontend_only_contract(self):
        pl = (ROOT / "pl/inwestycje/daily-trading.html").read_text(encoding="utf-8")
        en = (ROOT / "en/investing/daily-trading.html").read_text(encoding="utf-8")
        js = (ROOT / "scripts/daily-eurusd-abc-lab-pl.js").read_text(encoding="utf-8")
        self.assertIn('id="eurusd-abc-lab-pl-root"', pl)
        self.assertIn('/scripts/daily-eurusd-abc-lab-pl.js?v=2', pl)
        self.assertNotIn("eurusd-abc-lab-pl-root", en)
        self.assertNotIn("daily-eurusd-abc-lab-pl.js", en)
        self.assertIn('/data/investments/eurusd_abc_public_pl.json', js)
        self.assertIn("Sygnał wygenerowany", js)
        self.assertIn("SYGNAŁ", js)
        self.assertIn("Narastające porównanie", js)
        self.assertIn("30m", js)
        self.assertIn("1440m", js)


if __name__ == "__main__":
    unittest.main()
