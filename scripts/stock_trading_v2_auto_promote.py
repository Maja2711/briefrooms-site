#!/usr/bin/env python3
"""Bounded automatic production promotion for exact Stock Trading v2 Challengers.

This module is intentionally NOT a general code-writing agent. It may only
materialize an exact, previously frozen deployment_spec whose fresh prospective
holdout has formally passed. Production writes are restricted to allowlisted
JSON paths and are cryptographically bound to the Challenger and evaluation.
"""
from __future__ import annotations

import argparse
import copy
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts import stock_trading_v2_challenger_factory as factory
    from scripts import stock_trading_v2_challenger_eval as evaluator
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_policy_learner as learner
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_challenger_factory as factory
    import stock_trading_v2_challenger_eval as evaluator
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_policy_learner as learner

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "data/investments/stock_trading_v2_auto_promotion_config.json"
MANIFEST_REL = Path("data/investments/stock_trading_champion_manifest.json")
PROMOTION_ROOT_REL = Path("data/investments/stock_trading_promotions")
CONFIG_SCHEMA = "stock-trading-v2-auto-promotion-config-v1"
PROMOTION_SCHEMA = "stock-trading-v2-production-promotion-v1"
MANIFEST_SCHEMA = "stock-trading-champion-manifest-v1"

TARGET_FILES = {
    "gpw_daily_config": Path("data/investments/gpw_daily_pick_config.json"),
    "stock_trading_policy": Path("data/investments/stock_trading_policy.json"),
}


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        temp = Path(handle.name)
    temp.replace(path)


def validate_config(config: Mapping[str, Any]) -> None:
    if config.get("schema_version") != CONFIG_SCHEMA:
        raise contracts.ContractError("auto-promotion config schema mismatch")
    if config.get("automatic_production_promotion_enabled") is not True:
        raise contracts.ContractError("automatic production promotion is not enabled")
    if config.get("require_exact_replay") is not True:
        raise contracts.ContractError("auto-promotion must require exact replay")
    if config.get("require_formal_holdout_pass") is not True:
        raise contracts.ContractError("auto-promotion must require formal holdout PASS")
    max_promotions = int(config.get("maximum_promotions_per_run") or 0)
    if max_promotions != 1:
        raise contracts.ContractError("auto-promotion is fail-closed to exactly one promotion per run")
    allowed_components = config.get("allowed_components")
    if not isinstance(allowed_components, list) or not allowed_components:
        raise contracts.ContractError("auto-promotion allowed_components missing")
    targets = config.get("allowed_targets")
    if not isinstance(targets, Mapping) or not targets:
        raise contracts.ContractError("auto-promotion allowed_targets missing")
    for target, paths in targets.items():
        if target not in TARGET_FILES:
            raise contracts.ContractError(f"unknown auto-promotion target: {target}")
        if not isinstance(paths, list) or not paths:
            raise contracts.ContractError(f"allowlist missing for target: {target}")
        for path in paths:
            if not isinstance(path, list) or not path or not all(isinstance(part, str) and part for part in path):
                raise contracts.ContractError("invalid auto-promotion path allowlist")


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise contracts.ContractError("production champion manifest schema mismatch")
    if manifest.get("status") != "ACTIVE":
        raise contracts.ContractError("production champion manifest is not ACTIVE")
    if int(manifest.get("revision") or 0) < 1:
        raise contracts.ContractError("production champion manifest revision invalid")
    if not isinstance(manifest.get("components"), Mapping):
        raise contracts.ContractError("production champion components missing")


def _path_key(path: Sequence[str]) -> tuple[str, ...]:
    return tuple(str(part) for part in path)


def _allowed_paths(config: Mapping[str, Any], target: str) -> set[tuple[str, ...]]:
    raw = (config.get("allowed_targets") or {}).get(target) or []
    return {_path_key(path) for path in raw if isinstance(path, list)}


def _replace_path(document: dict[str, Any], path: Sequence[str], value: Any) -> Any:
    if not path:
        raise contracts.ContractError("empty production patch path")
    node: Any = document
    for part in path[:-1]:
        if not isinstance(node, dict) or part not in node:
            raise contracts.ContractError(f"production patch path missing: {'.'.join(path)}")
        node = node[part]
    leaf = path[-1]
    if not isinstance(node, dict) or leaf not in node:
        raise contracts.ContractError(f"production patch leaf missing: {'.'.join(path)}")
    previous = copy.deepcopy(node[leaf])
    node[leaf] = copy.deepcopy(value)
    return previous


def _iter_json_files(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    return [_read(path) for path in sorted(root.glob("*.json"))]


def _validate_exact_binding(challenger: Mapping[str, Any], evaluation: Mapping[str, Any]) -> dict[str, Any]:
    learner.validate_challenger(challenger)
    evaluator.validate_evaluation(evaluation)
    if evaluation.get("state") != "RESEARCH_PASS_AWAITING_MANUAL_ENABLEMENT":
        raise contracts.ContractError("evaluation is not a formal PASS")
    metrics = evaluation.get("metrics") or {}
    if metrics.get("formal_pass") is not True:
        raise contracts.ContractError("evaluation formal_pass is not true")
    production = challenger.get("production_candidate")
    replay = challenger.get("exact_replay")
    evaluated = evaluation.get("evaluated_production_candidate")
    if not isinstance(production, Mapping) or not isinstance(replay, Mapping) or not isinstance(evaluated, Mapping):
        raise contracts.ContractError("exact production binding missing")
    if evaluated.get("execution_semantics") != "exact_shadow_replay":
        raise contracts.ContractError("evaluation did not use exact shadow replay")
    for key in ("component", "deployment_id", "base_manifest_revision", "base_component_version", "deployment_sha256"):
        if evaluated.get(key) != production.get(key):
            raise contracts.ContractError(f"evaluation/Challenger binding mismatch: {key}")
    spec = production.get("deployment_spec")
    if not isinstance(spec, Mapping):
        raise contracts.ContractError("deployment_spec missing")
    if factory.deployment_sha256(spec) != production.get("deployment_sha256"):
        raise contracts.ContractError("deployment_spec hash mismatch")
    if str(replay.get("adapter") or "") == "":
        raise contracts.ContractError("exact replay adapter missing")
    return dict(production)


def _score(evaluation: Mapping[str, Any]) -> tuple[float, float, float, str]:
    metrics = evaluation.get("metrics") or {}
    return (
        float(metrics.get("bootstrap_lower_bound_percent") or -1e99),
        float(metrics.get("mean_incremental_net_return_percent") or -1e99),
        float(metrics.get("positive_rate") or -1e99),
        str(evaluation.get("challenger_id") or ""),
    )


def eligible_pairs(
    *,
    challengers: Sequence[Mapping[str, Any]],
    evaluations: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    config: Mapping[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    validate_config(config)
    validate_manifest(manifest)
    by_id = {str(row.get("challenger_id") or ""): dict(row) for row in challengers}
    allowed_components = {str(value) for value in config.get("allowed_components") or []}
    allowed_adapters = {str(value) for value in config.get("allowed_replay_adapters") or []}
    current_revision = int(manifest["revision"])
    rows: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []

    for raw_eval in evaluations:
        evaluation = dict(raw_eval)
        evaluator.validate_evaluation(evaluation)
        if evaluation.get("state") != "RESEARCH_PASS_AWAITING_MANUAL_ENABLEMENT":
            continue
        if (evaluation.get("metrics") or {}).get("formal_pass") is not True:
            continue
        challenger = by_id.get(str(evaluation.get("challenger_id") or ""))
        if challenger is None:
            raise contracts.ContractError("formal PASS has no matching Challenger")
        if not isinstance(challenger.get("production_candidate"), Mapping):
            continue
        if not isinstance(challenger.get("exact_replay"), Mapping):
            continue
        production = _validate_exact_binding(challenger, evaluation)
        component = str(production.get("component") or "")
        if component not in allowed_components:
            continue
        replay_adapter = str((challenger.get("exact_replay") or {}).get("adapter") or "")
        if allowed_adapters and replay_adapter not in allowed_adapters:
            continue
        if int(production.get("base_manifest_revision") or 0) != current_revision:
            continue
        component_state = (manifest.get("components") or {}).get(component) or {}
        if str(component_state.get("version") or "") != str(production.get("base_component_version") or ""):
            continue
        if component_state.get("deployment_id") == production.get("deployment_id"):
            continue
        rows.append((challenger, evaluation, production))

    rows.sort(key=lambda item: _score(item[1]), reverse=True)
    return rows


def _validate_deployment_spec(spec: Mapping[str, Any], config: Mapping[str, Any]) -> None:
    if spec.get("type") != "config_patch":
        raise contracts.ContractError("auto-promotion only supports exact config_patch deployments")
    targets = spec.get("targets")
    if not isinstance(targets, Mapping) or not targets:
        raise contracts.ContractError("deployment targets missing")
    for target, patches in targets.items():
        if target not in TARGET_FILES:
            raise contracts.ContractError(f"deployment target is not supported: {target}")
        if not isinstance(patches, list) or not patches:
            raise contracts.ContractError(f"deployment patches missing: {target}")
        allowed = _allowed_paths(config, str(target))
        for patch in patches:
            if not isinstance(patch, Mapping) or patch.get("op") != "replace":
                raise contracts.ContractError("only replace operations are permitted")
            path = patch.get("path")
            if not isinstance(path, list) or _path_key(path) not in allowed:
                raise contracts.ContractError(f"production patch path is not allowlisted: {target}:{path}")


def _validate_cross_file_invariants(documents: Mapping[str, Mapping[str, Any]]) -> None:
    gpw = documents.get("gpw_daily_config")
    policy = documents.get("stock_trading_policy")
    if gpw is not None and policy is not None:
        left = int(gpw.get("minimum_composite_score") or 0)
        right = int((((policy.get("markets") or {}).get("GPW") or {}).get("minimum_entry_score")) or 0)
        if left <= 0 or left != right:
            raise contracts.ContractError("GPW production entry thresholds became inconsistent")


def apply_one(
    *,
    challenger: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    production: Mapping[str, Any],
    production_root: Path,
    config: Mapping[str, Any],
    evidence_commit: str,
    production_base_sha: str,
    promoted_at: str | None = None,
) -> dict[str, Any]:
    validate_config(config)
    manifest_path = production_root / MANIFEST_REL
    manifest = _read(manifest_path)
    validate_manifest(manifest)
    production = _validate_exact_binding(challenger, evaluation)
    component = str(production["component"])
    if component not in {str(value) for value in config.get("allowed_components") or []}:
        raise contracts.ContractError("production component is not allowlisted")
    if int(production["base_manifest_revision"]) != int(manifest["revision"]):
        raise contracts.ContractError("stale Challenger base manifest revision")
    component_state = (manifest.get("components") or {}).get(component)
    if not isinstance(component_state, Mapping):
        raise contracts.ContractError("production component missing from manifest")
    if str(component_state.get("version") or "") != str(production["base_component_version"]):
        raise contracts.ContractError("stale Challenger base component version")

    spec = production["deployment_spec"]
    _validate_deployment_spec(spec, config)
    documents: dict[str, dict[str, Any]] = {}
    before_hashes: dict[str, str] = {}
    after_hashes: dict[str, str] = {}
    changes: list[dict[str, Any]] = []
    for target, patches in (spec.get("targets") or {}).items():
        path = production_root / TARGET_FILES[str(target)]
        document = _read(path)
        before_hashes[str(target)] = contracts.payload_sha256(document)
        documents[str(target)] = document
        for patch in patches:
            previous = _replace_path(document, patch["path"], patch.get("value"))
            changes.append({
                "target": target,
                "path": list(patch["path"]),
                "previous": previous,
                "value": copy.deepcopy(patch.get("value")),
            })
        after_hashes[str(target)] = contracts.payload_sha256(document)

    _validate_cross_file_invariants(documents)
    replay = challenger.get("exact_replay") or {}
    if str(replay.get("adapter") or "") == "entry_threshold_v1":
        expected_champion = int(replay.get("champion_threshold") or 0)
        expected_challenger = int(replay.get("challenger_threshold") or 0)
        if expected_champion <= 0 or expected_challenger <= 0:
            raise contracts.ContractError("entry threshold replay contract is incomplete")
        relevant = [
            row for row in changes
            if (row["target"], tuple(row["path"])) in {
                ("gpw_daily_config", ("minimum_composite_score",)),
                ("stock_trading_policy", ("markets", "GPW", "minimum_entry_score")),
            }
        ]
        if len(relevant) != 2:
            raise contracts.ContractError("entry promotion must patch both production thresholds")
        if any(int(row["previous"]) != expected_champion for row in relevant):
            raise contracts.ContractError("production baseline drifted from exact replay Champion")
        if any(int(row["value"]) != expected_challenger for row in relevant):
            raise contracts.ContractError("deployment patch drifted from exact replay Challenger")

    timestamp = promoted_at or contracts.iso_utc()
    promotion_identity = {
        "challenger_id": challenger["challenger_id"],
        "evaluation_sha256": evaluation["evaluation_sha256"],
        "deployment_sha256": production["deployment_sha256"],
        "base_manifest_revision": production["base_manifest_revision"],
        "evidence_commit": evidence_commit,
    }
    promotion_id = "stpromv2-" + contracts.payload_sha256(promotion_identity)[:24]
    new_manifest = copy.deepcopy(manifest)
    old_revision = int(manifest["revision"])
    new_manifest["previous_revision"] = old_revision
    new_manifest["revision"] = old_revision + 1
    new_manifest["promotion_id"] = promotion_id
    new_manifest["updated_at"] = timestamp
    new_manifest["components"][component] = {
        "version": str(spec.get("version") or production["deployment_id"]),
        "challenger_id": challenger["challenger_id"],
        "deployment_id": production["deployment_id"],
        "evidence_sha256": evaluation["evaluation_sha256"],
        "promoted_at": timestamp,
        "source": "stock-trading-v2-auto-promotion",
    }
    validate_manifest(new_manifest)

    record: dict[str, Any] = {
        "schema_version": PROMOTION_SCHEMA,
        "promotion_id": promotion_id,
        "promoted_at": timestamp,
        "component": component,
        "challenger_id": challenger["challenger_id"],
        "challenger_sha256": challenger["challenger_sha256"],
        "evaluation_sha256": evaluation["evaluation_sha256"],
        "deployment_id": production["deployment_id"],
        "deployment_sha256": production["deployment_sha256"],
        "evidence_commit": evidence_commit,
        "production_base_sha": production_base_sha,
        "manifest_revision_before": old_revision,
        "manifest_revision_after": old_revision + 1,
        "changes": changes,
        "target_hashes_before": before_hashes,
        "target_hashes_after": after_hashes,
        "governance": {
            "automatic_production_promotion": True,
            "bounded_config_patch_only": True,
            "formal_fresh_holdout_required": True,
            "exact_replay_required": True,
            "arbitrary_source_code_generation": False,
        },
    }
    record["promotion_sha256"] = contracts.payload_sha256(record)

    for target, document in documents.items():
        _atomic(production_root / TARGET_FILES[target], document)
    _atomic(manifest_path, new_manifest)
    _atomic(production_root / PROMOTION_ROOT_REL / f"{promotion_id}.json", record)
    return record


def validate_production_root(production_root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    validate_config(config)
    manifest = _read(production_root / MANIFEST_REL)
    validate_manifest(manifest)
    documents = {target: _read(production_root / path) for target, path in TARGET_FILES.items()}
    _validate_cross_file_invariants(documents)
    return {
        "ok": True,
        "manifest_revision": int(manifest["revision"]),
        "promotion_id": manifest.get("promotion_id"),
    }


def run(
    *,
    challenger_root: Path,
    evaluation_root: Path,
    production_root: Path,
    config_path: Path,
    evidence_commit: str,
    production_base_sha: str,
    apply: bool,
) -> dict[str, Any]:
    config = _read(config_path)
    validate_config(config)
    manifest = _read(production_root / MANIFEST_REL)
    challengers = _iter_json_files(challenger_root)
    evaluations = _iter_json_files(evaluation_root)
    eligible = eligible_pairs(challengers=challengers, evaluations=evaluations, manifest=manifest, config=config)
    if not eligible:
        return {
            "schema_version": "stock-trading-v2-auto-promotion-run-v1",
            "status": "NO_ELIGIBLE_PROMOTION",
            "eligible": 0,
            "applied": False,
        }
    challenger, evaluation, production = eligible[0]
    result = {
        "schema_version": "stock-trading-v2-auto-promotion-run-v1",
        "status": "ELIGIBLE",
        "eligible": len(eligible),
        "selected_challenger_id": challenger["challenger_id"],
        "selected_component": production["component"],
        "applied": False,
    }
    if not apply:
        return result
    record = apply_one(
        challenger=challenger,
        evaluation=evaluation,
        production=production,
        production_root=production_root,
        config=config,
        evidence_commit=evidence_commit,
        production_base_sha=production_base_sha,
    )
    validate_production_root(production_root, config)
    result.update({
        "status": "PROMOTED",
        "applied": True,
        "promotion_id": record["promotion_id"],
        "manifest_revision_after": record["manifest_revision_after"],
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challengers", type=Path)
    parser.add_argument("--evaluations", type=Path)
    parser.add_argument("--production-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--evidence-commit", default="")
    parser.add_argument("--production-base-sha", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify-production", action="store_true")
    args = parser.parse_args()
    config = _read(args.config)
    if args.verify_production:
        print(json.dumps(validate_production_root(args.production_root, config), indent=2, sort_keys=True))
        return 0
    if not args.challengers or not args.evaluations:
        raise SystemExit("--challengers and --evaluations are required")
    if not args.evidence_commit or not args.production_base_sha:
        raise SystemExit("--evidence-commit and --production-base-sha are required")
    result = run(
        challenger_root=args.challengers,
        evaluation_root=args.evaluations,
        production_root=args.production_root,
        config_path=args.config,
        evidence_commit=args.evidence_commit,
        production_base_sha=args.production_base_sha,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
