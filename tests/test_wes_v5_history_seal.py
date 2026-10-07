import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts import wes_v5_history_seal as seal


class WesV5HistorySealTests(unittest.TestCase):
    def manifest_path(self, td):
        path = Path(td) / "manifest.json"
        path.write_text(json.dumps({
            "schema_version": seal.MANIFEST_SCHEMA,
            "records": [],
            "head_record_hash": None,
        }), encoding="utf-8")
        return path

    def week(self):
        return {
            "week_id": "2026-W41",
            "method_version": "5.9.1-experimental",
            "base_method_version": "2.0.0",
            "forecast_created_at": "2026-10-04T00:24:03+02:00",
            "forecast_locked_at": "2026-10-04T00:24:03+02:00",
            "forecast_for_week_start": "2026-10-05",
            "forecast_for_week_end": "2026-10-09",
            "timezone": "Europe/Warsaw",
            "forecast_hash": "legacy-hash",
            "market_window": {
                "entry_target_local": "2026-10-05T08:00:00+02:00",
                "exit_target_local": "2026-10-09T22:00:00+02:00",
            },
            "instruments": [{
                "instrument_id": "eurusd",
                "symbol": "EURUSD=X",
                "direction": "short",
                "score": -70,
                "signals": {"last_close": 1.12},
                "risk_distance": {"stop_price_distance": 0.01},
                "position_legs": [],
            }],
        }

    def leg(self):
        return {
            "leg_id": "leg-1",
            "instrument_id": "eurusd",
            "symbol": "EURUSD=X",
            "direction": "short",
            "strategy_id": "weekly_trend",
            "entry_price": 1.12,
            "entry_captured_at": "2026-10-05T08:05:00+02:00",
            "entry_source": "test",
            "exit_price": 1.10,
            "exit_captured_at": "2026-10-07T12:00:00+02:00",
            "exit_source": "test",
            "exit_reason": "take_profit",
            "gross_result_percent": 1.785714,
            "estimated_round_trip_cost_percent": 0.01,
            "net_result_percent": 1.775714,
            "risk_plan": {"stop_loss_price": 1.13, "take_profit_price": 1.10},
            "candidate_outcomes": {"weekly_trend": {"net_result_percent": 1.775714}},
            "archived_at": "2026-10-07T12:01:00+02:00",
        }

    def test_forecast_seal_is_idempotent_and_detects_snapshot_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = self.manifest_path(td)
            week = self.week()
            first = seal.seal_forecast(week, manifest_path=manifest)
            second = seal.seal_forecast(week, manifest_path=manifest)
            self.assertEqual(first["payload_hash"], second["payload_hash"])
            self.assertEqual([], seal.week_seal_violations(week, seal.verify_manifest(manifest)))

            week["frozen_forecast"]["instruments"][0]["score"] = 99
            codes = {row["error"] for row in seal.week_seal_violations(week, seal.verify_manifest(manifest))}
            self.assertIn("sealed_payload_hash_mismatch", codes)

    def test_closed_leg_and_settlement_are_independently_sealed(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = self.manifest_path(td)
            week = self.week()
            seal.seal_forecast(week, manifest_path=manifest)
            leg = self.leg()
            seal.seal_closed_leg("2026-W41", leg, manifest_path=manifest)
            week["instruments"][0]["position_legs"] = [leg]
            stored = seal.verify_manifest(manifest)
            self.assertEqual(3, len(stored["records"]))
            self.assertEqual([], seal.week_seal_violations(week, stored))

            tampered = copy.deepcopy(week)
            tampered["instruments"][0]["position_legs"][0]["net_result_percent"] = 9.0
            codes = {row["error"] for row in seal.week_seal_violations(tampered, stored)}
            self.assertIn("settlement_payload_differs_from_leg", codes)
            self.assertIn("sealed_payload_hash_mismatch", codes)

    def test_reseal_changed_artifact_is_forbidden(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = self.manifest_path(td)
            leg = self.leg()
            seal.seal_closed_leg("2026-W41", leg, manifest_path=manifest)
            changed = copy.deepcopy(leg)
            changed.pop("position_leg_seal", None)
            changed.pop("settlement_seal", None)
            changed.pop("settlement", None)
            changed["candidate_outcomes"]["weekly_trend"]["net_result_percent"] = -5.0
            with self.assertRaises(seal.WesHistorySealError):
                seal.seal_closed_leg("2026-W41", changed, manifest_path=manifest)

    def test_manifest_chain_detects_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = self.manifest_path(td)
            week = self.week()
            seal.seal_forecast(week, manifest_path=manifest)
            leg = self.leg()
            seal.seal_closed_leg("2026-W41", leg, manifest_path=manifest)
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            raw["records"][0]["payload_hash"] = "0" * 64
            manifest.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(seal.WesHistorySealError):
                seal.verify_manifest(manifest)

    def test_migration_metadata_does_not_change_economic_payload(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = self.manifest_path(td)
            week = self.week()
            week["instruments"][0]["position_legs"] = [self.leg()]
            before = seal.sha256(seal.economic_payload(week))
            seal.seal_forecast(week, manifest_path=manifest)
            seal.seal_closed_leg("2026-W41", week["instruments"][0]["position_legs"][0], manifest_path=manifest)
            after = seal.sha256(seal.economic_payload(week))
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
