import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import audit_weekly_model as audit
from scripts import wes_v5_history_seal as seal


class WesV5AuditSealTests(unittest.TestCase):
    def _week(self):
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
                "entry_latest_local": "2026-10-05T10:00:00+02:00",
                "exit_target_local": "2026-10-09T22:00:00+02:00",
            },
            "instruments": [{
                "instrument_id": "eurusd",
                "symbol": "EURUSD=X",
                "direction": "neutral",
                "score": 0,
                "signals": {"last_close": 1.12},
                "risk_distance": {"stop_price_distance": 0.01},
                "entry_price": None,
                "exit_price": None,
                "trade_status": "no_trade",
                "result": "no_trade",
                "result_value": 0.0,
                "result_percent": 0.0,
                "position_legs": [{
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
                    "archived_at": "2026-10-07T12:01:00+02:00",
                }],
            }],
        }

    def _empty_manifest(self, path):
        path.write_text(json.dumps({
            "schema_version": seal.MANIFEST_SCHEMA,
            "records": [],
            "head_record_hash": None,
        }), encoding="utf-8")

    def test_audit_blocks_historical_leg_drift_after_seal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            week_path = root / "2026-W41.json"
            manifest_path = root / "wes_v5_manifest.json"
            closed_manifest = root / "closed_week_manifest.json"
            report_path = root / "model_audit.json"
            quarantine_path = root / "public_quarantine.json"
            self._empty_manifest(manifest_path)
            closed_manifest.write_text(json.dumps({"sealed": {}}), encoding="utf-8")
            quarantine_path.write_text(json.dumps({}), encoding="utf-8")

            week = self._week()
            seal.seal_forecast(week, manifest_path=manifest_path)
            seal.seal_closed_leg("2026-W41", week["instruments"][0]["position_legs"][0], manifest_path=manifest_path)
            week_path.write_text(json.dumps(week), encoding="utf-8")

            with patch.object(audit, "WES_V5_MANIFEST", manifest_path),                  patch.object(audit, "MANIFEST", closed_manifest),                  patch.object(audit, "REPORT", report_path),                  patch.object(audit, "QUARANTINE", quarantine_path):
                passed = audit.audit_paths([week_path])
                self.assertEqual("passed", passed["status"])

                week["instruments"][0]["position_legs"][0]["net_result_percent"] = -9.0
                week_path.write_text(json.dumps(week), encoding="utf-8")
                failed = audit.audit_paths([week_path])

            self.assertEqual("failed", failed["status"])
            codes = {row.get("error") for row in failed["errors"]}
            self.assertIn("wes_v5_historical_payload_differs_from_seal", codes)
            seal_errors = {
                issue.get("error")
                for row in failed["errors"]
                for issue in row.get("violations", [])
            }
            self.assertIn("settlement_payload_differs_from_leg", seal_errors)
            self.assertIn("sealed_payload_hash_mismatch", seal_errors)


if __name__ == "__main__":
    unittest.main()
