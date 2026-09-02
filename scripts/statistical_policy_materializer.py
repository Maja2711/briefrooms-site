#!/usr/bin/env python3
"""Fail-closed production materializer for PR36.

PR-A freezes all autonomous GPW/US production materialization while methodology
v2 collects fresh prospective validation and confirmation samples. Legacy PR36
authorization verification remains available for audit/tests, but materialize()
returns a no-op unless governance is explicitly un-frozen in a future reviewed
change.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

try:
    from policy_repo_materializer import materialize as base_materialize
    from policy_runtime_overlay import load_registry, production_promotion_enabled, PROMOTION_FREEZE_STATUS
    from statistical_promotion_gate import AUTH_FILENAME, _load_authorizations
except ModuleNotFoundError:  # pragma: no cover
    from scripts.policy_repo_materializer import materialize as base_materialize
    from scripts.policy_runtime_overlay import load_registry, production_promotion_enabled, PROMOTION_FREEZE_STATUS
    from scripts.statistical_promotion_gate import AUTH_FILENAME, _load_authorizations


def assert_statistical_authorization(registry_path: Path, authorization_path: Path) -> dict[str, Any]:
    registry = load_registry(registry_path)
    if registry is None:
        raise ValueError("invalid autonomous policy registry")
    auth = _load_authorizations(authorization_path)
    authorized = auth.get("authorizations") if isinstance(auth.get("authorizations"), Mapping) else {}
    checked: list[str] = []
    for engine_id, state in (registry.get("engines") or {}).items():
        revision = int(state.get("revision") or 0)
        if revision <= 0:
            continue
        policy_id = str(state.get("policy_id") or "")
        row = authorized.get(policy_id)
        if not isinstance(row, Mapping) or row.get("status") != "PASS":
            raise RuntimeError(f"production write blocked: missing PR36 PASS for {engine_id}")
        if row.get("engine_id") != engine_id:
            raise RuntimeError(f"production write blocked: PR36 engine mismatch for {engine_id}")
        if row.get("effective_policy_version") != state.get("effective_policy_version"):
            raise RuntimeError(f"production write blocked: PR36 version mismatch for {engine_id}")
        if row.get("candidate_id") != state.get("source_candidate_id"):
            raise RuntimeError(f"production write blocked: PR36 candidate mismatch for {engine_id}")
        checked.append(engine_id)
    return {"authorized_nonbaseline_engines": checked}


def materialize(registry_path: Path, authorization_path: Path, repo_root: Path) -> dict[str, Any]:
    registry = load_registry(registry_path)
    if registry is None:
        raise ValueError("invalid autonomous policy registry")
    if not production_promotion_enabled(registry):
        governance = registry.get("governance") if isinstance(registry.get("governance"), Mapping) else {}
        return {
            "changed": False,
            "status": PROMOTION_FREEZE_STATUS,
            "production_promotion_enabled": False,
            "promotion_methodology_version": governance.get("promotion_methodology_version"),
            "migration_boundary_at": governance.get("migration_boundary_at"),
            "updated": [],
        }
    authorization = assert_statistical_authorization(registry_path, authorization_path)
    result = base_materialize(registry_path, repo_root)
    result["statistical_authorization"] = authorization
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="PR36 statistically authorized policy materializer")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--authorizations")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()
    registry_path = Path(args.registry)
    authorization_path = Path(args.authorizations) if args.authorizations else registry_path.parent / AUTH_FILENAME
    result = materialize(registry_path, authorization_path, Path(args.repo_root))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
