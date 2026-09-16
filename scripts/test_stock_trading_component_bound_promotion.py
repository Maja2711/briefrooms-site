#!/usr/bin/env python3
"""Tests for exact Challenger/evaluation/deployment binding."""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from scripts import stock_trading_component_bound_promotion as bound


def _hash_without(payload: dict, field: str) -> str:
    body = dict(payload)
    body.pop(field, None)
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        challengers = root / "challengers"
        evaluations = root / "evaluations"
        registry_path = root / "registry.json"
        challengers.mkdir(); evaluations.mkdir()

        deployment = {
            "component": "ranking",
            "version": "v2.exact-test",
            "type": "config_patch",
            "targets": {
                "gpw_daily_config": [
                    {"op": "replace", "path": ["weights", "catalyst"], "value": 24},
                    {"op": "replace", "path": ["weights", "relative_momentum"], "value": 21}
                ]
            }
        }
        dep_sha = bound.deployment_sha256(deployment)
        _write(registry_path, {
            "schema_version": "stock-trading-component-deployments-v1",
            "deployments": {"ranking-v2-exact-test": deployment}
        })

        candidate = {
            "component": "ranking",
            "deployment_id": "ranking-v2-exact-test",
            "base_manifest_revision": 1,
            "base_component_version": "v1",
            "deployment_sha256": dep_sha
        }
        challenger = {
            "schema_version": "stock-trading-v2-challenger-policy-v1",
            "challenger_id": "exact-test",
            "component": "relative_momentum_weight",
            "production_candidate": candidate
        }
        challenger["challenger_sha256"] = _hash_without(challenger, "challenger_sha256")
        _write(challengers / "exact-test.json", challenger)

        evaluation = {
            "schema_version": "stock-trading-v2-challenger-evaluation-v1",
            "challenger_id": "exact-test",
            "component": "relative_momentum_weight",
            "state": "RESEARCH_PASS_PROMOTION_ELIGIBLE",
            "metrics": {"formal_pass": True}
        }
        evaluation["evaluation_sha256"] = _hash_without(evaluation, "evaluation_sha256")
        _write(evaluations / "exact-test.json", evaluation)
        first = bound.stage_bound_evidence(
            challengers=challengers,
            evaluations=evaluations,
            registry_path=registry_path,
            output_root=root / "stage1",
        )
        assert first["accepted"] == 0
        assert first["rejected"]

        evaluation.pop("evaluation_sha256")
        evaluation["evaluated_production_candidate"] = {
            **candidate,
            "execution_semantics": bound.EXACT_SEMANTICS,
        }
        evaluation["evaluation_sha256"] = _hash_without(evaluation, "evaluation_sha256")
        _write(evaluations / "exact-test.json", evaluation)
        second = bound.stage_bound_evidence(
            challengers=challengers,
            evaluations=evaluations,
            registry_path=registry_path,
            output_root=root / "stage2",
        )
        assert second["accepted"] == 1, second

        evaluation.pop("evaluation_sha256")
        evaluation["evaluated_production_candidate"]["deployment_sha256"] = "0" * 64
        evaluation["evaluation_sha256"] = _hash_without(evaluation, "evaluation_sha256")
        _write(evaluations / "exact-test.json", evaluation)
        third = bound.stage_bound_evidence(
            challengers=challengers,
            evaluations=evaluations,
            registry_path=registry_path,
            output_root=root / "stage3",
        )
        assert third["accepted"] == 0
        assert any("deployment_sha256" in row["reason"] or "hash" in row["reason"] for row in third["rejected"])

    print("COMPONENT_BOUND_PROMOTION_TESTS_OK")


if __name__ == "__main__":
    run()
