from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from scripts.gse_home_ranking import rank_home_forecasts

NOW = "2026-10-22T18:00:00Z"

def market(asset, p, at="2026-10-22T17:00:00Z", target="2026-11-21T17:00:00Z", horizon=720, direction=1, n=35):
    return dict(asset=asset, candidate_id="PRIVATE-ID", forecast_at=at, target_at=target,
        horizon_hours=horizon, direction=direction, v2_regime_candidate_probability=p,
        epistemic_confidence=.83, effective_cluster_n=n,
        scenario_diagnostics=[{"neighbours":[{"event_id":"PRIVATE-EVIDENCE"}]}])

def write_lines(root, file, rows):
    (root / file).write_text("".join(json.dumps(x)+"\n" for x in rows), encoding="utf-8")

class GSEHomeRankingTests(unittest.TestCase):
    def test_ranks_all_assets_and_deduplicates_horizons(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            write_lines(root, "gse_v2_regime_forecasts.jsonl", [
                market("GOLD", .84, at="2026-10-20T06:00:00Z"),
                market("GOLD", .72), market("BRENT", .74),
                market("SPX", .79, direction=-1),
                market("SPX", .77, horizon=168),
                market("WHEAT", .69)])
            out=rank_home_forecasts(root, generated_at=NOW)
            self.assertEqual(out["selected"]["asset"], "SPX")
            self.assertEqual([x["asset"] for x in out["runners_up"]], ["BRENT", "GOLD"])
            self.assertIn("spadnie", out["selected"]["question_pl"])
            self.assertEqual(out["eligible_subjects"],4)
            self.assertFalse(out["decision_influence"])
            self.assertNotIn("PRIVATE", json.dumps(out))

    def test_latest_candidate_beats_older_higher_probability(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            write_lines(root,"gse_v2_regime_forecasts.jsonl",[
                market("GOLD",.84,at="2026-10-22T16:00:00Z"),
                market("GOLD",.62),market("BRENT",.71)])
            self.assertEqual(rank_home_forecasts(root,generated_at=NOW)["selected"]["asset"],"BRENT")

    def test_expired_future_stale_low_evidence_and_nan_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            write_lines(root,"gse_v2_regime_forecasts.jsonl",[
                market("GOLD",.84,at="2026-10-21T00:00:00Z"),
                market("BRENT",.81,target="2026-10-22T17:00:00Z"),
                market("SPX",.80,at="2026-10-23T00:00:00Z"),
                market("WHEAT",.79,n=1),market("USD",float("nan"))])
            self.assertIsNone(rank_home_forecasts(root,generated_at=NOW)["selected"])

    def test_uncalibrated_attack_seed_cannot_outrank_market(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            write_lines(root,"gse_v2_regime_forecasts.jsonl",[market("GOLD",.71)])
            (root/"gse_event_threat_state.json").write_text(json.dumps({"model_status":"prospective_uncalibrated_seed"}))
            (root/"gse_event_probability_calibration.json").write_text(json.dumps(
                {"overall":{"count":70,"positive_count":10,"mean_brier":.1,"mean_prior_brier":.2}}))
            write_lines(root,"gse_event_probability_forecasts.jsonl",[dict(
                event_type="russia_attack_poland",horizon_hours=720,predicted_probability=.95,
                confidence=.9,forecast_at="2026-10-22T17:00:00Z",target_at="2026-11-21T17:00:00Z")])
            self.assertEqual(rank_home_forecasts(root,generated_at=NOW)["selected"]["asset"],"GOLD")

    def test_sufficiently_calibrated_event_can_win(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/"gse_event_threat_state.json").write_text(json.dumps({"model_status":"prospective_calibrated"}))
            (root/"gse_event_probability_calibration.json").write_text(json.dumps(
                {"overall":{"count":80,"positive_count":8,"mean_brier":.04,"mean_prior_brier":.07}}))
            write_lines(root,"gse_event_probability_forecasts.jsonl",[dict(
                event_type="russia_attack_poland",horizon_hours=720,predicted_probability=.21,
                confidence=.7,forecast_at="2026-10-22T00:30:00Z",
                target_at="2026-11-21T00:30:00Z",supporting_evidence_ids=["PRIVATE"])])
            out=rank_home_forecasts(root,generated_at=NOW)
            self.assertEqual(out["selected"]["kind"],"event")
            self.assertNotIn("PRIVATE",json.dumps(out))

if __name__ == "__main__":
    unittest.main()
