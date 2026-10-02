from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from belief_macro_expectations_collector import collect, normalize_payload

UTC = timezone.utc


class MacroExpectationsCollectorTests(unittest.TestCase):
    def test_normalize_requires_sourced_institution_forecasts(self):
        now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
        payload = {
            "provider": "licensed-test-feed",
            "releases": [{
                "region": "US",
                "indicator": "nonfarm_payrolls_change",
                "period": "2026-09",
                "event_at": "2026-10-02T12:30:00Z",
                "unit": "thousands",
                "market_consensus": 90,
                "forecasts": [
                    {
                        "institution": "Bank A",
                        "forecast": 50,
                        "published_at": "2026-10-02T08:00:00Z",
                        "source_ref": "https://research.example.test/bank-a",
                        "historical_mae": 30,
                        "accuracy_weight": 99,
                    },
                    {
                        "institution": "Bank B",
                        "forecast": 110,
                        "published_at": "2026-10-02T09:00:00Z",
                        "source_ref": "https://research.example.test/bank-b",
                        "historical_mae": 45,
                    },
                ],
            }],
        }
        normalized = normalize_payload(payload, now=now)
        self.assertEqual(normalized["status"], "ready")
        release = normalized["releases"][0]
        self.assertEqual(release["market_consensus"], 90.0)
        self.assertEqual(len(release["forecasts"]), 2)
        self.assertNotIn("accuracy_weight", release["forecasts"][0])
        self.assertEqual(release["forecasts"][0]["historical_mae"], 30.0)

    def test_unsourced_rows_do_not_enter_distribution(self):
        now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
        payload = {
            "provider": "test",
            "releases": [{
                "region": "US",
                "indicator": "nonfarm_payrolls_change",
                "period": "2026-09",
                "event_at": "2026-10-02T12:30:00Z",
                "forecasts": [
                    {
                        "institution": "Bank A",
                        "forecast": 50,
                        "published_at": "2026-10-02T08:00:00Z",
                        "source_ref": "not-a-url",
                    },
                    {
                        "institution": "Bank B",
                        "forecast": 100,
                        "published_at": "2026-10-02T09:00:00Z",
                        "source_ref": "https://example.test/b",
                    },
                ],
            }],
        }
        normalized = normalize_payload(payload, now=now)
        self.assertEqual(normalized["status"], "no_valid_releases")
        self.assertEqual(normalized["releases"], [])

    def test_unconfigured_feed_fails_closed(self):
        now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
        payload = collect(now, url="")
        self.assertEqual(payload["status"], "unconfigured")
        self.assertEqual(payload["releases"], [])


if __name__ == "__main__":
    unittest.main()
