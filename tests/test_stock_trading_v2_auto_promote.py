from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts import stock_trading_v2_auto_promote as promote
from scripts import stock_trading_v2_challenger_eval as evaluator
from scripts import stock_trading_v2_challenger_factory as factory
from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_policy_learner as learner


class AutoPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "schema_version": promote.CONFIG_SCHEMA,
            "automatic_production_promotion_enabled": True,
            "require_exact_replay": True,
            "require_formal_holdout_pass": True,
            "maximum_promotions_per_run": 1,
            "allowed_components": ["entry"],
            "allowed_replay_adapters": ["entry_threshold_v1"],
            "allowed_targets": {
                "gpw_daily_config": [["minimum_composite_score"]],
                "stock_trading_policy": [["markets", "GPW", "minimum_entry_score"]],
            },
        }

    def _spec(self, value: int = 71) -> dict:
        return {
            "component": "entry",
            "version": f"entry-threshold-{value}-r1",
            "type": "config_patch",
            "targets": {
                "gpw_daily_config": [
                    {"op": "replace", "path": ["minimum_composite_score"], "value": value}
                ],
                "stock_trading_policy": [
                    {"op": "replace", "path": ["markets", "GPW", "minimum_entry_score"], "value": value}
                ],
            },
        }

    def _challenger(self, value: int = 71, challenger_id: str = "stchallv2-exact-test") -> dict:
        spec = self._spec(value)
        spec_sha = factory.deployment_sha256(spec)
        payload = {
            "schema_version": learner.SCHEMA_VERSION,
            "challenger_id": challenger_id,
            "created_at": "2026-09-19T05:00:00Z",
            "validation_start_at": "2026-09-19T05:00:00Z",
            "component": "entry_score_below_threshold",
            "horizon_sessions": 2,
            "champion": {"manifest_revision": 1, "component_version": "v1"},
            "change": {
                "type": "exact_config_patch_challenger",
                "deployment_id": "stdep-test",
                "deployment_sha256": spec_sha,
                "safety_gates_unchanged": True,
            },
            "production_candidate": {
                "component": "entry",
                "deployment_id": "stdep-test",
                "base_manifest_revision": 1,
                "base_component_version": "v1",
                "deployment_sha256": spec_sha,
                "deployment_spec": spec,
            },
            "exact_replay": {
                "adapter": "entry_threshold_v1",
                "champion_threshold": 72,
                "challenger_threshold": value,
                "market": "GPW",
                "requires_single_entry_blocker": True,
            },
            "origin_evidence": {"test": True},
            "holdout": {
                "fixed_paired_n": 30,
                "confidence_level": 0.9,
                "bootstrap_samples": 4000,
                "minimum_net_incremental_return_percent": 0.1,
                "minimum_net_positive_rate": 0.55,
                "minimum_unique_symbols": 5,
                "minimum_span_days": 10,
                "maximum_single_positive_contribution_share": 0.5,
                "single_formal_look": True,
                "historical_evidence_reuse_forbidden": True,
            },
            "state": "COLLECTING_FRESH_HOLDOUT",
            "governance": {
                "production_decision_influence": False,
                "automatic_policy_writeback": False,
                "production_promotion_enabled": False,
                "future_only_holdout": True,
                "safety_gates_unchanged": True,
                "tournament_is_discovery_only": True,
            },
        }
        payload["challenger_sha256"] = contracts.payload_sha256(payload)
        learner.validate_challenger(payload)
        return payload

    def _evaluation(self, challenger: dict, *, passed: bool = True, lower: float = 0.2) -> dict:
        production = challenger["production_candidate"]
        payload = {
            "schema_version": evaluator.SCHEMA_VERSION,
            "challenger_id": challenger["challenger_id"],
            "component": challenger["component"],
            "horizon_sessions": challenger["horizon_sessions"],
            "validation_start_at": challenger["validation_start_at"],
            "generated_at": "2026-10-20T22:30:00Z",
            "state": "RESEARCH_PASS_AWAITING_MANUAL_ENABLEMENT" if passed else "FORMAL_HOLDOUT_FAIL",
            "metrics": {
                "paired_n": 30,
                "required_n": 30,
                "mean_incremental_net_return_percent": 0.35 if passed else -0.1,
                "positive_rate": 0.63 if passed else 0.4,
                "unique_symbols": 10,
                "span_days": 20,
                "maximum_single_positive_contribution_share": 0.22,
                "bootstrap_lower_bound_percent": lower if passed else -0.2,
                "checks": {"test": passed},
                "formal_pass": passed,
            },
            "formal_samples": [],
            "governance": {
                "production_decision_influence": False,
                "automatic_policy_writeback": False,
                "production_promotion_enabled": False,
                "single_formal_look": True,
            },
            "evaluated_production_candidate": {
                "component": production["component"],
                "deployment_id": production["deployment_id"],
                "base_manifest_revision": production["base_manifest_revision"],
                "base_component_version": production["base_component_version"],
                "deployment_sha256": production["deployment_sha256"],
                "execution_semantics": "exact_shadow_replay",
            },
            "exact_replay_adapter": challenger["exact_replay"]["adapter"],
        }
        payload["evaluation_sha256"] = contracts.payload_sha256(payload)
        evaluator.validate_evaluation(payload)
        return payload

    def _write_production(self, root: Path, *, revision: int = 1) -> None:
        data = root / "data/investments"
        data.mkdir(parents=True, exist_ok=True)
        manifest = {
            "components": {
                name: {
                    "challenger_id": None,
                    "deployment_id": None,
                    "evidence_sha256": None,
                    "promoted_at": None,
                    "source": "legacy",
                    "version": "v1",
                }
                for name in ("entry", "exit", "meta_label", "portfolio", "ranking", "regime", "risk", "universe")
            },
            "previous_revision": None,
            "promotion_id": None,
            "revision": revision,
            "schema_version": promote.MANIFEST_SCHEMA,
            "status": "ACTIVE",
            "updated_at": "2026-09-16T00:00:00Z",
        }
        (data / "stock_trading_champion_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        gpw = {"schema_version": "gpw-daily-pick-config-v1", "minimum_composite_score": 72}
        (data / "gpw_daily_pick_config.json").write_text(json.dumps(gpw), encoding="utf-8")
        policy = {
            "schema_version": "stock-trading-policy-v1",
            "markets": {"GPW": {"minimum_entry_score": 72}},
        }
        (data / "stock_trading_policy.json").write_text(json.dumps(policy), encoding="utf-8")

    def _write_inputs(self, root: Path, challenger: dict, evaluation: dict) -> tuple[Path, Path, Path]:
        challengers = root / "challengers"
        evaluations = root / "evaluations"
        challengers.mkdir()
        evaluations.mkdir()
        (challengers / f"{challenger['challenger_id']}.json").write_text(json.dumps(challenger), encoding="utf-8")
        (evaluations / f"{challenger['challenger_id']}.json").write_text(json.dumps(evaluation), encoding="utf-8")
        config_path = root / "auto.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        return challengers, evaluations, config_path

    def test_formal_exact_pass_is_materialized_and_manifest_advances(self) -> None:
        challenger = self._challenger()
        evaluation = self._evaluation(challenger)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            production = root / "production"
            self._write_production(production)
            challengers, evaluations, config_path = self._write_inputs(root, challenger, evaluation)
            result = promote.run(
                challenger_root=challengers,
                evaluation_root=evaluations,
                production_root=production,
                config_path=config_path,
                evidence_commit="abc123",
                production_base_sha="main123",
                apply=True,
            )
            self.assertEqual("PROMOTED", result["status"])
            self.assertTrue(result["applied"])
            gpw = json.loads((production / "data/investments/gpw_daily_pick_config.json").read_text())
            policy = json.loads((production / "data/investments/stock_trading_policy.json").read_text())
            manifest = json.loads((production / "data/investments/stock_trading_champion_manifest.json").read_text())
            self.assertEqual(71, gpw["minimum_composite_score"])
            self.assertEqual(71, policy["markets"]["GPW"]["minimum_entry_score"])
            self.assertEqual(2, manifest["revision"])
            self.assertEqual(challenger["challenger_id"], manifest["components"]["entry"]["challenger_id"])
            records = list((production / "data/investments/stock_trading_promotions").glob("*.json"))
            self.assertEqual(1, len(records))
            record = json.loads(records[0].read_text())
            self.assertEqual(evaluation["evaluation_sha256"], record["evaluation_sha256"])
            self.assertTrue(record["governance"]["automatic_production_promotion"])

    def test_dry_run_never_mutates_production(self) -> None:
        challenger = self._challenger()
        evaluation = self._evaluation(challenger)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            production = root / "production"
            self._write_production(production)
            challengers, evaluations, config_path = self._write_inputs(root, challenger, evaluation)
            before = (production / "data/investments/gpw_daily_pick_config.json").read_text()
            result = promote.run(
                challenger_root=challengers,
                evaluation_root=evaluations,
                production_root=production,
                config_path=config_path,
                evidence_commit="abc123",
                production_base_sha="main123",
                apply=False,
            )
            self.assertEqual("ELIGIBLE", result["status"])
            self.assertFalse(result["applied"])
            self.assertEqual(before, (production / "data/investments/gpw_daily_pick_config.json").read_text())

    def test_failed_holdout_cannot_promote(self) -> None:
        challenger = self._challenger()
        evaluation = self._evaluation(challenger, passed=False)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            production = root / "production"
            self._write_production(production)
            challengers, evaluations, config_path = self._write_inputs(root, challenger, evaluation)
            result = promote.run(
                challenger_root=challengers,
                evaluation_root=evaluations,
                production_root=production,
                config_path=config_path,
                evidence_commit="abc123",
                production_base_sha="main123",
                apply=True,
            )
            self.assertEqual("NO_ELIGIBLE_PROMOTION", result["status"])
            gpw = json.loads((production / "data/investments/gpw_daily_pick_config.json").read_text())
            self.assertEqual(72, gpw["minimum_composite_score"])

    def test_stale_challenger_cannot_promote(self) -> None:
        challenger = self._challenger()
        evaluation = self._evaluation(challenger)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            production = root / "production"
            self._write_production(production, revision=2)
            challengers, evaluations, config_path = self._write_inputs(root, challenger, evaluation)
            result = promote.run(
                challenger_root=challengers,
                evaluation_root=evaluations,
                production_root=production,
                config_path=config_path,
                evidence_commit="abc123",
                production_base_sha="main123",
                apply=True,
            )
            self.assertEqual("NO_ELIGIBLE_PROMOTION", result["status"])

    def test_non_allowlisted_patch_is_rejected(self) -> None:
        challenger = self._challenger()
        bad = copy.deepcopy(challenger)
        bad["production_candidate"]["deployment_spec"]["targets"]["gpw_daily_config"][0]["path"] = ["minimum_reward_risk"]
        spec = bad["production_candidate"]["deployment_spec"]
        spec_sha = factory.deployment_sha256(spec)
        bad["production_candidate"]["deployment_sha256"] = spec_sha
        bad["change"]["deployment_sha256"] = spec_sha
        bad.pop("challenger_sha256", None)
        bad["challenger_sha256"] = contracts.payload_sha256(bad)
        evaluation = self._evaluation(bad)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            production = root / "production"
            self._write_production(production)
            challengers, evaluations, config_path = self._write_inputs(root, bad, evaluation)
            with self.assertRaises(contracts.ContractError):
                promote.run(
                    challenger_root=challengers,
                    evaluation_root=evaluations,
                    production_root=production,
                    config_path=config_path,
                    evidence_commit="abc123",
                    production_base_sha="main123",
                    apply=True,
                )

    def test_evaluation_binding_mismatch_is_rejected(self) -> None:
        challenger = self._challenger()
        evaluation = self._evaluation(challenger)
        evaluation["evaluated_production_candidate"]["deployment_sha256"] = "0" * 64
        evaluation.pop("evaluation_sha256", None)
        evaluation["evaluation_sha256"] = contracts.payload_sha256(evaluation)
        evaluator.validate_evaluation(evaluation)
        manifest = {
            "schema_version": promote.MANIFEST_SCHEMA,
            "status": "ACTIVE",
            "revision": 1,
            "components": {"entry": {"version": "v1"}},
        }
        with self.assertRaises(contracts.ContractError):
            promote.eligible_pairs(
                challengers=[challenger],
                evaluations=[evaluation],
                manifest=manifest,
                config=self.config,
            )

    def test_strongest_formal_pass_is_selected_deterministically(self) -> None:
        first = self._challenger(71, "stchallv2-exact-first")
        second = self._challenger(70, "stchallv2-exact-second")
        first_eval = self._evaluation(first, lower=0.12)
        second_eval = self._evaluation(second, lower=0.28)
        manifest = {
            "schema_version": promote.MANIFEST_SCHEMA,
            "status": "ACTIVE",
            "revision": 1,
            "components": {"entry": {"version": "v1"}},
        }
        rows = promote.eligible_pairs(
            challengers=[first, second],
            evaluations=[first_eval, second_eval],
            manifest=manifest,
            config=self.config,
        )
        self.assertEqual("stchallv2-exact-second", rows[0][0]["challenger_id"])


if __name__ == "__main__":
    unittest.main()
