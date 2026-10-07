from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from scripts import audit_provenance as audit
from scripts import provenance_contract as pc


PRE = "2026-10-07T21:30:00Z"
POST = "2026-10-07T21:40:00Z"


class GlobalProvenanceAuditTests(unittest.TestCase):
    def test_legacy_pre_activation_artifact_is_grandfathered(self):
        payload = {
            "schema_version": "daily-eurusd-belief-decision-v1",
            "decision_source": "NATIVE_BELIEF_FIRST",
            "direction": "FLAT",
            "used_beliefs": [],
        }
        records = [audit.GovernedArtifact("daily", "daily#legacy", payload, PRE)]
        result = audit.audit_records(records)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["grandfathered_legacy"], 1)

    def test_post_activation_missing_provenance_fails_closed(self):
        payload = {
            "schema_version": "daily-eurusd-belief-decision-v1",
            "decision_source": "NATIVE_BELIEF_FIRST",
            "direction": "FLAT",
            "used_beliefs": [],
        }
        records = [audit.GovernedArtifact("daily", "daily#new", payload, POST)]
        result = audit.audit_records(records)
        self.assertEqual(result["status"], "failed")
        self.assertIn("missing_native_provenance", {x["error"] for x in result["errors"]})

    def test_valid_daily_native_artifact_passes(self):
        raw = {
            "schema_version": "daily-eurusd-belief-decision-v1",
            "decision_source": "NATIVE_BELIEF_FIRST",
            "epistemic_source": "BELIEF_CORE_CF07",
            "direction": "FLAT",
            "used_beliefs": [{
                "belief_id": "eurusd.trend.bullish",
                "observed_at": "2026-10-07T21:39:00Z",
            }],
        }
        artifact_id = "daily:test:1"
        payload = pc.attach_native(
            raw,
            artifact_id=artifact_id,
            artifact_type="decision",
            engine_id="daily_eurusd",
            engine_version="belief-first-v1.9",
            created_at=POST,
            authority="decision",
            belief_ids=["eurusd.trend.bullish"],
            decision_id=artifact_id,
            domain_provenance={"native_write_time": True},
        )
        result = audit.audit_records([audit.GovernedArtifact("daily", "daily#ok", payload, POST)])
        self.assertEqual(result["status"], "passed")

    def test_payload_tamper_after_provenance_fails(self):
        raw = {"action": "cash", "market": "US", "reason": "test"}
        payload = pc.attach_native(
            raw,
            artifact_id="stock:test:cash",
            artifact_type="stock_cash",
            engine_id="stock_trading_v2",
            engine_version="v2",
            created_at=POST,
            authority="decision",
            decision_id="stock:test:cash",
            domain_provenance={"native_write_time": True},
        )
        payload["reason"] = "tampered"
        result = audit.audit_records([audit.GovernedArtifact("stock", "stock#tamper", payload, POST)])
        self.assertEqual(result["status"], "failed")
        self.assertIn("invalid_provenance_envelope", {x["error"] for x in result["errors"]})

    def test_wrong_authority_fails(self):
        raw = {"action": "open", "market": "US", "position_id": "p1"}
        payload = pc.attach_native(
            raw,
            artifact_id="stock:open:p1",
            artifact_type="stock_open",
            engine_id="stock_trading_v2",
            engine_version="v2",
            created_at=POST,
            authority="decision",
            decision_id="p1",
            domain_provenance={"native_write_time": True},
        )
        result = audit.audit_records([audit.GovernedArtifact("stock", "stock#open", payload, POST)])
        self.assertEqual(result["status"], "failed")
        self.assertIn("authority_boundary_violation", {x["error"] for x in result["errors"]})

    def test_future_daily_evidence_fails(self):
        raw = {
            "schema_version": "daily-eurusd-belief-decision-v1",
            "direction": "LONG",
            "used_beliefs": [{
                "belief_id": "eurusd.trend.bullish",
                "observed_at": "2026-10-07T21:41:00Z",
            }],
        }
        payload = pc.attach_native(
            raw,
            artifact_id="daily:test:future",
            artifact_type="decision",
            engine_id="daily_eurusd",
            engine_version="v1.9",
            created_at=POST,
            authority="decision",
            decision_id="daily:test:future",
            domain_provenance={"native_write_time": True},
        )
        result = audit.audit_records([audit.GovernedArtifact("daily", "daily#future", payload, POST)])
        self.assertEqual(result["status"], "failed")
        self.assertIn("temporal_lineage_violation", {x["error"] for x in result["errors"]})

    def test_conflicting_duplicate_artifact_id_fails(self):
        a = pc.attach_native(
            {"action": "cash", "market": "US"},
            artifact_id="stock:duplicate",
            artifact_type="stock_cash",
            engine_id="stock_trading_v2",
            engine_version="v2",
            created_at=POST,
            authority="decision",
            decision_id="stock:duplicate",
            domain_provenance={"native_write_time": True},
        )
        b = pc.attach_native(
            {"action": "skip", "market": "GPW"},
            artifact_id="stock:duplicate",
            artifact_type="stock_skip",
            engine_id="stock_trading_v2",
            engine_version="v2",
            created_at=POST,
            authority="decision",
            decision_id="stock:duplicate",
            domain_provenance={"native_write_time": True},
        )
        result = audit.audit_records([
            audit.GovernedArtifact("stock", "stock#a", a, POST),
            audit.GovernedArtifact("stock", "stock#b", b, POST),
        ])
        self.assertEqual(result["status"], "failed")
        self.assertIn("artifact_id_payload_hash_collision", {x["error"] for x in result["errors"]})

    def test_local_parent_cycle_fails(self):
        a = pc.attach_native(
            {"action": "cash", "market": "US"},
            artifact_id="stock:a",
            artifact_type="stock_cash",
            engine_id="stock_trading_v2",
            engine_version="v2",
            created_at=POST,
            authority="decision",
            parent_artifact_ids=["stock:b"],
            decision_id="stock:a",
            domain_provenance={"native_write_time": True},
        )
        b = pc.attach_native(
            {"action": "cash", "market": "GPW"},
            artifact_id="stock:b",
            artifact_type="stock_cash",
            engine_id="stock_trading_v2",
            engine_version="v2",
            created_at=POST,
            authority="decision",
            parent_artifact_ids=["stock:a"],
            decision_id="stock:b",
            domain_provenance={"native_write_time": True},
        )
        result = audit.audit_records([
            audit.GovernedArtifact("stock", "stock#a", a, POST),
            audit.GovernedArtifact("stock", "stock#b", b, POST),
        ])
        self.assertEqual(result["status"], "failed")
        self.assertIn("provenance_parent_cycle", {x["error"] for x in result["errors"]})

    def test_post_activation_wes_sealed_forecast_without_provenance_fails(self):
        week = {
            "week_id": "2026-W42",
            "frozen_forecast": {"schema_version": "briefrooms-wes-v5-frozen-forecast-v1", "week_id": "2026-W42"},
            "frozen_forecast_seal": {"sealed_at": POST},
            "instruments": [],
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "2026-W42.json"
            path.write_text(__import__("json").dumps(week), encoding="utf-8")
            records = audit.collect_file(path, "wes")
        result = audit.audit_records(records)
        self.assertEqual(result["status"], "failed")
        self.assertIn("missing_native_provenance", {x["error"] for x in result["errors"]})


if __name__ == "__main__":
    unittest.main()
