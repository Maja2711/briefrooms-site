from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import gse_v2_fast_cycle as fast

UTC = timezone.utc


class GSEV2FastCycleTests(unittest.TestCase):
    def _fixture(self, root: Path) -> Path:
        catalog = root / "catalog.json"
        catalog_payload = {
            "schema_version": "catalog-v1",
            "catalog_version": "1",
            "events": [{"event_id": "e1", "event_at": "2024-01-01T00:00:00Z"}],
        }
        catalog.write_text(json.dumps(catalog_payload), encoding="utf-8")
        catalog_sha = fast.loop.canonical_sha256(fast.loop._catalog_projection(catalog_payload))
        (root / "gse_v2_enriched_library.json").write_text(
            json.dumps({
                "schema_version": fast.loop.ENRICHED_LIBRARY_VERSION,
                "built_at": "2026-08-25T00:00:00Z",
                "catalog_sha256": catalog_sha,
                "responses": [{"event_id": "e1"}],
            }),
            encoding="utf-8",
        )
        (root / "gse_v2_historical_walkforward.json").write_text(
            json.dumps({
                "evaluable_predictions": 40,
                "overall": {
                    "regime_aware": {"n": 40, "brier": 0.22},
                    "unweighted_analogue": {"n": 40, "brier": 0.27},
                    "delta_brier_regime_minus_unweighted": -0.05,
                },
            }),
            encoding="utf-8",
        )
        (root / "gse_v2_policy_proposal.json").write_text(
            json.dumps({
                "schema_version": fast.loop.POLICY_PROPOSAL_VERSION,
                "status": "measuring",
                "automatically_applied": False,
                "active_policy_unchanged": True,
                "candidate": None,
            }),
            encoding="utf-8",
        )
        return catalog

    def test_fast_cycle_appends_ledger_even_without_new_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = self._fixture(root)
            now = datetime(2026, 8, 26, 10, 17, tzinfo=UTC)
            with patch.object(fast.loop, "generate_candidates", return_value=0), patch.object(
                fast.loop, "verify_candidates", return_value=0
            ):
                state = fast.run_fast_cycle(root, catalog, now=now, market=object())
            rows = fast.loop.read_jsonl(root / "gse_v2_learning_ledger.jsonl")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["recorded_at"], "2026-08-26T10:17:00Z")
            self.assertEqual(state["cycle_mode"], "fast_prospective")
            self.assertEqual(state["last_cycle"]["candidates_added"], 0)
            self.assertEqual(state["last_cycle"]["verifications_added"], 0)
            self.assertFalse(state["controls"]["automatic_tuning_enabled"])
            self.assertFalse(state["controls"]["decision_engine_connected"])

    def test_fast_cycle_updates_prospective_calibration_and_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = self._fixture(root)
            with patch.object(fast.loop, "generate_candidates", return_value=3), patch.object(
                fast.loop, "verify_candidates", return_value=2
            ), patch.object(
                fast.loop,
                "build_calibration",
                return_value={
                    "overall": {
                        "paired_n": 35,
                        "delta_brier_v2_minus_v1": -0.01,
                        "delta_log_loss_v2_minus_v1": -0.01,
                        "calibration_bias_v2_regime": 0.05,
                    }
                },
            ):
                state = fast.run_fast_cycle(root, catalog, now=datetime(2026, 8, 26, 11, 17, tzinfo=UTC), market=object())
            self.assertEqual(state["last_cycle"]["candidates_added"], 3)
            self.assertEqual(state["last_cycle"]["verifications_added"], 2)
            self.assertEqual(state["readiness"]["status"], "eligible_for_human_promotion_review")
            self.assertFalse(state["readiness"]["automatic_promotion"])

    def test_catalog_change_marks_deep_research_refresh_without_blocking_hourly_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = self._fixture(root)
            payload = json.loads(catalog.read_text(encoding="utf-8"))
            payload["events"].append({"event_id": "e2", "event_at": "2025-01-01T00:00:00Z"})
            catalog.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(fast.loop, "generate_candidates", return_value=0), patch.object(
                fast.loop, "verify_candidates", return_value=0
            ):
                state = fast.run_fast_cycle(root, catalog, now=datetime(2026, 8, 26, 12, 17, tzinfo=UTC), market=object())
            self.assertTrue(state["historical"]["refresh_required"])
            self.assertTrue(state["last_cycle"]["historical_refresh_required"])
            self.assertTrue((root / "gse_v2_learning_ledger.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
