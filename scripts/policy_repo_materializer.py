#!/usr/bin/env python3
"""Materialize a hash-verified PR35 registry into allowlisted production configs.

This is the only repository write path for autonomous promotion.  It can change
exactly one allowlisted scalar parameter per supported engine plus
``policy_version``.  It refuses manual version divergence, unknown overrides and
out-of-range values.  Rollback materializes the immutable checked-in baseline.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

try:
    from policy_runtime_overlay import load_registry
except ModuleNotFoundError:  # pragma: no cover
    from scripts.policy_runtime_overlay import load_registry

BASELINES_PATH = "data/investments/autonomous_policy_baselines.json"


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain an object")
    return payload


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    body = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        tmp = Path(handle.name)
    os.replace(tmp, path)


def _allowed_version(current: str, baseline: str) -> bool:
    return current == baseline or current.startswith(baseline + "+auto")


def materialize(registry_path: Path, repo_root: Path) -> dict[str, Any]:
    registry = load_registry(registry_path)
    if registry is None:
        raise ValueError("invalid autonomous policy registry")
    baselines = _read(repo_root / BASELINES_PATH)
    if baselines.get("schema_version") != "briefrooms-autonomous-policy-baselines-v1":
        raise ValueError("autonomous policy baseline schema mismatch")

    changes: list[dict[str, Any]] = []
    for engine_id, spec in (baselines.get("engines") or {}).items():
        state = (registry.get("engines") or {}).get(engine_id)
        if not isinstance(state, Mapping) or state.get("status") != "ACTIVE":
            raise ValueError(f"missing active policy state for {engine_id}")
        config_path = repo_root / str(spec["config_path"])
        config = _read(config_path)
        baseline_version = str(spec["baseline_policy_version"])
        current_version = str(config.get("policy_version") or "")
        if not _allowed_version(current_version, baseline_version):
            raise RuntimeError(f"manual policy-version divergence for {engine_id}: {current_version}")
        if str(state.get("baseline_policy_version") or "") != baseline_version:
            raise RuntimeError(f"registry baseline mismatch for {engine_id}")

        parameter = str(spec["parameter"])
        overrides = state.get("overrides") if isinstance(state.get("overrides"), Mapping) else {}
        if set(overrides) - {parameter}:
            raise RuntimeError(f"non-allowlisted override for {engine_id}")
        target_value = float(overrides.get(parameter, spec["baseline_value"]))
        if not float(spec["minimum_allowed"]) <= target_value <= float(spec["maximum_allowed"]):
            raise RuntimeError(f"policy value outside immutable bounds for {engine_id}")
        original = config.get(parameter)
        if isinstance(original, int) and target_value.is_integer():
            target: int | float = int(target_value)
        else:
            target = target_value
        target_version = str(state.get("effective_policy_version") or baseline_version)
        if not _allowed_version(target_version, baseline_version):
            raise RuntimeError(f"invalid effective version for {engine_id}")

        if config.get(parameter) == target and current_version == target_version:
            continue
        before = {"policy_version": current_version, parameter: config.get(parameter)}
        config[parameter] = target
        config["policy_version"] = target_version
        _atomic(config_path, config)
        changes.append({
            "engine_id": engine_id,
            "path": str(spec["config_path"]),
            "before": before,
            "after": {"policy_version": target_version, parameter: target},
            "policy_id": state.get("policy_id"),
            "source_candidate_id": state.get("source_candidate_id"),
        })
    return {"changed": bool(changes), "changes": changes}


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize PR35 policy registry to allowlisted repo configs")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--check", action="store_true", help="Materialize into checkout and report; caller decides whether to commit")
    args = parser.parse_args()
    result = materialize(Path(args.registry), Path(args.repo_root))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
