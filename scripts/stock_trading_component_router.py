#!/usr/bin/env python3
"""Fail-closed production router for component-level Stock Trading Champions.

Only deployments pre-registered on the production branch are executable. A
research Challenger may name a deployment_id, but cannot provide code, paths,
imports or arbitrary patches to production.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts import stock_trading_component_champion as champion
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_component_champion as champion

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "data/investments/stock_trading_component_deployments.json"
REGISTRY_SCHEMA = "stock-trading-component-deployments-v1"

# Production-owned capability map. This is deliberately narrower than the JSON
# files themselves. Safety invariants are not promotable through this router.
ALLOWED_PATHS: dict[str, dict[str, set[tuple[str, ...]]]] = {
    "gpw_daily_config": {
        "universe": {("universe",)},
        "ranking": {
            ("weights", "catalyst"),
            ("weights", "relative_momentum"),
            ("weights", "volume_liquidity"),
            ("weights", "market_context"),
            ("weights", "risk_reward"),
            ("weights", "historical_expectancy"),
            ("relative_momentum", "weights", "rank_1d"),
            ("relative_momentum", "weights", "rank_5d"),
            ("relative_momentum", "weights", "rank_20d"),
            ("relative_momentum", "weights", "rank_risk_adjusted_5d"),
            ("relative_momentum", "weights", "rank_sector_5d"),
            ("opening_confirmation", "weight"),
            ("expected_value", "score_weight"),
            ("expected_value", "uncertainty_penalty"),
        },
        "entry": {("minimum_composite_score",)},
        "meta_label": set(),
        "risk": set(),
        "portfolio": set(),
        "exit": set(),
        "regime": set(),
    },
    "stock_trading_policy": {
        "universe": set(),
        "ranking": set(),
        "entry": {
            ("markets", "GPW", "minimum_entry_score"),
            ("markets", "US", "minimum_entry_score"),
        },
        "risk": {
            ("markets", "GPW", "atr_multiple"),
            ("markets", "GPW", "risk_floor_percent"),
            ("markets", "US", "atr_multiple"),
            ("markets", "US", "risk_floor_percent"),
        },
        "exit": {
            ("markets", "GPW", "model_exit_score"),
            ("markets", "GPW", "model_reversal_from_peak"),
            ("markets", "US", "model_exit_score"),
            ("markets", "US", "model_reversal_from_peak"),
        },
        "portfolio": {
            ("markets", "GPW", "strategic_target_reward_risk"),
            ("markets", "GPW", "strong_thesis_target_reward_risk"),
            ("markets", "GPW", "exceptional_thesis_target_reward_risk"),
            ("markets", "US", "strategic_target_reward_risk"),
            ("markets", "US", "strong_thesis_target_reward_risk"),
            ("markets", "US", "exceptional_thesis_target_reward_risk"),
        },
        "meta_label": set(),
        "regime": set(),
    },
}

# Explicitly non-promotable safety policy, even if somebody later broadens a
# capability map accidentally.
FORBIDDEN_PATH_PARTS = {
    "forced_trade_allowed",
    "max_open_positions",
    "maximum_risk_percent",
    "minimum_reward_risk",
    "stop_loss_required",
    "take_profit_required",
    "data_gates",
    "minimum_median_turnover_pln",
}


class RouterError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RouterError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RouterError(f"{path} must contain a JSON object")
    return payload


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    payload = _read_json(path)
    if payload.get("schema_version") != REGISTRY_SCHEMA:
        raise RouterError("Component deployment registry schema mismatch")
    deployments = payload.get("deployments")
    if not isinstance(deployments, Mapping):
        raise RouterError("Component deployment registry missing deployments")
    for deployment_id, spec in deployments.items():
        validate_deployment(str(deployment_id), spec)
    return payload


def _path_tuple(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise RouterError("Patch path must be an array")
    path = tuple(str(part) for part in raw)
    if not path or any(not part for part in path):
        raise RouterError("Patch path cannot be empty")
    return path


def validate_deployment(deployment_id: str, spec: Any) -> None:
    if not deployment_id.strip() or not isinstance(spec, Mapping):
        raise RouterError("Invalid deployment registry entry")
    component = str(spec.get("component") or "")
    if component not in champion.COMPONENTS:
        raise RouterError(f"Deployment {deployment_id} has invalid component")
    if str(spec.get("version") or "").strip() == "":
        raise RouterError(f"Deployment {deployment_id} has no version")
    if spec.get("type") != "config_patch":
        raise RouterError(f"Deployment {deployment_id} type is not allowed")
    targets = spec.get("targets")
    if not isinstance(targets, Mapping) or not targets:
        raise RouterError(f"Deployment {deployment_id} has no targets")
    for target, patches in targets.items():
        if target not in ALLOWED_PATHS:
            raise RouterError(f"Deployment {deployment_id} target {target} is not allowed")
        if not isinstance(patches, list) or not patches:
            raise RouterError(f"Deployment {deployment_id} target {target} has no patches")
        for patch in patches:
            if not isinstance(patch, Mapping) or patch.get("op") != "replace":
                raise RouterError("Only deterministic replace patches are supported")
            path = _path_tuple(patch.get("path"))
            if any(part in FORBIDDEN_PATH_PARTS for part in path):
                raise RouterError(f"Safety path is not promotable: {'.'.join(path)}")
            if path not in ALLOWED_PATHS[target].get(component, set()):
                raise RouterError(
                    f"Component {component} cannot mutate {target}:{'.'.join(path)}"
                )
            value = patch.get("value")
            if isinstance(value, (dict, list)) and path != ("universe",):
                raise RouterError("Nested object replacement is forbidden for this path")


def resolve_deployment(deployment_id: str, *, registry_path: Path = REGISTRY_PATH) -> dict[str, Any]:
    registry = load_registry(registry_path)
    spec = (registry.get("deployments") or {}).get(deployment_id)
    if not isinstance(spec, Mapping):
        raise RouterError(f"Unknown production deployment_id: {deployment_id}")
    return dict(spec)


def _replace_existing(payload: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cursor: Any = payload
    for key in path[:-1]:
        if not isinstance(cursor, dict) or key not in cursor:
            raise RouterError(f"Patch path does not exist: {'.'.join(path)}")
        cursor = cursor[key]
    final = path[-1]
    if not isinstance(cursor, dict) or final not in cursor:
        raise RouterError(f"Patch path does not exist: {'.'.join(path)}")
    cursor[final] = deepcopy(value)


def apply_component_overrides(
    base: Mapping[str, Any],
    *,
    target: str,
    manifest_path: Path = champion.MANIFEST_PATH,
    registry_path: Path = REGISTRY_PATH,
) -> dict[str, Any]:
    if target not in ALLOWED_PATHS:
        raise RouterError(f"Unknown production target: {target}")
    result = deepcopy(dict(base))
    manifest = champion.load_manifest(manifest_path)
    registry = load_registry(registry_path)
    deployments = registry.get("deployments") or {}
    for component in champion.COMPONENTS:
        state = manifest["components"][component]
        deployment_id = state.get("deployment_id")
        if not deployment_id:
            continue
        deployment = deployments.get(deployment_id)
        if not isinstance(deployment, Mapping):
            raise RouterError(f"Active deployment missing from registry: {deployment_id}")
        validate_deployment(str(deployment_id), deployment)
        if deployment.get("component") != component:
            raise RouterError(f"Manifest/registry component mismatch for {deployment_id}")
        if deployment.get("version") != state.get("version"):
            raise RouterError(f"Manifest/registry version mismatch for {deployment_id}")
        for patch in (deployment.get("targets") or {}).get(target, []):
            _replace_existing(result, _path_tuple(patch["path"]), patch.get("value"))
    return result


def load_effective_json_config(
    base_path: Path,
    *,
    target: str,
    manifest_path: Path = champion.MANIFEST_PATH,
    registry_path: Path = REGISTRY_PATH,
) -> dict[str, Any]:
    base = _read_json(base_path)
    return apply_component_overrides(
        base,
        target=target,
        manifest_path=manifest_path,
        registry_path=registry_path,
    )
