import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.learning_ledger import append_event
from scripts.timesfm_shadow_forecaster import (
    LEDGER_FILENAME,
    STATUS_FILENAME,
    ensure_activation,
)
from scripts.timesfm_shadow_public_projection import build_projection, validate_projection


class TimesFMShadowPublicProjectionTests(unittest.TestCase):
    def test_projection_is_sanitized_and_aggregates_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc)
            ensure_activation(root, now=now, bootstrap=True)
            ledger = root / LEDGER_FILENAME
            subject = "timesfm:eurusd:30m:test:h2"
            append_event(
                ledger,
                event_type="forecast",
                occurred_at="2026-08-25T18:00:00Z",
                subject_id=subject,
                source_ref=f"timesfm://forecast/{subject}",
                payload={
                    "model_id": "google/timesfm-2.5-200m-pytorch",
                    "origin_bar_at": "2026-08-25T17:30:00Z",
                    "origin_price": 1.1700,
                    "context_points": 256,
                    "horizon_steps": 2,
                    "horizon_label": "1h",
                    "forecast_price": 1.1720,
                    "predicted_return": 1.1720 / 1.1700 - 1.0,
                    "predicted_direction": "UP",
                    "quantiles": {"q10": 1.1680, "q50": 1.1715, "q90": 1.1750},
                    "shadow": True,
                    "decision_influence": False,
                },
            )
            append_event(
                ledger,
                event_type="outcome",
                occurred_at="2026-08-25T19:00:00Z",
                subject_id=subject,
                source_ref=f"timesfm://outcome/{subject}",
                payload={
                    "horizon_label": "1h",
                    "forecast_price": 1.1720,
                    "actual_price": 1.1710,
                    "actual_return": 1.1710 / 1.1700 - 1.0,
                    "absolute_error": 0.0010,
                    "direction_correct": True,
                    "interval_80_contains_actual": True,
                    "decision_influence": False,
                },
            )
            (root / STATUS_FILENAME).write_text(json.dumps({
                "updated_at": "2026-08-25T19:00:00Z",
                "ledger_count": 2,
            }), encoding="utf-8")

            payload = build_projection(root)
            validate_projection(payload)

            self.assertTrue(payload["experiment"]["research_only"])
            self.assertFalse(payload["experiment"]["decision_influence"])
            self.assertTrue(payload["latest"]["available"])
            self.assertEqual(payload["latest"]["horizons"]["1h"]["status"], "RESOLVED")
            self.assertTrue(payload["latest"]["horizons"]["1h"]["direction_correct"])
            self.assertEqual(payload["performance"]["1h"]["resolved"], 1)
            self.assertEqual(payload["performance"]["1h"]["direction_hit_rate"], 1.0)
            self.assertAlmostEqual(payload["performance"]["1h"]["mae_pips"], 10.0)
            self.assertNotIn("ledger_head_hash", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
