import unittest

from scripts import daily_stock_timestamp_integrity as integrity


class StockTimestampIntegrityTest(unittest.TestCase):
    def test_us_uses_explicit_activation_snapshot_and_closed_at(self):
        payload = {
            "decision": "TRADE",
            "selection": {
                "entry_zone": [150.85, 152.21],
                "market_snapshot": {
                    "last": 151.30,
                    "observed_at": "2026-08-19T10:10:36-04:00",
                },
            },
            "outcome": {
                "status": "RESOLVED",
                "activated": True,
                "entry_price": 151.30,
                "exit_price": 156.51,
                "closed_at": "2026-08-25T15:59:59-04:00",
            },
        }
        self.assertTrue(integrity.enrich_payload(payload, "us"))
        self.assertEqual(payload["outcome"]["activated_at"], "2026-08-19T10:10:36-04:00")
        self.assertEqual(payload["outcome"]["exit_at"], "2026-08-25T15:59:59-04:00")

    def test_us_never_invents_entry_time_without_explicit_snapshot(self):
        payload = {
            "decision": "TRADE",
            "generated_at": "2026-08-19T10:10:25-04:00",
            "selection": {"entry_zone": [150.85, 152.21], "market_snapshot": {"last": 151.30}},
            "outcome": {"status": "RESOLVED", "activated": True, "entry_price": 151.30},
        }
        integrity.enrich_payload(payload, "us")
        self.assertNotIn("activated_at", payload["outcome"])

    def test_us_rejects_snapshot_outside_entry_zone(self):
        payload = {
            "decision": "TRADE",
            "selection": {
                "entry_zone": [150.85, 152.21],
                "market_snapshot": {"last": 153.00, "observed_at": "2026-08-19T10:10:36-04:00"},
            },
            "outcome": {"status": "RESOLVED", "activated": True, "entry_price": 151.30},
        }
        integrity.enrich_payload(payload, "us")
        self.assertNotIn("activated_at", payload["outcome"])

    def test_gpw_copies_only_explicit_exit_bar_timestamp(self):
        payload = {
            "decision": "TRANSAKCJA",
            "selection": {"symbol": "PKO.WA"},
            "outcome": {
                "status": "RESOLVED",
                "activated": True,
                "activated_at": "2026-08-19T11:39:32+02:00",
                "entry_price": 109.46,
                "exit_price": 114.57,
                "exit_reason": "target",
                "exit_bar_at": "2026-08-19T14:30:00+02:00",
            },
        }
        self.assertTrue(integrity.enrich_payload(payload, "gpw"))
        self.assertEqual(payload["outcome"]["exit_at"], "2026-08-19T14:30:00+02:00")


if __name__ == "__main__":
    unittest.main()
