from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import belief_geopolitical_live as live

UTC = timezone.utc
NOW = datetime(2026, 8, 19, 16, 0, tzinfo=UTC)


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")


def prepare_gse(root: Path) -> None:
    write_json(root / "gse_state.json", {
        "schema_version": 1,
        "mode": "shadow",
        "controls": {
            "trade_execution_enabled": False,
            "policy_output_enabled": False,
            "automatic_tuning_enabled": False,
            "decision_engine_connected": False,
        },
    })
    metric = {
        "count": 35,
        "status": "measuring",
        "mean_brier": 0.17,
        "mean_log_loss": 0.5,
        "accuracy": 0.63,
        "mean_predicted": 0.61,
        "bias": 0.03,
    }
    write_json(root / "gse_calibration.json", {
        "mode": "shadow",
        "overall": metric,
        "by_asset": {"SPX": metric},
        "by_horizon_hours": {"24": metric},
        "automatic_tuning_enabled": False,
    })
    write_jsonl(root / "gse_forecasts.jsonl", [{
        "forecast_id": "gf1",
        "batch_id": "gb1",
        "asset": "SPX",
        "symbol": "SPY",
        "forecast_at": "2026-08-19T12:00:00Z",
        "target_at": "2026-08-20T12:00:00Z",
        "horizon_hours": 24,
        "direction": -1,
        "predicted_probability": 0.68,
        "confidence": 0.64,
        "impact_magnitude": "medium",
        "baseline_value": 100.0,
        "scenario_snapshot": [{
            "scenario_id": "gs1",
            "scenario_type": "middle_east_energy_escalation",
            "probability": 0.6,
            "confidence": 0.7,
            "evidence_ids": ["geo1"],
        }],
        "evidence_snapshot": [{"evidence_id": "geo1"}],
        "mode": "shadow",
    }])


class GeopoliticalLiveIntegrationTests(unittest.TestCase):
    def test_qualified_frozen_gse_evidence_is_persisted_shadow_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            belief = Path(tmp) / "belief"
            gse = Path(tmp) / "gse"
            prepare_gse(gse)
            status = live.run_cycle(belief, gse, NOW)
            self.assertEqual(status["evidence_ingested"], 1)
            self.assertFalse(status["decision_engine_connected"])
            self.assertFalse(status["trade_execution_enabled"])
            state = json.loads((belief / "state.json").read_text())
            evidence = state["evidence"]
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0]["belief_id"], "spx.trend.bullish")
            self.assertEqual(evidence[0]["source"], "BriefRooms GSE")
            self.assertEqual(evidence[0]["source_type"], "derived")
            self.assertTrue(evidence[0]["derived_from"])

    def test_retry_is_observation_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            belief = Path(tmp) / "belief"
            gse = Path(tmp) / "gse"
            prepare_gse(gse)
            first = live.run_cycle(belief, gse, NOW)
            second = live.run_cycle(belief, gse, NOW)
            self.assertEqual(first["observations_written"], 1)
            self.assertEqual(second["observations_written"], 0)
            rows = [x for x in (belief / "observations.jsonl").read_text().splitlines() if x.strip()]
            self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
