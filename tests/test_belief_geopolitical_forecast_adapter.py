from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import belief_geopolitical_forecast_adapter as adapter

UTC = timezone.utc
AS_OF = datetime(2026, 8, 19, 16, 0, tzinfo=UTC)


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")


def safe_state() -> dict:
    return {
        "schema_version": 1,
        "mode": "shadow",
        "controls": {
            "trade_execution_enabled": False,
            "policy_output_enabled": False,
            "automatic_tuning_enabled": False,
            "decision_engine_connected": False,
        },
    }


def calibration(n=30, brier=0.18, bias=0.04) -> dict:
    row = {
        "count": n,
        "status": "measuring" if n >= 30 else "insufficient_sample",
        "mean_brier": brier,
        "mean_log_loss": 0.55,
        "accuracy": 0.60,
        "mean_predicted": 0.64,
        "bias": bias,
    }
    return {
        "mode": "shadow",
        "overall": row,
        "by_asset": {"SPX": dict(row), "BRENT": dict(row)},
        "by_horizon_hours": {"24": dict(row), "168": dict(row)},
        "automatic_tuning_enabled": False,
    }


def forecast(
    fid="g1",
    *,
    asset="SPX",
    horizon=24,
    direction=-1,
    probability=0.70,
    forecast_at="2026-08-19T12:00:00Z",
    target_at="2026-08-20T12:00:00Z",
) -> dict:
    return {
        "forecast_id": fid,
        "batch_id": "batch-1",
        "asset": asset,
        "symbol": "SPY" if asset == "SPX" else "BZ=F",
        "forecast_at": forecast_at,
        "target_at": target_at,
        "horizon_hours": horizon,
        "direction": direction,
        "predicted_probability": probability,
        "confidence": 0.65,
        "impact_magnitude": "medium",
        "baseline_value": 100.0,
        "scenario_snapshot": [
            {
                "scenario_id": "s1",
                "scenario_type": "middle_east_energy_escalation",
                "probability": 0.6,
                "confidence": 0.7,
                "evidence_ids": ["ge1", "ge2"],
            }
        ],
        "evidence_snapshot": [{"evidence_id": "ge1"}, {"evidence_id": "ge2"}],
        "mode": "shadow",
    }


def prepare(root: Path, *, forecasts=None, cal=None, state=None, v2=None) -> None:
    write_json(root / "gse_state.json", state if state is not None else safe_state())
    write_json(root / "gse_calibration.json", cal if cal is not None else calibration())
    write_jsonl(root / "gse_forecasts.jsonl", forecasts if forecasts is not None else [forecast()])
    if v2 is not None:
        write_jsonl(root / "gse_v2_forecasts.jsonl", v2)


class GeopoliticalForecastAdapterTests(unittest.TestCase):
    def test_uncalibrated_gse_is_observation_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare(root, cal=calibration(n=8))
            result = adapter.GeopoliticalForecastAdapter().run(root, AS_OF)
            self.assertEqual(len(result.observations), 1)
            self.assertEqual(len(result.evidence), 0)
            q = result.observations[0].metadata["calibration_qualification"]
            self.assertFalse(q["evidence_eligible"])
            self.assertIn("asset_calibration_n_below_30", q["reasons"])

    def test_calibrated_spx_24h_becomes_modest_shadow_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare(root)
            result = adapter.GeopoliticalForecastAdapter().run(root, AS_OF)
            self.assertEqual(len(result.observations), 1)
            self.assertEqual(len(result.evidence), 1)
            evidence = result.evidence[0]
            self.assertEqual(evidence.belief_id, "spx.trend.bullish")
            self.assertEqual(evidence.direction, -1)
            self.assertLessEqual(evidence.strength, adapter.MAX_EVIDENCE_STRENGTH)
            self.assertEqual(evidence.evidence_type, "geopolitical_forecast")
            self.assertEqual(evidence.source_type, "derived")
            self.assertTrue(evidence.derived_from)
            self.assertFalse(evidence.metadata["decision_influence"])

    def test_v2_candidate_is_telemetry_only_and_cannot_replace_v1_probability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare(
                root,
                v2=[{
                    "candidate_id": "c1",
                    "baseline_forecast_id": "g1",
                    "v2_candidate_probability": 0.51,
                    "effective_analogue_n": 6,
                    "analogue_status": "exploratory",
                    "candidate_sha256": "abc",
                }],
            )
            result = adapter.GeopoliticalForecastAdapter().run(root, AS_OF)
            obs = result.observations[0]
            self.assertEqual(obs.value["probability"], 0.70)
            self.assertEqual(obs.metadata["forecast_variant_used_for_evidence"], "gse_v1_frozen")
            self.assertEqual(obs.metadata["v2_role"], "research_telemetry_only")
            self.assertEqual(obs.metadata["v2_candidate"]["v2_candidate_probability"], 0.51)
            self.assertTrue(result.evidence[0].metadata["gse_v2_not_used_for_evidence"])

    def test_future_or_expired_forecasts_never_enter_point_in_time_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare(root, forecasts=[
                forecast("future", forecast_at="2026-08-20T12:00:00Z", target_at="2026-08-21T12:00:00Z"),
                forecast("expired", forecast_at="2026-08-18T12:00:00Z", target_at="2026-08-19T15:59:00Z"),
            ])
            result = adapter.GeopoliticalForecastAdapter().run(root, AS_OF)
            self.assertEqual(len(result.observations), 0)
            self.assertEqual(len(result.evidence), 0)

    def test_non_spx_assets_remain_observations_until_atomic_beliefs_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare(root, forecasts=[forecast("brent", asset="BRENT", direction=1)])
            result = adapter.GeopoliticalForecastAdapter().run(root, AS_OF)
            self.assertEqual(len(result.observations), 1)
            self.assertEqual(result.observations[0].entity, "BRENT")
            self.assertEqual(len(result.evidence), 0)

    def test_unsafe_gse_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = safe_state()
            state["controls"]["automatic_tuning_enabled"] = True
            prepare(root, state=state)
            result = adapter.GeopoliticalForecastAdapter().run(root, AS_OF)
            self.assertEqual(len(result.evidence), 0)
            self.assertEqual(len(result.observations), 1)
            self.assertEqual(result.observations[0].status, "invalid")
            self.assertIn("gse_control_not_hard_off:automatic_tuning_enabled", result.observations[0].metadata["reasons"])

    def test_missing_gse_state_is_explicit_unavailable_not_fabricated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = adapter.GeopoliticalForecastAdapter().run(root, AS_OF)
            self.assertEqual(len(result.evidence), 0)
            self.assertEqual(result.observations[0].status, "unavailable")
            self.assertEqual(result.observations[0].value, "unavailable")


if __name__ == "__main__":
    unittest.main()
