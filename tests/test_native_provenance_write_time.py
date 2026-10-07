from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import provenance_contract as provenance
from belief_core import Evidence
import daily_eurusd_belief_decision as daily
import brace_company_entity_framework as brace
import stock_trading_v2_production_bridge as stock
import build_shadow_engines_public as shadow
import l3a_research_executor as l3a
import wes_decision_ledger as wes_ledger
import wes_v5_history_seal as wes_seal


NOW = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc)
NOW_Z = NOW.isoformat().replace("+00:00", "Z")


class NativeProvenanceWriteTimeTests(unittest.TestCase):
    def assert_native(self, payload, engine_id):
        self.assertIn("provenance", payload)
        envelope = provenance.verify_attached(payload)
        self.assertEqual(envelope["schema_version"], provenance.SCHEMA_VERSION)
        self.assertEqual(envelope["engine_id"], engine_id)
        self.assertTrue(envelope["domain_provenance"].get("native_write_time"))
        return envelope

    def test_belief_core_evidence_emits_native_provenance(self):
        row = Evidence(
            evidence_id="ev-native-1",
            belief_id="eurusd.trend.bullish",
            source="unit-test",
            source_ref="source:test",
            observed_at=NOW_Z,
            direction=1,
            strength=0.8,
            reliability=0.9,
            independence_cluster="test",
        ).to_dict()
        env = self.assert_native(row, "belief_core")
        self.assertEqual(env["artifact_id"], "ev-native-1")
        self.assertEqual(env["evidence_ids"], ["ev-native-1"])
        self.assertEqual(env["belief_ids"], ["eurusd.trend.bullish"])

    def test_l3a_attempt_is_written_with_native_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            state_dir = Path(td)
            record = {
                "schema_version": l3a.SCHEMA,
                "attempt_id": "l3attempt-native-1",
                "run_id": "run-1",
                "attempted_at": NOW_Z,
                "intent_id": "intent-1",
                "question_id": "question-1",
                "belief_id": "eurusd.trend.bullish",
                "route": "approved_market_evidence_research",
                "research_result_status": "NO_NEW_EVIDENCE",
                "evidence_ids": [],
                "forecast_id": None,
            }
            l3a._record_attempt(state_dir, record)
            saved = json.loads((state_dir / l3a.EXPERIENCE_FILE).read_text(encoding="utf-8"))
            stored = saved["records"][0]
            env = self.assert_native(stored, "l3a")
            self.assertEqual(env["artifact_id"], "l3attempt-native-1")
            self.assertEqual(record.get("provenance"), None)

    def test_daily_decision_emits_native_provenance_without_changing_decision_logic(self):
        result = daily.synthesize({}, observed_at=NOW)
        env = self.assert_native(result, "daily_eurusd")
        self.assertEqual(result["direction"], "FLAT")
        self.assertIn("belief_state_unavailable", result["reasons"])
        self.assertEqual(env["authority"], "decision")

    def test_brace_report_emits_native_provenance_and_keeps_zero_influence(self):
        report = brace.build_report(
            {"entities": {}, "activation_boundary_established_at": NOW_Z},
            {},
            portfolio={},
            analysis={},
            universe={},
            as_of=NOW,
        )
        env = self.assert_native(report, "brace")
        self.assertFalse(report["active_decision_influence"])
        self.assertEqual(env["authority"], "report")

    def test_stock_audit_emits_native_provenance_and_preserves_legacy_execution_provenance(self):
        action = {
            "market": "US",
            "action": "open",
            "position_id": "pos-native-1",
            "source_engine": "v2",
            "opened_at": NOW_Z,
            "entry_decision_at": NOW_Z,
            "execution_provenance": {
                "source": "stock_trading_v2_production_bridge",
                "quote_observed_at": "2026-10-07T19:59:00Z",
                "recorded_at": NOW_Z,
            },
        }
        out = stock._attach_audit_provenance(action, now_utc=NOW, phase="CANARY")
        env = self.assert_native(out, "stock_trading_v2")
        self.assertEqual(out["execution_provenance"], action["execution_provenance"])
        self.assertEqual(env["artifact_id"], "stock-trading-v2:open:pos-native-1")
        self.assertEqual(action.get("provenance"), None)

    def test_shadow_observatory_rows_emit_native_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".github" / "workflows").mkdir(parents=True)
            payload = shadow.build(root, {}, now=NOW)
            self.assertTrue(payload["engines"])
            for row in payload["engines"]:
                env = self.assert_native(row, f"shadow:{row['id']}")
                self.assertEqual(env["authority"], "shadow")
                self.assertFalse(env["domain_provenance"]["production_authority"])

    def test_wes_frozen_decision_provenance_does_not_enter_ledger_payload_hash(self):
        payload = {
            "decided_at": "2026-10-07T22:00:00+02:00",
            "entry_not_before": "2026-10-07T22:00:00+02:00",
            "decision": {
                "strategy_id": "daily_weekly_blend",
                "direction": "short",
                "raw_score": -72.0,
                "confidence": 0.73,
            },
            "fresh_signal": {"score": -72.0},
            "weekly_signal": {"score": -44.0},
            "macro_context": {"direction": "short"},
            "entry_price_plan": {"execution_mode": "limit_pullback", "target_price": 1.124},
            "authorization_basis": {"strategy_id": "daily_weekly_blend", "direction": "short"},
            "rule": "execute_only_when_frozen_entry_target_is_touched_after_decision",
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.json"
            pending = wes_ledger.append_frozen_decision(
                payload,
                week_id="2026-W41",
                instrument_id="eurusd",
                path=path,
            )
            env = self.assert_native(pending, "wes")
            self.assertEqual(env["artifact_id"], pending["decision_id"])
            self.assertTrue(wes_ledger.assert_pending_integrity(pending))
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("provenance", stored["records"][0]["payload"])
            self.assertEqual(
                stored["records"][0]["payload_hash"],
                wes_ledger.sha256(stored["records"][0]["payload"]),
            )

    def test_wes_new_sealed_artifacts_emit_native_provenance_without_breaking_seal(self):
        week = {
            "week_id": "2026-W41",
            "method_version": "5.9.1-experimental",
            "base_method_version": "2.0.0",
            "forecast_created_at": "2026-10-04T00:24:03+02:00",
            "forecast_locked_at": "2026-10-04T00:24:03+02:00",
            "forecast_for_week_start": "2026-10-05",
            "forecast_for_week_end": "2026-10-09",
            "timezone": "Europe/Warsaw",
            "forecast_hash": "legacy-hash",
            "market_window": {},
            "instruments": [{
                "instrument_id": "eurusd",
                "symbol": "EURUSD=X",
                "direction": "short",
                "score": -70,
                "signals": {"last_close": 1.12},
                "position_legs": [],
            }],
        }
        leg = {
            "leg_id": "leg-native-1",
            "instrument_id": "eurusd",
            "direction": "short",
            "entry_price": 1.12,
            "entry_captured_at": "2026-10-05T08:05:00+02:00",
            "exit_price": 1.10,
            "exit_captured_at": "2026-10-07T12:00:00+02:00",
            "exit_reason": "take_profit",
            "gross_result_percent": 1.785714,
            "estimated_round_trip_cost_percent": 0.01,
            "net_result_percent": 1.775714,
        }
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "manifest.json"
            wes_seal.seal_forecast(week, manifest_path=manifest, sealed_at=NOW_Z)
            self.assert_native(week["frozen_forecast"], "wes")

            wes_seal.seal_closed_leg("2026-W41", leg, manifest_path=manifest, sealed_at=NOW_Z)
            self.assert_native(leg["settlement"], "wes")
            self.assert_native(leg, "wes")

            week["instruments"][0]["position_legs"] = [leg]
            self.assertEqual([], wes_seal.week_seal_violations(week, wes_seal.verify_manifest(manifest)))


if __name__ == "__main__":
    unittest.main()
