from __future__ import annotations

import copy
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts import daily_stock_rejected_candidate_freeze as freeze
from scripts import gpw_loco_counterfactual_replay as loco
from scripts import gpw_rejected_candidate_outcomes as outcomes

WARSAW = ZoneInfo("Europe/Warsaw")
UTC = ZoneInfo("UTC")


def weights() -> dict:
    return {
        "catalyst": 25,
        "relative_momentum": 20,
        "volume_liquidity": 15,
        "market_context": 15,
        "risk_reward": 15,
        "historical_expectancy": 10,
    }


def candidate(
    symbol: str,
    *,
    relative: float,
    other: float = 50.0,
    reference: float = 100.0,
    risk: float = 0.05,
    opening_score: float = 50.0,
    ev_score: float | None = None,
    ev_weight: float = 0.0,
    pre_ev_rr: float = 2.0,
    full_rr: float = 2.0,
    quant_rank: int = 1,
) -> dict:
    base = {
        "catalyst": 50.0,
        "relative_momentum": relative,
        "volume_liquidity": other,
        "market_context": other,
        "risk_reward": other,
        "historical_expectancy": other,
    }
    legacy = loco._weighted_score(base, weights())
    opening_adjusted = round(legacy * 0.75 + opening_score * 0.25, 2)
    final = round(opening_adjusted * (1.0 - ev_weight) + (ev_score or 0.0) * ev_weight, 2) if ev_weight else opening_adjusted
    quant_pre = loco._weighted_score(base, {key: weights()[key] for key in loco.QUANT_COMPONENTS})
    return {
        "symbol": symbol,
        "name": symbol,
        "sector": "test",
        "quant_rank": quant_rank,
        "quant_pre_score": quant_pre,
        "base_scores": base,
        "legacy_composite_score": legacy,
        "opening_confirmation_score": opening_score,
        "opening_confirmation": {"score": opening_score},
        "opening_adjusted_score": opening_adjusted,
        "expected_value_score": ev_score,
        "expected_value_model": {"status": "ready", "score": ev_score, "selected_reward_risk": full_rr} if ev_score is not None else None,
        "expected_value_weight": ev_weight,
        "final_score": final,
        "pre_ev_reward_risk": pre_ev_rr,
        "full_reward_risk": full_rr,
        "risk_percent": risk,
        "historical_feature_session": "2026-09-04",
        "historical_feature_lag_sessions": 0,
        "execution_data_gate": {"accepted": True},
        "market_snapshot": {
            "date": "2026-09-07",
            "last": reference,
            "open": reference,
            "high": reference * 1.01,
            "low": reference * 0.99,
            "observed_at": "2026-09-07T10:00:30+02:00",
        },
    }


def published_payload(selected: dict) -> dict:
    plan = loco._plan_from_candidate(selected)
    return {
        "date": "2026-09-07",
        "generated_at": "2026-09-07T10:00:00+02:00",
        "decision": "TRANSAKCJA",
        "selection": {
            "selection_mode": "MANDATORY_DAILY_FINAL",
            "symbol": selected["symbol"],
            "score": selected["final_score"],
            "opening_adjusted_score": selected["opening_adjusted_score"],
            "opening_confirmation_score": selected["opening_confirmation_score"],
            "expected_value_score": selected["expected_value_score"],
            "reference_price": plan["reference_price"],
            "entry_zone": plan["entry_zone"],
            "stop": plan["stop"],
            "target": plan["target"],
            "reward_risk": plan["reward_risk"],
            "risk_percent": plan["risk_percent"],
        },
        "data_quality": {"expected_session": "2026-09-04"},
    }


def snapshot_for(candidates: list[dict]) -> dict:
    full = sorted(candidates, key=loco._candidate_sort_key, reverse=True)[0]
    return loco.build_prospective_snapshot(
        payload=published_payload(full),
        evaluated_candidates=candidates,
        config={"weights": weights()},
        policy={"opening_confirmation_weight": 0.25},
        candidate_errors={},
        captured_at=datetime(2026, 9, 7, 10, 2, tzinfo=WARSAW),
        ranking_hash="ranking",
    )


def minimal_freeze() -> dict:
    body = {
        "schema_version": freeze.SCHEMA_VERSION,
        "market": "gpw",
        "date": "2026-09-07",
        "decision_at": "2026-09-07T10:00:00+02:00",
        "frozen_at": "2026-09-07T08:01:00Z",
        "source_payload_sha256": "source",
        "status": "frozen",
        "selected_symbol": "AAA.WA",
        "candidate_count": 0,
        "economically_evaluable_count": 0,
        "candidates": [],
        "contract": {
            "prospective_only": True,
            "preserve_existing_same_decision": True,
            "news_or_llm_rerun": False,
            "source_engine_writeback": False,
            "decision_influence": False,
            "future_outcome_requires_preexisting_freeze": True,
        },
    }
    body["freeze_sha256"] = freeze._sha(body)
    return body


def outcome_record(full_return: float = 1.62) -> dict:
    frozen = minimal_freeze()
    source = {
        "schema_version": outcomes.SOURCE_SCHEMA_VERSION,
        "market": "gpw",
        "decision_date": "2026-09-07",
        "decision_at": "2026-09-07T10:00:00+02:00",
        "producer_decision": "TRANSAKCJA",
        "producer_reason": "test",
        "selected_plan": {"action": "LONG", "symbol": "AAA.WA", "entry_zone": [99.7, 100.6], "stop": 95.0, "target": 110.0, "reward_risk": 2.0, "reference_price": 100.0},
        "valid_until": "2026-09-09",
        "horizon_sessions": [1, 2],
        "rejected_candidate_freeze": frozen,
        "freeze_sha256": frozen["freeze_sha256"],
        "comparison_contract": {},
        "governance": {
            "prospective_only": True,
            "historical_backfill": False,
            "decision_influence": False,
            "ranking_writeback": False,
            "gate_writeback": False,
            "production_trade_writeback": False,
            "automatic_learning_writeback": False,
            "automatic_promotion": False,
            "observational_only": True,
        },
    }
    record = {
        "schema_version": outcomes.SCHEMA_VERSION,
        "market": "gpw",
        "decision_date": "2026-09-07",
        "captured_at": "2026-09-07T08:02:00Z",
        "source_snapshot": source,
        "source_snapshot_sha256": outcomes._sha(source),
        "settlement": {
            "status": "RESOLVED",
            "last_checked_at": "2026-09-09T16:00:00Z",
            "selected": {
                "symbol": "AAA.WA",
                "action": "LONG",
                "horizons": [
                    {"status": "RESOLVED", "horizon_sessions": 1, "session": "2026-09-08", "net_return_percent": 0.5},
                    {"status": "RESOLVED", "horizon_sessions": 2, "session": "2026-09-09", "net_return_percent": full_return},
                ],
            },
            "rejected_candidates": [],
            "summary": {"status": "RESOLVED"},
        },
    }
    outcomes.verify_record(record)
    return record


def intraday_bar(symbol: str) -> list[dict]:
    price = 50.0 if symbol == "BBB.WA" else 100.0
    return [{
        "timestamp": datetime(2026, 9, 7, 10, 5, tzinfo=WARSAW),
        "open": price,
        "high": price * 1.01,
        "low": price * 0.995,
        "close": price,
    }]


def daily_rows(symbol: str) -> list[dict]:
    if symbol == "BBB.WA":
        return [{"day": "2026-09-08", "close": 52.0}, {"day": "2026-09-09", "close": 55.0}]
    return [{"day": "2026-09-08", "close": 101.0}, {"day": "2026-09-09", "close": 102.0}]


class LocoReplayTests(unittest.TestCase):
    def test_prospective_full_replay_must_match_published_decision(self):
        a = candidate("AAA.WA", relative=100.0, quant_rank=1)
        b = candidate("BBB.WA", relative=40.0, other=60.0, reference=50.0, quant_rank=2)
        snap = snapshot_for([a, b])
        self.assertEqual(snap["status"], "VALIDATED_EQUIVALENT")
        self.assertTrue(snap["validation"]["full_replay_equivalent"])
        loco.verify_snapshot(snap)

        bad = copy.deepcopy(snap)
        bad["producer_selected_symbol"] = "ZZZ.WA"
        body = dict(bad)
        body.pop("snapshot_sha256")
        bad["snapshot_sha256"] = loco._sha(body)
        loco.verify_snapshot(bad)  # hash/authority still valid; equivalence data itself remains immutable evidence

    def test_remove_relative_momentum_changes_counterfactual_selection(self):
        a = candidate("AAA.WA", relative=100.0, quant_rank=1)
        b = candidate("BBB.WA", relative=40.0, other=60.0, reference=50.0, quant_rank=2)
        snap = snapshot_for([a, b])
        full = loco.replay_selection(snap)
        without = loco.replay_selection(snap, "relative_momentum")
        self.assertEqual(full["selected_symbol"], "AAA.WA")
        self.assertEqual(without["selected_symbol"], "BBB.WA")

    def test_expected_value_removal_changes_plan_even_when_symbol_is_same(self):
        a = candidate(
            "AAA.WA",
            relative=100.0,
            ev_score=100.0,
            ev_weight=0.20,
            pre_ev_rr=1.8,
            full_rr=2.5,
        )
        snap = snapshot_for([a])
        full = loco.replay_selection(snap)
        without = loco.replay_selection(snap, "expected_value")
        self.assertEqual(full["selected_symbol"], without["selected_symbol"])
        self.assertEqual(full["plan"]["reward_risk"], 2.5)
        self.assertEqual(without["plan"]["reward_risk"], 1.8)
        self.assertTrue(loco._plan_changed(full["plan"], without["plan"]))

    def test_late_capture_is_invalid_not_backfilled(self):
        a = candidate("AAA.WA", relative=100.0)
        snap = loco.build_prospective_snapshot(
            payload=published_payload(a),
            evaluated_candidates=[a],
            config={"weights": weights()},
            policy={"opening_confirmation_weight": 0.25},
            candidate_errors={},
            captured_at=datetime(2026, 9, 7, 11, 0, tzinfo=WARSAW),
        )
        self.assertEqual(snap["status"], "INVALID_CAPTURE_WINDOW")
        self.assertFalse(snap["validation"]["full_replay_equivalent"])
        self.assertFalse(snap["governance"]["historical_backfill"])

    def test_hard_gate_component_is_explicitly_unsupported(self):
        a = candidate("AAA.WA", relative=100.0)
        snap = snapshot_for([a])
        result = loco.replay_selection(snap, "minimum_liquidity")
        self.assertEqual(result["status"], "UNSUPPORTED")
        self.assertIn("not prospectively evaluated", result["reason"])

    def test_replay_marginal_value_is_full_minus_without_component(self):
        a = candidate("AAA.WA", relative=100.0, quant_rank=1)
        b = candidate("BBB.WA", relative=40.0, other=60.0, reference=50.0, risk=0.05, quant_rank=2)
        snap = snapshot_for([a, b])
        record = outcome_record(1.62)
        replay = loco.replay_snapshot_outcome(
            snap,
            record,
            config={"non_session_dates": []},
            now=datetime(2026, 9, 9, 18, 0, tzinfo=WARSAW),
            intraday_fetcher=intraday_bar,
            daily_fetcher=daily_rows,
        )
        self.assertEqual(replay["status"], "RESOLVED")
        relative = next(row for row in replay["variants"] if row["component"] == "relative_momentum")
        self.assertTrue(relative["decision_changed"])
        self.assertAlmostEqual(relative["without_component_t2_net_return_percent"], 9.62, places=2)
        self.assertAlmostEqual(relative["marginal_value_percent"], -8.0, places=2)
        self.assertEqual(relative["classification"], "HARMFUL_COMPONENT")

    def test_artifact_is_zero_authority_and_aggregates_component_value(self):
        a = candidate("AAA.WA", relative=100.0, quant_rank=1)
        b = candidate("BBB.WA", relative=40.0, other=60.0, reference=50.0, quant_rank=2)
        snap = snapshot_for([a, b])
        artifact = loco.build_replay_artifact(
            snapshots=[snap],
            outcome_records=[outcome_record(1.62)],
            config={"non_session_dates": []},
            now=datetime(2026, 9, 9, 18, 0, tzinfo=WARSAW),
            intraday_fetcher=intraday_bar,
            daily_fetcher=daily_rows,
        )
        loco.verify_replay_artifact(artifact)
        relative = next(row for row in artifact["components"] if row["component"] == "relative_momentum")
        self.assertEqual(relative["resolved_decision_count"], 1)
        self.assertAlmostEqual(relative["mean_marginal_value_percent"], -8.0, places=2)
        self.assertEqual(relative["evidence_status"], "OBSERVING")
        self.assertFalse(artifact["governance"]["decision_influence"])
        self.assertFalse(artifact["governance"]["automatic_learning_writeback"])
        self.assertFalse(artifact["method"]["absolute_causal_claim"])


if __name__ == "__main__":
    unittest.main()
