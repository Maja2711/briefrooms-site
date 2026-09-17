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


def _pair(name: str, *, threshold: int, horizon: int, revision: int = 1, tamper_hash: bool = False) -> tuple[dict, dict, str]:
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
        "horizon_sessions": horizon,
        "production_candidate": copy.deepcopy(candidate),
    }
    challenger["challenger_sha256"] = promotion._hash_without(challenger, "challenger_sha256")
    evaluation = {
        "schema_version": "stock-trading-v2-challenger-evaluation-v1",
        "challenger_id": name,
        "component": "entry_score_below_threshold",
        "horizon_sessions": horizon,
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

        fixtures: list[tuple[str, dict, dict]] = []
        valid_ids: list[str] = []
        for horizon in (1, 2):
            ch, ev, deployment_id = _pair(f"valid-h{horizon}", threshold=70, horizon=horizon)
            fixtures.append((f"valid-h{horizon}", ch, ev))
            valid_ids.append(deployment_id)
        assert valid_ids[0] == valid_ids[1]
        valid_id = valid_ids[0]

        # A short-only PASS is deliberately insufficient: the exact same
        # deployment must also PASS the other production holding horizon.
        short_only_ch, short_only_ev, short_only_id = _pair("short-only", threshold=71, horizon=1)
        fixtures.append(("short-only", short_only_ch, short_only_ev))

        too_large_ids: list[str] = []
        for horizon in (1, 2):
            ch, ev, deployment_id = _pair(f"too-large-h{horizon}", threshold=68, horizon=horizon)
            fixtures.append((f"too-large-h{horizon}", ch, ev))
            too_large_ids.append(deployment_id)
        too_large_id = too_large_ids[0]

        tampered_ch, tampered_ev, tampered_id = _pair("tampered", threshold=69, horizon=1, tamper_hash=True)
        stale_ch, stale_ev, stale_id = _pair("stale", threshold=69, horizon=2, revision=2)
        long_ch, long_ev, long_id = _pair("long-horizon", threshold=69, horizon=20)
        fixtures.extend([
            ("tampered", tampered_ch, tampered_ev),
            ("stale", stale_ch, stale_ev),
            ("long-horizon", long_ch, long_ev),
        ])

        for name, ch, ev in fixtures:
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
        reason_text = "\n".join(row["reason"] for row in result["rejected"])
        assert "maximum per-promotion delta" in reason_text
        assert "SHA" in reason_text
        assert "stale" in reason_text.lower()
        assert "outside the 1-2 session production mandate" in reason_text
        assert "missing exact fresh-holdout PASS" in reason_text

        approved = router.load_registry(approved_path)
        assert set(approved["deployments"]) == {valid_id}
        for rejected_id in (short_only_id, too_large_id, tampered_id, stale_id, long_id):
            assert rejected_id not in approved["deployments"]

        promoted = champion.build_promoted_manifest(
            base_manifest,
            component="entry",
            version=approved["deployments"][valid_id]["version"],
            deployment_id=valid_id,
            challenger_id="valid-h1",
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
