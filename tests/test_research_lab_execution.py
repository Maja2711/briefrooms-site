from __future__ import annotations

import unittest

from scripts import investments_research_lab as lab


def row(week: str, ret: float, magnitude: float | None = None) -> dict:
    mag = abs(ret if magnitude is None else magnitude)
    tf = {}
    for name in lab.TIMEFRAMES:
        tf[name] = {
            "stack": 1,
            "fast": 1,
            "slow": 1,
            "price_all": 1,
            "support_hold_long": True,
            "resistance_hold_short": False,
            "reclaim_ma30_long": True,
            "reclaim_ma30_short": False,
            "cross_30_60_long": True,
            "cross_30_60_short": False,
        }
    return {
        "week": week,
        "entry": 1.0,
        "exit": 1.0 + ret / 100.0,
        "return_pct": ret,
        "tf": tf,
        "_mag": mag,
    }


class ResearchLabExecutionTests(unittest.TestCase):
    def policy(self) -> dict:
        return {
            "enabled": True,
            "search": {"max_candidates_per_cycle": 120},
            "costs": {"round_trip_cost_bps": 2.0},
            "target": {
                "minimum_total_trades": 60,
                "minimum_holdout_trades": 20,
                "minimum_walk_forward_folds": 3,
                "minimum_promotable_win_rate": 0.58,
                "minimum_mean_week_percent": 0.02,
                "minimum_profit_factor": 1.10,
                "maximum_drawdown_percent": 8.0,
                "minimum_prospective_shadow_trades": 12,
                "holdout_fraction": 0.20,
            },
            "governance": {"require_regime_stability": True},
        }

    def candidate(self) -> dict:
        spec = {
            "instrument_id": "eurusd",
            "kind": "standalone",
            "timeframe": "H1",
            "rule": "full_stack",
            "side": "long",
            "feature_model": "ma30_60_100_200",
            "execution_horizon": "monday_0800_to_friday_2200",
        }
        return {"candidate_id": lab.cid(spec), "spec": spec}

    def records(self, holdout_negative: bool = False) -> list[dict]:
        rows = []
        for i in range(100):
            # Alternate low/high volatility so the regime gate has two populated groups.
            ret = 0.18 if i % 2 == 0 else 0.42
            if holdout_negative and i >= 80:
                ret = -0.35 if i % 2 == 0 else -0.55
            rows.append(row(f"2026-W{i:03d}", ret))
        return rows

    def test_catalog_only_contains_executable_candidates(self) -> None:
        rows = lab.candidate_catalog(self.policy())
        self.assertEqual(len(rows), 74)
        self.assertEqual(len({x["candidate_id"] for x in rows}), 74)
        self.assertTrue(all(x["spec"]["kind"] in {"standalone", "combination"} for x in rows))

    def test_candidate_reaches_shadow_only_after_all_historical_gates(self) -> None:
        result = lab.evaluate_candidate(self.records(), self.candidate(), self.policy())
        self.assertEqual(result["status"], "approved_for_shadow")
        self.assertEqual(result["runtime_activation"], "shadow_only")
        self.assertEqual(result["runtime_adjustment_points"], 0.0)
        self.assertFalse(result["production_impact"])
        self.assertEqual(result["walk_forward"]["status"], "PASS")
        self.assertEqual(result["regime_stability"]["status"], "PASS")

    def test_frozen_holdout_can_reject_good_development(self) -> None:
        result = lab.evaluate_candidate(self.records(holdout_negative=True), self.candidate(), self.policy())
        self.assertEqual(result["status"], "rejected_holdout")
        self.assertIn("frozen_holdout_gate_failed", result["reasons"])

    def test_prospective_shadow_is_separate_from_historical_holdout(self) -> None:
        base = self.records()
        first = lab.evaluate_candidate(base, self.candidate(), self.policy())
        self.assertEqual(first["status"], "approved_for_shadow")
        start = first["shadow_start_week"]
        prior = {"shadow_start_week": start}
        # No later weeks means it remains collecting, never promotion-eligible.
        second = lab.evaluate_candidate(base, self.candidate(), self.policy(), prior=prior)
        self.assertEqual(second["status"], "shadow_collecting")
        self.assertEqual(second["shadow_metrics"]["count"], 0)

    def test_run_drains_queue_and_never_grants_production_authority(self) -> None:
        state, registry, report, changed = lab.run(self.policy(), self.records(), {})
        self.assertTrue(changed)
        self.assertTrue(report["execution_loop_closed"])
        self.assertEqual(state["queue_remaining"], 0)
        self.assertEqual(state["queue"], [])
        self.assertEqual(registry["authority"]["automatic_production_promotion"], False)
        self.assertEqual(registry["authority"]["production_impact"], False)
        self.assertTrue(all(x["runtime_adjustment_points"] == 0.0 for x in registry["candidates"]))


if __name__ == "__main__":
    unittest.main()
