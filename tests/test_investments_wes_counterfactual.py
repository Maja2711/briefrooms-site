from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "investments_wes_counterfactual.py"
spec = importlib.util.spec_from_file_location("wes_counterfactual", MODULE_PATH)
cf = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(cf)


def policy() -> dict:
    return {
        "instruments": [
            {
                "instrument_id": "sp500_futures",
                "round_trip_cost": 1.0,
                "cost_unit": "points",
            }
        ]
    }


def week() -> dict:
    return {
        "week_id": "2026-W35",
        "market_window": {"exit_target_local": "2026-08-28T22:00:00+02:00"},
        "instruments": [
            {
                "instrument_id": "sp500_futures",
                "symbol": "ES=F",
                "risk_distance": {
                    "stop_price_distance": 10.0,
                    "take_price_distance": 20.0,
                },
            }
        ],
    }


def record(direction: str = "long", with_plan: bool = True) -> dict:
    risk_plan = None
    if with_plan:
        risk_plan = {
            "model_version": "2.0.0",
            "direction": direction,
            "stop_loss_price": 90.0 if direction == "long" else 110.0,
            "take_profit_price": 120.0 if direction == "long" else 80.0,
            "same_bar_rule": "stop_loss_first_conservative",
        }
    return {
        "decision_id": "d1",
        "week_id": "2026-W35",
        "instrument_id": "sp500_futures",
        "decision_type": "entered_position",
        "relationship": {
            "class": "STRONG_AGREEMENT",
            "alpha_eligible": True,
        },
        "v5_counterfactual_capture": {
            "eligible": True,
            "status": "frozen_point_in_time",
        },
        "v5_counterfactual": {
            "direction": direction,
            "strategy_id": "weekly_trend",
            "raw_score": 50.0,
            "entry_price": 100.0,
            "entry_captured_at": "2026-08-24T08:00:00+00:00",
            "risk_plan": risk_plan,
            "net_result_percent": None,
        },
        "wes_actual": {
            "direction": direction,
            "strategy_id": "weekly_trend",
            "entry_class": "monday_weekly",
        },
        "outcome": {
            "status": "wes_observed_v5_counterfactual_pending",
            "wes_net_result_percent": 5.0,
            "v5_counterfactual_net_result_percent": None,
            "incremental_wes_vs_v5_percent": None,
        },
        "active_decision_influence": False,
    }


def ledger(row: dict | None = None) -> dict:
    return {
        "schema_version": "wes-spx-brace-readonly-v1",
        "active_decision_influence": False,
        "governance": {"bounded_influence_enabled": False},
        "records": [row or record()],
    }


def contract(direction: str = "long") -> dict:
    r = record(direction=direction)
    result = cf.make_replay_contract(
        r,
        week(),
        policy(),
        frozen_at=datetime(2026, 8, 24, 8, 1, tzinfo=timezone.utc),
    )
    assert result["status"] == "frozen"
    return result["contract"]


def bars(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    index = pd.to_datetime([x[0] for x in rows], utc=True)
    return pd.DataFrame(
        {
            "Open": [x[1] for x in rows],
            "High": [x[2] for x in rows],
            "Low": [x[3] for x in rows],
            "Close": [x[4] for x in rows],
        },
        index=index,
    )


class WesCounterfactualTests(unittest.TestCase):
    def test_freeze_contract_is_self_contained_and_cost_is_frozen(self):
        result = cf.make_replay_contract(
            record(), week(), policy(),
            frozen_at=datetime(2026, 8, 24, 8, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(result["status"], "frozen")
        c = result["contract"]
        self.assertEqual(c["baseline_scope"], "frozen_pre_wes_v5_risk_plan")
        self.assertEqual(c["scheduled_exit"], "2026-08-28T22:00:00+02:00")
        self.assertAlmostEqual(c["round_trip_cost_percent"], 1.0, places=8)
        self.assertFalse(c["decision_influence"])
        self.assertTrue(c["contract_sha256"])

    def test_missing_pre_wes_plan_uses_only_frozen_risk_distance(self):
        result = cf.make_replay_contract(
            record(with_plan=False), week(), policy(),
            frozen_at=datetime(2026, 8, 24, 8, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(result["status"], "frozen")
        c = result["contract"]
        self.assertEqual(c["risk_plan_source"], "derived_point_in_time_from_frozen_risk_distance")
        self.assertEqual(c["risk_plan"]["stop_loss_price"], 90.0)
        self.assertEqual(c["risk_plan"]["take_profit_price"], 120.0)

    def test_no_prospective_baseline_is_never_reconstructed(self):
        r = record()
        r["v5_counterfactual"] = None
        r["v5_counterfactual_capture"] = {
            "eligible": False,
            "status": "missed_not_reconstructed",
        }
        frozen = cf.freeze_contracts(
            ledger(r), weeks={"2026-W35": week()}, policy=policy(),
            frozen_at=datetime(2026, 8, 24, 8, 2, tzinfo=timezone.utc),
        )
        self.assertIsNone(frozen["records"][0]["v5_counterfactual"])

    def test_long_take_profit_replay(self):
        data = bars([
            ("2026-08-24T08:05:00Z", 100, 105, 99, 104),
            ("2026-08-24T08:10:00Z", 104, 121, 103, 120),
        ])
        result = cf.replay_from_bars(
            contract("long"), data,
            evaluated_at=datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["exit_reason"], "take_profit")
        self.assertEqual(result["exit_price"], 120.0)
        self.assertAlmostEqual(result["net_result_percent"], 19.0, places=8)

    def test_same_bar_both_levels_uses_conservative_stop_first(self):
        data = bars([
            ("2026-08-24T08:05:00Z", 100, 121, 89, 110),
        ])
        result = cf.replay_from_bars(
            contract("long"), data,
            evaluated_at=datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result["exit_reason"], "stop_loss")
        self.assertEqual(result["execution_rule"], "same_bar_stop_first_conservative")
        self.assertAlmostEqual(result["net_result_percent"], -11.0, places=8)

    def test_short_stop_loss_replay(self):
        data = bars([
            ("2026-08-24T08:05:00Z", 100, 111, 98, 109),
        ])
        result = cf.replay_from_bars(
            contract("short"), data,
            evaluated_at=datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result["exit_reason"], "stop_loss")
        self.assertEqual(result["exit_price"], 110.0)
        self.assertAlmostEqual(result["net_result_percent"], -11.0, places=8)

    def test_pending_before_deadline_without_hit(self):
        data = bars([
            ("2026-08-24T08:05:00Z", 100, 105, 95, 102),
        ])
        result = cf.replay_from_bars(
            contract("long"), data,
            evaluated_at=datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result["status"], "pending")

    def test_scheduled_close_replay(self):
        data = bars([
            ("2026-08-24T08:05:00Z", 100, 105, 95, 102),
            ("2026-08-28T20:00:00Z", 106, 108, 104, 107),
        ])
        result = cf.replay_from_bars(
            contract("long"), data,
            evaluated_at=datetime(2026, 8, 28, 21, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["exit_reason"], "scheduled_week_close")
        self.assertEqual(result["exit_price"], 106.0)
        self.assertAlmostEqual(result["net_result_percent"], 5.0, places=8)

    def test_apply_evaluation_computes_incremental_alpha_once(self):
        r = record()
        c = contract("long")
        r["v5_counterfactual"]["replay_contract"] = c
        r["v5_counterfactual"]["replay_contract_status"] = "frozen"
        data = bars([
            ("2026-08-24T08:05:00Z", 100, 121, 99, 120),
        ])
        out = cf.apply_evaluations(
            ledger(r),
            evaluated_at=datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc),
            bars_by_decision={"d1": data},
        )
        row = out["records"][0]
        self.assertEqual(row["v5_counterfactual"]["net_result_percent"], 19.0)
        self.assertEqual(row["outcome"]["incremental_wes_vs_v5_percent"], -14.0)
        self.assertEqual(row["outcome"]["status"], "resolved_incremental_alpha")
        out2 = cf.apply_evaluations(
            out,
            evaluated_at=datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc),
            bars_by_decision={"d1": bars([])},
        )
        self.assertEqual(out2["records"][0]["outcome"]["incremental_wes_vs_v5_percent"], -14.0)

    def test_incremental_report_separates_overall_and_agreement_conflict(self):
        r1 = record()
        r1["outcome"]["incremental_wes_vs_v5_percent"] = 1.5
        r2 = deepcopy_record = record()
        r2 = {**r2, "decision_id": "d2"}
        r2["relationship"] = {"class": "UNAVAILABLE", "alpha_eligible": False}
        r2["outcome"]["incremental_wes_vs_v5_percent"] = -0.5
        report = cf.build_incremental_report({"records": [r1, r2]})
        self.assertEqual(report["overall"]["resolved_pairs"], 2)
        self.assertAlmostEqual(report["overall"]["mean_incremental_alpha_percent"], 0.5)
        self.assertEqual(report["agreement_conflict_incremental_alpha"]["STRONG_AGREEMENT"]["resolved_pairs"], 1)
        self.assertEqual(report["sample"]["status"], "warmup_insufficient_evidence")
        self.assertFalse(report["bounded_influence_enabled"])


if __name__ == "__main__":
    unittest.main()
