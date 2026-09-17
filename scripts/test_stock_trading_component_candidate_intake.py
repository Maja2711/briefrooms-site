#!/usr/bin/env python3
"""Synthetic safety tests for production-side exact Challenger intake."""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

from scripts import stock_trading_component_bound_promotion as bound
from scripts import stock_trading_component_candidate_intake as intake
from scripts import stock_trading_component_champion as champion
from scripts import stock_trading_component_promotion as promotion
from scripts import stock_trading_component_router as router


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _policy() -> dict:
    return {
        "forced_trade_allowed": False,
        "markets": {
            "GPW": {
                "minimum_entry_score": 72,
                "max_open_positions": 3,
                "minimum_reward_risk": 1.5,
                "maximum_risk_percent": 0.07,
            },
            "US": {
                "minimum_entry_score": 72,
                "max_open_positions": 3,
                "minimum_reward_risk": 1.5,
                "maximum_risk_percent": 0.07,
            },
        },
    }


def _gpw() -> dict:
    return {"minimum_composite_score": 72}


def _registry() -> dict:
    return {
        "schema_version": router.REGISTRY_SCHEMA,
        "governance": {
            "research_may_reference_only": True,
            "production_owns_executable_artifacts": True,
            "arbitrary_code_loading_forbidden": True,
            "one_component_per_promotion": True,
        },
        "deployments": {},
    }


def _spec(threshold: int) -> dict:
    return {
        "component": "entry",
        "version": f"entry-threshold-{threshold}-r1",
        "type": "config_patch",
        "targets": {
            "gpw_daily_config": [
                {"op": "replace", "path": ["minimum_composite_score"], "value": threshold},
            ],
            "stock_trading_policy": [
                {"op": "replace", "path": ["markets", "GPW", "minimum_entry_score"], "value": threshold},
            ],
        },
    }


def _pair(name: str, *, threshold: int, revision: int = 1, tamper_hash: bool = False) -> tuple[dict, dict, str]:
    spec = _spec(threshold)
    deployment_sha = bound.deployment_sha256(spec)
    deployment_id = f"stdep-entry-{deployment_sha[:20]}"
    candidate = {
        "component": "entry",
        "deployment_id": deployment_id,
        "base_manifest_revision": revision,
        "base_component_version": "v1",
        "deployment_sha256": ("0" * 64) if tamper_hash else deployment_sha,
        "deployment_spec": spec,
    }
    challenger = {
        "schema_version": "stock-trading-v2-challenger-policy-v1",
        "challenger_id": name,
        "component": "entry_score_below_threshold",
        "production_candidate": copy.deepcopy(candidate),
    }
    challenger["challenger_sha256"] = promotion._hash_without(challenger, "challenger_sha256")
    evaluation = {
        "schema_version": "stock-trading-v2-challenger-evaluation-v1",
        "challenger_id": name,
        "component": "entry_score_below_threshold",
        "state": "RESEARCH_PASS_EXACT_HOLDOUT",
        "metrics": {"formal_pass": True},
        "evaluated_production_candidate": {
            "component": candidate["component"],
            "deployment_id": candidate["deployment_id"],
            "base_manifest_revision": candidate["base_manifest_revision"],
            "base_component_version": candidate["base_component_version"],
            "deployment_sha256": candidate["deployment_sha256"],
            "execution_semantics": bound.EXACT_SEMANTICS,
        },
    }
    evaluation["evaluation_sha256"] = promotion._hash_without(evaluation, "evaluation_sha256")
    return challenger, evaluation, deployment_id


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="component-intake-test-") as tmp:
        root = Path(tmp)
        challengers = root / "challengers"
        evaluations = root / "evaluations"
        challengers.mkdir()
        evaluations.mkdir()
        registry_path = root / "registry.json"
        manifest_path = root / "manifest.json"
        gpw_path = root / "gpw.json"
        policy_path = root / "policy.json"
        approved_path = root / "approved.json"

        base_manifest = champion.bootstrap_manifest(created_at="2026-09-17T00:00:00Z")
        _write(manifest_path, base_manifest)
        _write(registry_path, _registry())
        _write(gpw_path, _gpw())
        _write(policy_path, _policy())

        valid_ch, valid_ev, valid_id = _pair("valid", threshold=70)
        too_large_ch, too_large_ev, too_large_id = _pair("too-large", threshold=68)
        tampered_ch, tampered_ev, tampered_id = _pair("tampered", threshold=70, tamper_hash=True)
        stale_ch, stale_ev, stale_id = _pair("stale", threshold=71, revision=2)

        for name, ch, ev in (
            ("valid", valid_ch, valid_ev),
            ("too-large", too_large_ch, too_large_ev),
            ("tampered", tampered_ch, tampered_ev),
            ("stale", stale_ch, stale_ev),
        ):
            _write(challengers / f"{name}.json", ch)
            _write(evaluations / f"{name}.json", ev)

        result = intake.approve_proposals(
            challengers=challengers,
            evaluations=evaluations,
            registry_path=registry_path,
            manifest_path=manifest_path,
            gpw_config_path=gpw_path,
            policy_path=policy_path,
            output_path=approved_path,
        )
        assert result["accepted"] == [valid_id]
        reasons = {row["challenger_id"]: row["reason"] for row in result["rejected"]}
        assert "maximum per-promotion delta" in reasons["too-large"]
        assert "SHA" in reasons["tampered"]
        assert "stale" in reasons["stale"].lower()

        approved = router.load_registry(approved_path)
        assert set(approved["deployments"]) == {valid_id}
        assert too_large_id not in approved["deployments"]
        assert tampered_id not in approved["deployments"]
        assert stale_id not in approved["deployments"]

        promoted = champion.build_promoted_manifest(
            base_manifest,
            component="entry",
            version=approved["deployments"][valid_id]["version"],
            deployment_id=valid_id,
            challenger_id="valid",
            evidence_sha256="a" * 64,
            promotion_id="synthetic-promotion",
            expected_revision=1,
            promoted_at="2026-09-17T01:00:00Z",
        )
        _write(manifest_path, promoted)
        persist = intake.persist_active(
            approved_registry_path=approved_path,
            registry_path=registry_path,
            manifest_path=manifest_path,
        )
        assert persist["persisted"] == [valid_id]
        production_registry = router.load_registry(registry_path)
        assert set(production_registry["deployments"]) == {valid_id}

    print("COMPONENT_CANDIDATE_INTAKE_TESTS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
