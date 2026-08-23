from __future__ import annotations

import unittest
from datetime import datetime, timezone

from scripts.gse_publish_discovery_status import build_status

UTC = timezone.utc


class GseDiscoveryStatusTests(unittest.TestCase):
    def test_target_met_status_exposes_auditable_counts(self) -> None:
        state = {
            "mode": "shadow",
            "pipeline_version": "v1",
            "effective_verified_cluster_n": 137,
            "target_verified_cluster_n": 100,
            "target_met": True,
            "machine_verified_document_n": 264,
            "auto_cluster_n": 121,
            "quality_balance_gate": {
                "non_sanctions_cluster_n": 48,
                "scenario_families_with_at_least_5_clusters": 5,
                "gate_met": True,
            },
            "by_scenario": {"sanctions_escalation": 52, "middle_east_energy_escalation": 31},
            "by_source": {"OFAC Sanctions List Updates": 144},
            "controls": {"market_outcomes_used_for_event_selection": False},
        }
        status = build_status(
            state,
            discovery_exit_code=0,
            now=datetime(2026, 8, 23, 21, 0, tzinfo=UTC),
        )
        self.assertEqual("target_met", status["status"])
        self.assertEqual(137, status["verified_clusters"])
        self.assertEqual(100, status["target_verified_clusters"])
        self.assertTrue(status["target_met"])
        self.assertTrue(status["quality_balance_gate"]["gate_met"])
        self.assertIn("middle_east_energy_escalation", status["by_scenario"])
        self.assertIsNotNone(status["source_state_sha256"])

    def test_below_target_is_visible_not_silently_successful(self) -> None:
        status = build_status(
            {
                "effective_verified_cluster_n": 87,
                "target_verified_cluster_n": 100,
                "target_met": False,
            },
            discovery_exit_code=0,
        )
        self.assertEqual("below_target", status["status"])
        self.assertEqual(87, status["verified_clusters"])
        self.assertFalse(status["target_met"])

    def test_discovery_error_remains_observable_without_state(self) -> None:
        status = build_status({}, discovery_exit_code=2)
        self.assertEqual("discovery_error", status["status"])
        self.assertIsNone(status["verified_clusters"])
        self.assertEqual(2, status["discovery_exit_code"])
        self.assertFalse(status["target_met"])


if __name__ == "__main__":
    unittest.main()
