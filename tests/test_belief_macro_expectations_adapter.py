from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.belief_macro_expectations_adapter import MacroExpectationsAdapter

UTC = timezone.utc


class MacroExpectationsAdapterTests(unittest.TestCase):
    def test_sourced_bank_forecasts_create_auditable_distribution(self):
        now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
        payload = {
            "provider": "test-consensus-provider",
            "releases": [{
                "region": "US",
                "indicator": "nonfarm_payrolls_change",
                "period": "2026-09",
                "event_at": "2026-10-02T12:30:00Z",
                "unit": "thousands",
                "market_consensus": 80.0,
                "forecasts": [
                    {
                        "institution": "Bank A",
                        "forecast": 60.0,
                        "published_at": "2026-10-02T08:00:00Z",
                        "source_ref": "https://example.test/a",
                        "historical_mae": 35.0,
                        "accuracy_weight": 1.4,
                    },
                    {
                        "institution": "Bank B",
                        "forecast": 95.0,
                        "published_at": "2026-10-02T09:00:00Z",
                        "source_ref": "https://example.test/b",
                        "historical_mae": 45.0,
                        "accuracy_weight": 1.0,
                    },
                ],
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "expectations.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = MacroExpectationsAdapter(path).run(now)

        self.assertEqual(len(result.observations), 1)
        obs = result.observations[0]
        self.assertEqual(obs.metric, "macro_expectation_distribution")
        self.assertEqual(obs.value["forecast_count"], 2)
        self.assertEqual(obs.value["market_consensus"], 80.0)
        self.assertIsNotNone(obs.value["p_actual_below_market_consensus_proxy"])
        self.assertEqual(len(obs.metadata["forecasts"]), 2)

    def test_missing_or_unsourced_forecasts_fail_neutral(self):
        now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
        payload = {
            "provider": "test",
            "releases": [{
                "region": "US",
                "indicator": "nonfarm_payrolls_change",
                "period": "2026-09",
                "event_at": "2026-10-02T12:30:00Z",
                "forecasts": [
                    {"institution": "Bank A", "forecast": 60.0},
                ],
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "expectations.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = MacroExpectationsAdapter(path).run(now)

        self.assertEqual(result.observations, ())


if __name__ == "__main__":
    unittest.main()
