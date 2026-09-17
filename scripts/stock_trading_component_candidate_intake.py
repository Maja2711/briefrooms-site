#!/usr/bin/env python3
"""Production-side intake for exactly evaluated research deployment proposals.

Research may propose a deterministic config_patch, but production independently
validates its hash, component ownership, bounded parameter movement, current
Champion baseline and safety invariants before the proposal is made visible to
the existing exact-binding Promotion Gate. Approval is temporary until a
promotion succeeds; only the deployment referenced by the active Champion is
persisted to the production registry.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts import stock_trading_component_bound_promotion as bound
    from scripts import stock_trading_component_champion as champion
    from scripts import stock_trading_component_promotion as promotion
    from scripts import stock_trading_component_router as router
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_component_bound_promotion as bound
    import stock_trading_component_champion as champion
    import stock_trading_component_promotion as promotion
    import stock_trading_component_router as router

ROOT = Path(__file__).resolve().parents[1]
GPW_CONFIG_PATH = ROOT / "data/investments/gpw_daily_pick_config.json"
POLICY_PATH = ROOT / "data/investments/stock_trading_policy.json"
# Daily Trading has a 1-2 session mandate. A production mutation is admitted
# only after the *same exact deployment SHA* has independently passed the fresh
# holdout on both short horizons. Longer v2 horizons remain research-only.
REQUIRED_PRODUCTION_HORIZONS = frozenset({1, 2})


class IntakeError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IntakeError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise IntakeError(f"{path} must contain a JSON object")
    return payload


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        temp = Path(handle.name)
    os.replace(temp, path)


def _path(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise IntakeError("patch path must be an array")
    result = tuple(str(part) for part in raw)
    if not result:
        raise IntakeError("patch path cannot be empty")
    return result


def _get(payload: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    cursor: Any = payload
    for key in path:
        if not isinstance(cursor, Mapping) or key not in cursor:
            raise IntakeError(f"baseline path missing: {'.'.join(path)}")
        cursor = cursor[key]
    return cursor


def _replace(payload: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cursor: Any = payload
    for key in path[:-1]:
        if not isinstance(cursor, dict) or key not in cursor:
            raise IntakeError(f"baseline path missing: {'.'.join(path)}")
        cursor = cursor[key]
    final = path[-1]
    if not isinstance(cursor, dict) or final not in cursor:
        raise IntakeError(f"baseline path missing: {'.'.join(path)}")
    cursor[final] = copy.deepcopy(value)


def _finite_number(value: Any, *, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise IntakeError(f"{label} must be numeric") from exc
    if not math.isfinite(numeric):
        raise IntakeError(f"{label} must be finite")
    return numeric


def _bounded_delta(old: Any, new: Any, *, maximum: float, low: float | None = None, high: float | None = None, label: str) -> None:
    old_n = _finite_number(old, label=f"{label} baseline")
    new_n = _finite_number(new, label=label)
    if abs(new_n - old_n) > maximum + 1e-12:
        raise IntakeError(f"{label} exceeds maximum per-promotion delta {maximum}")
    if low is not None and new_n < low:
        raise IntakeError(f"{label} below production intake floor {low}")
    if high is not None and new_n > high:
        raise IntakeError(f"{label} above production intake ceiling {high}")


def validate_bounded_deployment(spec: Mapping[str, Any], *, gpw_config: Mapping[str, Any], policy: Mapping[str, Any]) -> None:
    component = str(spec.get("component") or "")
    if component in {"universe", "meta_label", "regime"}:
        raise IntakeError(f"automatic intake for component {component} is not enabled")
    targets = spec.get("targets") or {}
    patch_count = sum(len(patches or []) for patches in targets.values())
    if patch_count < 1 or patch_count > 6:
        raise IntakeError("deployment patch count outside automatic intake bounds")

    effective_gpw = copy.deepcopy(dict(gpw_config))
    effective_policy = copy.deepcopy(dict(policy))
    for target, patches in targets.items():
        base = gpw_config if target == "gpw_daily_config" else policy if target == "stock_trading_policy" else None
        effective = effective_gpw if target == "gpw_daily_config" else effective_policy if target == "stock_trading_policy" else None
        if base is None or effective is None:
            raise IntakeError(f"unsupported automatic intake target: {target}")
        for patch in patches or []:
            path = _path(patch.get("path"))
            old = _get(base, path)
            new = patch.get("value")
            label = f"{target}:{'.'.join(path)}"
            if path in {("minimum_composite_score",), ("markets", "GPW", "minimum_entry_score"), ("markets", "US", "minimum_entry_score")}:
                _bounded_delta(old, new, maximum=3.0, low=60.0, high=85.0, label=label)
            elif len(path) == 2 and path[0] == "weights":
                _bounded_delta(old, new, maximum=5.0, low=0.0, high=50.0, label=label)
            elif len(path) == 3 and path[:2] == ("relative_momentum", "weights"):
                _bounded_delta(old, new, maximum=0.10, low=0.0, high=1.0, label=label)
            elif path == ("opening_confirmation", "weight"):
                _bounded_delta(old, new, maximum=0.05, low=0.0, high=0.50, label=label)
            elif path == ("expected_value", "score_weight"):
                _bounded_delta(old, new, maximum=0.05, low=0.0, high=0.50, label=label)
            elif path == ("expected_value", "uncertainty_penalty"):
                _bounded_delta(old, new, maximum=0.10, low=0.0, high=1.0, label=label)
            elif path[-1] == "atr_multiple":
                _bounded_delta(old, new, maximum=0.15, low=0.5, high=2.0, label=label)
            elif path[-1] == "risk_floor_percent":
                _bounded_delta(old, new, maximum=0.002, low=0.005, high=0.03, label=label)
            elif path[-1] in {"model_exit_score", "model_reversal_from_peak"}:
                _bounded_delta(old, new, maximum=5.0, low=0.0, high=100.0, label=label)
            elif path[-1] in {"strategic_target_reward_risk", "strong_thesis_target_reward_risk", "exceptional_thesis_target_reward_risk"}:
                _bounded_delta(old, new, maximum=0.5, low=1.5, high=6.0, label=label)
            else:
                raise IntakeError(f"automatic intake has no bounded rule for {label}")
            _replace(effective, path, new)

    weights = effective_gpw.get("weights") or {}
    if weights and abs(sum(float(value) for value in weights.values()) - 100.0) > 1e-9:
        raise IntakeError("candidate GPW ranking weights do not sum to 100")
    rm_weights = ((effective_gpw.get("relative_momentum") or {}).get("weights") or {})
    if rm_weights and abs(sum(float(value) for value in rm_weights.values()) - 1.0) > 1e-9:
        raise IntakeError("candidate relative-momentum weights do not sum to 1")
    if effective_policy.get("forced_trade_allowed") is not False:
        raise IntakeError("candidate would violate forced_trade_allowed invariant")
    for market in ("GPW", "US"):
        cfg = ((effective_policy.get("markets") or {}).get(market) or {})
        if int(cfg.get("max_open_positions") or 0) != 3:
            raise IntakeError(f"candidate would violate {market} max_open_positions invariant")
        if float(cfg.get("minimum_reward_risk") or 0) < 1.5:
            raise IntakeError(f"candidate would violate {market} minimum_reward_risk invariant")
        if float(cfg.get("maximum_risk_percent") or 1) > 0.07:
            raise IntakeError(f"candidate would violate {market} maximum_risk_percent invariant")


def approve_proposals(*, challengers: Path, evaluations: Path, registry_path: Path, manifest_path: Path, gpw_config_path: Path, policy_path: Path, output_path: Path) -> dict[str, Any]:
    registry = router.load_registry(registry_path)
    approved = copy.deepcopy(registry)
    manifest = champion.load_manifest(manifest_path)
    gpw = _read(gpw_config_path)
    policy = _read(policy_path)
    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    proposals: dict[str, dict[str, Any]] = {}
    passed_horizons: dict[str, set[int]] = {}
    proposal_challengers: dict[str, list[str]] = {}

    for evaluation_path in sorted(evaluations.glob("*.json")) if evaluations.exists() else []:
        evaluation = _read(evaluation_path)
        challenger_id = str(evaluation.get("challenger_id") or "")
        challenger_path = challengers / f"{challenger_id}.json"
        if not challenger_path.exists():
            continue
        challenger = _read(challenger_path)
        try:
            promotion.validate_research_pair(challenger, evaluation)
            horizon = int(challenger.get("horizon_sessions") or 0)
            if horizon not in REQUIRED_PRODUCTION_HORIZONS:
                raise IntakeError("formal PASS horizon is outside the 1-2 session production mandate")
            candidate = challenger.get("production_candidate")
            evaluated = evaluation.get("evaluated_production_candidate")
            if not isinstance(candidate, Mapping) or not isinstance(evaluated, Mapping):
                raise IntakeError("exact production candidate binding missing")
            if evaluated.get("execution_semantics") != bound.EXACT_SEMANTICS:
                raise IntakeError("evaluation did not use exact_shadow_replay semantics")
            for key in bound.REQUIRED_CANDIDATE_FIELDS:
                if str(candidate.get(key)) != str(evaluated.get(key)):
                    raise IntakeError(f"evaluated candidate mismatch: {key}")
            spec = candidate.get("deployment_spec")
            if not isinstance(spec, Mapping):
                raise IntakeError("production candidate deployment_spec missing")
            deployment_id = str(candidate.get("deployment_id") or "")
            router.validate_deployment(deployment_id, spec)
            actual_sha = bound.deployment_sha256(spec)
            if actual_sha != candidate.get("deployment_sha256") or actual_sha != evaluated.get("deployment_sha256"):
                raise IntakeError("proposed deployment SHA does not match exact evaluation")
            component = str(candidate.get("component") or "")
            if str(spec.get("component") or "") != component:
                raise IntakeError("proposal component mismatch")
            if int(candidate.get("base_manifest_revision") or 0) != int(manifest["revision"]):
                raise IntakeError("proposal is stale against current Champion revision")
            if str(candidate.get("base_component_version") or "") != str(manifest["components"][component]["version"]):
                raise IntakeError("proposal is stale against current component version")
            validate_bounded_deployment(spec, gpw_config=gpw, policy=policy)
            previous = proposals.get(deployment_id)
            if previous is not None and previous != dict(spec):
                raise IntakeError("deployment_id collision with different exact specs across horizons")
            proposals[deployment_id] = copy.deepcopy(dict(spec))
            passed_horizons.setdefault(deployment_id, set()).add(horizon)
            proposal_challengers.setdefault(deployment_id, []).append(challenger_id)
        except (promotion.PromotionError, router.RouterError, IntakeError, KeyError, ValueError, TypeError) as exc:
            rejected.append({"challenger_id": challenger_id, "reason": str(exc)})

    for deployment_id in sorted(proposals):
        missing = REQUIRED_PRODUCTION_HORIZONS - passed_horizons.get(deployment_id, set())
        if missing:
            rejected.append({
                "challenger_id": ",".join(sorted(proposal_challengers.get(deployment_id) or [])),
                "reason": "missing exact fresh-holdout PASS for production horizon(s): " + ",".join(str(value) for value in sorted(missing)),
            })
            continue
        spec = proposals[deployment_id]
        existing = (approved.get("deployments") or {}).get(deployment_id)
        if existing is not None and existing != spec:
            rejected.append({
                "challenger_id": ",".join(sorted(proposal_challengers.get(deployment_id) or [])),
                "reason": "deployment_id collision with different production spec",
            })
            continue
        approved.setdefault("deployments", {})[deployment_id] = copy.deepcopy(spec)
        accepted.append(deployment_id)

    _atomic(output_path, approved)
    return {"accepted": accepted, "rejected": rejected, "output_registry": str(output_path)}


def persist_active(*, approved_registry_path: Path, registry_path: Path, manifest_path: Path) -> dict[str, Any]:
    approved = router.load_registry(approved_registry_path)
    current = router.load_registry(registry_path)
    manifest = champion.load_manifest(manifest_path)
    active_ids = {
        str(state.get("deployment_id"))
        for state in (manifest.get("components") or {}).values()
        if state.get("deployment_id")
    }
    added: list[str] = []
    for deployment_id in sorted(active_ids):
        if deployment_id in (current.get("deployments") or {}):
            continue
        spec = (approved.get("deployments") or {}).get(deployment_id)
        if not isinstance(spec, Mapping):
            raise IntakeError(f"active deployment missing from approved registry: {deployment_id}")
        router.validate_deployment(deployment_id, spec)
        current.setdefault("deployments", {})[deployment_id] = copy.deepcopy(dict(spec))
        added.append(deployment_id)
    if added:
        _atomic(registry_path, current)
    return {"persisted": added}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challengers", type=Path)
    parser.add_argument("--evaluations", type=Path)
    parser.add_argument("--registry", type=Path, default=router.REGISTRY_PATH)
    parser.add_argument("--manifest", type=Path, default=champion.MANIFEST_PATH)
    parser.add_argument("--gpw-config", type=Path, default=GPW_CONFIG_PATH)
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--output-registry", type=Path)
    parser.add_argument("--persist-active", action="store_true")
    parser.add_argument("--approved-registry", type=Path)
    args = parser.parse_args()
    if args.persist_active:
        if args.approved_registry is None:
            parser.error("--approved-registry is required with --persist-active")
        result = persist_active(approved_registry_path=args.approved_registry, registry_path=args.registry, manifest_path=args.manifest)
    else:
        if args.challengers is None or args.evaluations is None or args.output_registry is None:
            parser.error("--challengers, --evaluations and --output-registry are required")
        result = approve_proposals(
            challengers=args.challengers,
            evaluations=args.evaluations,
            registry_path=args.registry,
            manifest_path=args.manifest,
            gpw_config_path=args.gpw_config,
            policy_path=args.policy,
            output_path=args.output_registry,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
