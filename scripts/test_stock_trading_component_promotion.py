#!/usr/bin/env python3
"""Promotion transaction unit tests; independent proof and monitor are seams.

Only proof verification and monitor registration are mocked. Native proof and
replay are tested without mocks in tests/test_stock_trading_component_evidence.py.
All persistence paths and runtime inputs in this file are temporary fixtures.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts import stock_trading_component_champion as champion
from scripts import stock_trading_component_promotion as promotion
from scripts import stock_trading_component_router as router
from scripts import stock_trading_component_bound_promotion as bound
from scripts import stock_trading_component_evidence as evidence
from scripts import stock_trading_component_monitor as monitor


def _hash(payload: dict, field: str) -> str:
    body = dict(payload)
    body.pop(field, None)
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@patch.object(monitor, "register_activation", return_value={"registered": ["unit-monitor"]})
@patch.object(evidence, "validate_proof", return_value={"verified": True})
def run(proof_mock, activation_mock) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = root / "manifest.json"
        registry = root / "registry.json"
        history = root / "history"
        audit = root / "audit.jsonl"
        challengers = root / "challengers"
        evaluations = root / "evaluations"
        gpw = root / "gpw.json"
        policy = root / "policy.json"
        trials = root / "trials.json"
        evidence.initialize_trials(trials)
        portfolio_path = root / "portfolio.json"
        bridge_config_path = root / "bridge-config.json"
        runtime_path = root / "runtime.json"
        for path in (portfolio_path, bridge_config_path, runtime_path):
            _write(path, {})
        challengers.mkdir(); evaluations.mkdir()

        _write(manifest, champion.bootstrap_manifest(created_at="2026-09-16T00:00:00Z"))
        _write(registry, {
            "schema_version": router.REGISTRY_SCHEMA,
            "deployments": {
                "ranking-v2-test": {
                    "component": "ranking",
                    "version": "v2.test",
                    "type": "config_patch",
                    "targets": {
                        "gpw_daily_config": [
                            {"op": "replace", "path": ["weights", "catalyst"], "value": 24},
                            {"op": "replace", "path": ["weights", "relative_momentum"], "value": 21}
                        ]
                    }
                }
            }
        })
        _write(gpw, {
            "universe": [{"symbol": "X.WA"}],
            "weights": {"catalyst": 25, "relative_momentum": 20, "volume_liquidity": 15, "market_context": 15, "risk_reward": 15, "historical_expectancy": 10},
            "minimum_reward_risk": 1.5,
            "maximum_risk_percent": 0.07
        })
        _write(policy, {
            "forced_trade_allowed": False,
            "markets": {
                "GPW": {"max_open_positions": 3, "minimum_reward_risk": 1.5, "maximum_risk_percent": 0.07},
                "US": {"max_open_positions": 3, "minimum_reward_risk": 1.5, "maximum_risk_percent": 0.07}
            }
        })
        deployment = json.loads(registry.read_text(encoding="utf-8"))["deployments"]["ranking-v2-test"]
        deployment_sha = bound.deployment_sha256(deployment)

        challenger_payload = {
            "schema_version": "stock-trading-v2-challenger-policy-v1",
            "challenger_id": "challenger-test",
            "created_at": "2026-09-16T00:00:00Z",
            "validation_start_at": "2026-09-16T00:00:00Z",
            "component": "relative_momentum_weight",
            "horizon_sessions": 2,
            "holdout": {"fixed_paired_n": 30, "single_formal_look": True},
            "governance": {"production_decision_influence": False, "automatic_policy_writeback": False, "safety_gates_unchanged": True},
            "production_candidate": {
                "component": "ranking",
                "deployment_id": "ranking-v2-test",
                "base_manifest_revision": 1,
                "base_component_version": "v1",
                "deployment_sha256": deployment_sha,
                "deployment_spec": deployment,
            }
        }
        challenger_payload["challenger_sha256"] = _hash(challenger_payload, "challenger_sha256")
        _write(challengers / "challenger-test.json", challenger_payload)
        evaluation_payload = {
            "schema_version": "stock-trading-v2-challenger-evaluation-v1",
            "challenger_id": "challenger-test",
            "component": "relative_momentum_weight",
            "generated_at": "2026-09-16T01:00:00Z",
            "state": "RESEARCH_PASS_PROMOTION_ELIGIBLE",
            "metrics": {"formal_pass": True},
            "evaluated_production_candidate": {
                **{key: challenger_payload["production_candidate"][key] for key in bound.REQUIRED_CANDIDATE_FIELDS},
                "execution_semantics": bound.EXACT_SEMANTICS,
            },
        }
        evaluation_payload["evaluation_sha256"] = _hash(evaluation_payload, "evaluation_sha256")
        _write(evaluations / "challenger-test.json", evaluation_payload)

        fixture_paths = dict(source_root=root / "source", frames_root=root / "frames", trials_path=trials,
            monitor_root=root / "monitor", portfolio_path=portfolio_path, bridge_config_path=bridge_config_path, runtime_path=runtime_path)
        tampered_evaluation = {**evaluation_payload, "evaluated_production_candidate": {
            **evaluation_payload["evaluated_production_candidate"], "deployment_sha256": "0" * 64}}
        tampered_evaluation["evaluation_sha256"] = _hash(tampered_evaluation, "evaluation_sha256")
        _write(evaluations / "challenger-test.json", tampered_evaluation)
        rejected = promotion.run(challengers=challengers, evaluations=evaluations, manifest_path=manifest,
            registry_path=registry, history_dir=history, audit_path=audit, gpw_config_path=gpw, policy_path=policy,
            dry_run=True, **fixture_paths)
        assert rejected["status"] == "NO_PROMOTION", rejected
        assert any("deployment_sha256" in row["reason"] for row in rejected["skipped"])
        assert champion.load_manifest(manifest)["revision"] == 1
        assert activation_mock.call_count == 0
        _write(evaluations / "challenger-test.json", evaluation_payload)
        partial = promotion.run(challengers=challengers, evaluations=evaluations, manifest_path=manifest,
            registry_path=registry, history_dir=history, audit_path=audit, gpw_config_path=gpw, policy_path=policy,
            dry_run=True, **fixture_paths)
        assert partial["status"] == "NO_PROMOTION", partial
        assert any("horizon(s): 1" in row["reason"] for row in partial["skipped"])
        assert champion.load_manifest(manifest)["revision"] == 1
        assert activation_mock.call_count == 0

        one_session = deepcopy(challenger_payload)
        one_session.update(challenger_id="challenger-test-1", horizon_sessions=1)
        one_session["challenger_sha256"] = _hash(one_session, "challenger_sha256")
        _write(challengers / "challenger-test-1.json", one_session)
        one_session_evaluation = deepcopy(evaluation_payload)
        one_session_evaluation["challenger_id"] = "challenger-test-1"
        one_session_evaluation["evaluation_sha256"] = _hash(one_session_evaluation, "evaluation_sha256")
        _write(evaluations / "challenger-test-1.json", one_session_evaluation)
        result = promotion.run(challengers=challengers, evaluations=evaluations, manifest_path=manifest, registry_path=registry, history_dir=history, audit_path=audit, gpw_config_path=gpw, policy_path=policy, **fixture_paths)
        assert result["status"] == "PROMOTED", result
        active = champion.load_manifest(manifest)
        assert active["revision"] == 2
        assert active["components"]["ranking"]["version"] == "v2.test"
        assert active["components"]["entry"]["version"] == "v1"
        assert (history / "champion-r000001.json").exists()

        effective = router.apply_component_overrides(json.loads(gpw.read_text()), target="gpw_daily_config", manifest_path=manifest, registry_path=registry)
        assert effective["weights"]["catalyst"] == 24
        assert effective["weights"]["relative_momentum"] == 21
        assert sum(effective["weights"].values()) == 100
        assert activation_mock.call_count == 1
        assert activation_mock.call_args.kwargs["root"] == root / "monitor"
        assert activation_mock.call_args.kwargs["previous_manifest"]["revision"] == 1
        assert activation_mock.call_args.kwargs["activated_manifest"]["revision"] == 2

        result2 = promotion.run(challengers=challengers, evaluations=evaluations, manifest_path=manifest, registry_path=registry, history_dir=history, audit_path=audit, gpw_config_path=gpw, policy_path=policy, **fixture_paths)
        assert result2["status"] == "NO_PROMOTION", result2
        assert champion.load_manifest(manifest)["revision"] == 2

        bad_registry = root / "bad_registry.json"
        _write(bad_registry, {
            "schema_version": router.REGISTRY_SCHEMA,
            "deployments": {
                "bad": {"component": "risk", "version": "v2.bad", "type": "config_patch", "targets": {"stock_trading_policy": [{"op": "replace", "path": ["markets", "GPW", "maximum_risk_percent"], "value": 0.2}]}}
            }
        })
        try:
            router.load_registry(bad_registry)
        except router.RouterError:
            pass
        else:
            raise AssertionError("Safety invariant patch was not rejected")

        rollback_manifest = root / "rollback_manifest.json"
        _write(rollback_manifest, champion.bootstrap_manifest(created_at="2026-09-16T00:00:00Z"))
        gpw_before = gpw.read_bytes()
        policy_before = policy.read_bytes()
        activation_mock.side_effect = promotion.PromotionError("synthetic monitor registration failure")
        rolled = promotion.run(challengers=challengers, evaluations=evaluations, manifest_path=rollback_manifest, registry_path=registry, history_dir=root / "rollback_history", audit_path=root / "rollback_audit.jsonl", gpw_config_path=gpw, policy_path=policy, **fixture_paths)
        assert rolled["status"] == "AUTO_ROLLBACK", rolled
        restored = champion.load_manifest(rollback_manifest)
        assert restored["revision"] == 1
        assert restored["components"]["ranking"]["version"] == "v1"
        assert json.loads(gpw.read_bytes()) == json.loads(gpw_before)
        assert json.loads(policy.read_bytes()) == json.loads(policy_before)
        assert proof_mock.call_count == 8
        assert activation_mock.call_count == 2

    print("COMPONENT_PROMOTION_TESTS_OK")


if __name__ == "__main__":
    run()
