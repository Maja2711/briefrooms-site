"""Research-only fundamental ranking experiment; protect V1 and no-lookahead."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from scripts import stock_trading_fundamental_policy as quality
from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_experience_store as experience
from scripts import stock_trading_v2_fundamental_trial as v1
from scripts import stock_trading_v2_fundamental_ranking_study as ranking
from tests.test_stock_trading_v2_fundamental_trial import NOW, DECISION, sample, fundamentals


def quality_at(percent):
    data = fundamentals(True)
    factors = {}
    for key, _weight, low, high in quality.SCORE_FACTORS:
        factors[key] = low + (high - low) * percent / 100
    score, coverage = quality.recompute_score(factors)
    data.update({"metrics": factors, "fundamental_score": score, "observed_score_weight": coverage})
    return data


def fixture_with_events(base: Path):
    opp, evidence, _ = sample()
    protocol = v1._read(v1.PROTOCOL_CONFIG)
    for row in opp["evaluated_candidates"]:
        row["fundamental_analysis"] = quality_at(60 if row["symbol"] == "WEAK" else 100)
    evidence["candidates"] = [
        {"symbol": row["symbol"], "fundamental_analysis": row["fundamental_analysis"]}
        for row in opp["evaluated_candidates"]
    ]
    experience_root = base / "experiences"
    original_events = []
    for row in opp["evaluated_candidates"]:
        event = contracts.make_experience_event(
            market="US", symbol=row["symbol"], decision_at=DECISION,
            session_date="2026-11-20", selected=row["symbol"] == "WEAK",
            source_engine="stock-trading-v2-opportunity-engine",
            source_schema_version="stock-trading-v2-portfolio-opportunity-v1",
            source_policy_version="shadow", source_payload_sha256="o" * 64,
            candidate_state={"risk_plan": row["research_risk_plan"]},
            recorded_at=DECISION,
        )
        experience.persist_event(experience_root, event)
        original_events.append(event)
    v1_root = base / "v1"
    with mock.patch.object(v1.opportunity_events, "events_from_snapshots",
                           return_value=original_events):
        assert v1.freeze(opp, evidence, protocol=protocol, root=v1_root,
                         experience_root=experience_root, frozen_at=NOW)["status"] == "FROZEN"
    return opp, evidence, original_events, v1_root, experience_root


class FundamentalRankingStudyTests(unittest.TestCase):
    def test_rank_changes_only_inside_v1_approved_universe(self):
        opp, _, _ = sample()
        opp["evaluated_candidates"][0]["fundamental_analysis"] = quality_at(60)
        opp["evaluated_candidates"][1]["fundamental_analysis"] = quality_at(100)
        original_utilities = [r["utility"] for r in opp["evaluated_candidates"]]
        self.assertEqual(ranking.choices(opp),
                         {"v1": "WEAK", "w10": "WEAK", "w20": "WEAK", "w30": "GOOD"})
        self.assertEqual(original_utilities, [r["utility"] for r in opp["evaluated_candidates"]])
        opp["evaluated_candidates"][1]["eligible"] = False
        self.assertTrue(all(name == "WEAK" for name in ranking.choices(opp).values()))
        opp["evaluated_candidates"][0]["fundamental_analysis"] = quality_at(20)
        self.assertTrue(all(v is None for v in ranking.choices(opp).values()))
        with self.assertRaises(ValueError):
            ranking.choose(opp, 25)

    def test_point_in_time_missing_facts_do_not_get_ranking_preference(self):
        opp, _, _ = sample()
        opp["evaluated_candidates"][0]["fundamental_analysis"] = quality_at(90)
        opp["evaluated_candidates"][0]["fundamental_analysis"]["source_facts"]["revenue_fy"]["filed"] = "2026-11-21"
        self.assertEqual(ranking.choices(opp), {a: "GOOD" for a in ("v1", "w10", "w20", "w30")})

    def test_requires_original_v1_freeze_and_never_backfills(self):
        opp, evidence, _ = sample()
        cfg = ranking.read(ranking.CONFIG_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(ranking.freeze(
                opp, evidence, config=cfg, root=root / "ranking",
                v1_root=root / "absent", frozen_at=NOW)["status"], "WAITING_V1_PROSPECTIVE_FREEZE")
            self.assertEqual(ranking.freeze(
                opp, evidence, config=cfg, root=root / "ranking",
                v1_root=root / "absent", frozen_at=NOW + timedelta(hours=2))["status"],
                "LATE_OR_FUTURE_DECISION")

    def test_record_all_arms_once_and_detect_tampering(self):
        cfg = ranking.read(ranking.CONFIG_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            opp, evidence, original_events, v1_root, event_root = fixture_with_events(base)
            study_root = base / "ranking"
            with mock.patch.object(ranking.events, "events_from_snapshots",
                                   return_value=original_events):
                result = ranking.freeze(
                    opp, evidence, config=cfg, root=study_root, v1_root=v1_root,
                    event_root=event_root, frozen_at=NOW)
                self.assertEqual(result["status"], "FROZEN")
                self.assertEqual(result["changed_arms"], ["w30"])
                self.assertEqual(ranking.verify_root(study_root, cfg, v1_root=v1_root)["frozen_sessions"], 1)
                self.assertEqual(ranking.freeze(
                    opp, evidence, config=cfg, root=study_root, v1_root=v1_root,
                    event_root=event_root, frozen_at=NOW)["status"], "ALREADY_FROZEN")
            path = study_root / "2026-11-20.json"
            saved = json.loads(path.read_text())
            self.assertFalse(saved["governance"]["production_decision_influence"])
            self.assertFalse(saved["governance"]["automatic_promotion"])
            self.assertEqual(saved["arms"]["v1"]["candidate_symbol"], "WEAK")
            self.assertEqual(saved["arms"]["w30"]["candidate_symbol"], "GOOD")
            saved["arms"]["w30"]["candidate_symbol"] = "WEAK"
            with self.assertRaises(ValueError):
                ranking.validate_receipt(saved, cfg, v1.read(v1_root / "2026-11-20.json"))

    def test_fixed_60_no_early_promotion_or_no_data_imputation(self):
        cfg = ranking.read(ranking.CONFIG_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            result = ranking.evaluate(
                config=cfg, root=Path(tmp), replay_config_path=ranking.REPLAY_CONFIG_PATH)
            self.assertEqual(result["status"], "COLLECTING")
            self.assertFalse(result["eligible_for_production"])
            self.assertFalse(result["automatic_promotion"])
            # No unregistered test weights/rolling-look reuse can change a result.
            changed = dict(cfg, weights_percent=[25], one_formal_look=False)
            with self.assertRaises(ValueError):
                ranking.validate_protocol(changed)

    def test_bootstrap_circular_blocks_deterministic(self):
        series = [0.5, -0.1, 0.5, -0.1, 0.5, -0.1] * 10
        a = ranking._block_bootstrap_lower(
            series, alpha=0.05 / 6, n=4000, block_length=5, seed=11)
        b = ranking._block_bootstrap_lower(
            series, alpha=0.05 / 6, n=4000, block_length=5, seed=11)
        self.assertEqual(a, b)
        self.assertLess(a, sum(series) / len(series))


if __name__ == "__main__":
    unittest.main()
