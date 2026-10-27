#!/usr/bin/env python3
"""Automatic evidence -> component Champion promotion bridge.

Research evidence can nominate only a production-owned deployment_id. This
bridge revalidates the immutable Challenger/evaluation pair, checks that the
candidate was tested against the current component baseline, promotes one
component atomically, verifies effective production configs and rolls back on
any health failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from scripts import stock_trading_component_champion as champion
    from scripts import stock_trading_component_router as router
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_component_champion as champion
    import stock_trading_component_router as router

ROOT = Path(__file__).resolve().parents[1]
GPW_CONFIG_PATH = ROOT / "data/investments/gpw_daily_pick_config.json"
POLICY_PATH = ROOT / "data/investments/stock_trading_policy.json"
REQUIRED_PRODUCTION_HORIZONS = frozenset({1, 2})


class PromotionError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PromotionError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PromotionError(f"{path} must contain a JSON object")
    return payload


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        temp = Path(handle.name)
    os.replace(temp, path)


def _hash_without(payload: Mapping[str, Any], field: str) -> str:
    body = dict(payload)
    body.pop(field, None)
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_research_pair(challenger: Mapping[str, Any], evaluation: Mapping[str, Any], *,
                           source_root: Path | None = None, frames_root: Path | None = None,
                           trials_path: Path | None = None,
                           current_policy: Mapping[str, Any] | None = None) -> None:
    if challenger.get("schema_version") != "stock-trading-v2-challenger-policy-v1":
        raise PromotionError("Unsupported Challenger schema")
    if evaluation.get("schema_version") != "stock-trading-v2-challenger-evaluation-v1":
        raise PromotionError("Unsupported evaluation schema")
    if challenger.get("challenger_id") != evaluation.get("challenger_id"):
        raise PromotionError("Challenger/evaluation identity mismatch")
    if challenger.get("component") != evaluation.get("component"):
        raise PromotionError("Challenger/evaluation research component mismatch")
    if _hash_without(challenger, "challenger_sha256") != challenger.get("challenger_sha256"):
        raise PromotionError("Challenger integrity hash mismatch")
    if _hash_without(evaluation, "evaluation_sha256") != evaluation.get("evaluation_sha256"):
        raise PromotionError("Evaluation integrity hash mismatch")
    metrics = evaluation.get("metrics") or {}
    if metrics.get("formal_pass") is not True:
        raise PromotionError("Evaluation has not passed the formal holdout")
    if not str(evaluation.get("state") or "").startswith("RESEARCH_PASS"):
        raise PromotionError("Evaluation state is not a research PASS")
    from scripts import stock_trading_component_evidence as evidence
    try:
        evidence.validate_proof(
            challenger, evaluation, source_root=source_root or evidence.DATA,
            frames_root=frames_root, trials_path=trials_path or evidence.TRIALS_PATH,
            current_policy=current_policy,
        )
    except (evidence.EvidenceError, ValueError, KeyError, TypeError, OSError) as exc:
        raise PromotionError("Independent production proof rejected: " + str(exc)) from exc


def production_candidate(challenger: Mapping[str, Any]) -> dict[str, Any] | None:
    candidate = challenger.get("production_candidate")
    if not isinstance(candidate, Mapping):
        return None
    required = ("component", "deployment_id", "base_manifest_revision", "base_component_version")
    if any(not str(candidate.get(key) if key not in {"base_manifest_revision"} else candidate.get(key)) for key in required):
        return None
    component = str(candidate.get("component") or "")
    if component not in champion.COMPONENTS:
        return None
    return dict(candidate)


def health_check(
    *,
    manifest_path: Path,
    registry_path: Path,
    gpw_config_path: Path = GPW_CONFIG_PATH,
    policy_path: Path = POLICY_PATH,
) -> dict[str, Any]:
    gpw = router.load_effective_json_config(
        gpw_config_path,
        target="gpw_daily_config",
        manifest_path=manifest_path,
        registry_path=registry_path,
    )
    if not isinstance(gpw.get("universe"), list) or not gpw["universe"]:
        raise PromotionError("Effective GPW universe is empty")
    weights = gpw.get("weights") or {}
    if abs(sum(float(value) for value in weights.values()) - 100.0) > 1e-9:
        raise PromotionError("Effective GPW ranking weights do not sum to 100")
    if float(gpw.get("minimum_reward_risk") or 0) < 1.5:
        raise PromotionError("Effective GPW minimum_reward_risk safety invariant violated")
    if float(gpw.get("maximum_risk_percent") or 1) > 0.07:
        raise PromotionError("Effective GPW maximum_risk_percent safety invariant violated")

    policy = router.load_effective_json_config(
        policy_path,
        target="stock_trading_policy",
        manifest_path=manifest_path,
        registry_path=registry_path,
    )
    if policy.get("forced_trade_allowed") is not False:
        raise PromotionError("Effective policy forced_trade_allowed invariant violated")
    for market in ("GPW", "US"):
        cfg = (policy.get("markets") or {}).get(market) or {}
        if int(cfg.get("max_open_positions") or 0) != 3:
            raise PromotionError(f"Effective {market} max_open_positions invariant violated")
        if float(cfg.get("minimum_reward_risk") or 0) < 1.5:
            raise PromotionError(f"Effective {market} minimum_reward_risk invariant violated")
        if float(cfg.get("maximum_risk_percent") or 1) > 0.07:
            raise PromotionError(f"Effective {market} maximum_risk_percent invariant violated")
    return {"ok": True, "champion_revision": champion.load_manifest(manifest_path)["revision"]}


def materialize_active_components(
    *,
    manifest_path: Path,
    registry_path: Path,
    gpw_config_path: Path = GPW_CONFIG_PATH,
    policy_path: Path = POLICY_PATH,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Materialize the active composite Champion into production config files.

    Existing files may already contain earlier promoted values; replace patches
    are idempotent, so rebuilding the composite remains deterministic.
    """
    gpw_base = _read_json(gpw_config_path)
    policy_base = _read_json(policy_path)
    gpw_effective = router.apply_component_overrides(
        gpw_base, target="gpw_daily_config", manifest_path=manifest_path, registry_path=registry_path
    )
    policy_effective = router.apply_component_overrides(
        policy_base, target="stock_trading_policy", manifest_path=manifest_path, registry_path=registry_path
    )
    _atomic_json(gpw_config_path, gpw_effective)
    _atomic_json(policy_path, policy_effective)
    return gpw_base, policy_base


def _candidate_paths(evaluations: Path) -> Iterable[Path]:
    return sorted(evaluations.glob("*.json")) if evaluations.exists() else []


def run(
    *,
    challengers: Path,
    evaluations: Path,
    manifest_path: Path = champion.MANIFEST_PATH,
    registry_path: Path = router.REGISTRY_PATH,
    history_dir: Path = champion.HISTORY_DIR,
    audit_path: Path = champion.AUDIT_PATH,
    gpw_config_path: Path = GPW_CONFIG_PATH,
    policy_path: Path = POLICY_PATH,
    dry_run: bool = False,
    source_root: Path | None = None,
    frames_root: Path | None = None,
    trials_path: Path | None = None,
    monitor_root: Path | None = None,
    portfolio_path: Path | None = None,
    bridge_config_path: Path | None = None,
    runtime_path: Path | None = None,
) -> dict[str, Any]:
    current = champion.load_manifest(manifest_path)
    registry = router.load_registry(registry_path)
    effective_policy = router.load_effective_json_config(
        policy_path, target="stock_trading_policy", manifest_path=manifest_path, registry_path=registry_path,
    )
    skipped: list[dict[str, str]] = []
    eligible: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    baseline_sha256 = hashlib.sha256(json.dumps(
        {"manifest": current, "policy": effective_policy}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    passed_horizons: dict[tuple[str, str, str], set[int]] = {}

    for evaluation_path in _candidate_paths(evaluations):
        evaluation = _read_json(evaluation_path)
        challenger_id = str(evaluation.get("challenger_id") or "")
        challenger_path = challengers / f"{challenger_id}.json"
        if not challenger_path.exists():
            skipped.append({"challenger_id": challenger_id, "reason": "challenger_missing"})
            continue
        challenger = _read_json(challenger_path)
        from scripts import stock_trading_component_bound_promotion as bound
        try:
            validate_research_pair(challenger, evaluation, source_root=source_root, frames_root=frames_root,
                                   trials_path=trials_path, current_policy=effective_policy)
            bound.validate_exact_binding(challenger, evaluation, registry)
        except (PromotionError, bound.EvidenceBindingError, router.RouterError) as exc:
            skipped.append({"challenger_id": challenger_id, "reason": str(exc)})
            continue
        candidate = production_candidate(challenger)
        if candidate is None:
            skipped.append({"challenger_id": challenger_id, "reason": "PASS_NOT_DEPLOYABLE"})
            continue
        deployment_id = str(candidate["deployment_id"])
        deployment = (registry.get("deployments") or {}).get(deployment_id)
        if not isinstance(deployment, Mapping):
            skipped.append({"challenger_id": challenger_id, "reason": "deployment_not_approved_on_main"})
            continue
        component = str(candidate["component"])
        if deployment.get("component") != component:
            skipped.append({"challenger_id": challenger_id, "reason": "deployment_component_mismatch"})
            continue
        if int(candidate["base_manifest_revision"]) != int(current["revision"]):
            skipped.append({"challenger_id": challenger_id, "reason": "stale_manifest_revision"})
            continue
        if str(candidate["base_component_version"]) != str(current["components"][component]["version"]):
            skipped.append({"challenger_id": challenger_id, "reason": "stale_component_version"})
            continue
        if current["components"][component].get("deployment_id") == deployment_id:
            skipped.append({"challenger_id": challenger_id, "reason": "already_active"})
            continue
        try:
            horizon = int(challenger.get("horizon_sessions") or 0)
        except (TypeError, ValueError):
            horizon = 0
        if horizon not in REQUIRED_PRODUCTION_HORIZONS:
            skipped.append({"challenger_id": challenger_id, "reason": "formal_PASS_horizon_outside_1_2_session_mandate"})
            continue
        key = (deployment_id, str(candidate["deployment_sha256"]), baseline_sha256)
        passed_horizons.setdefault(key, set()).add(horizon)
        eligible.append((challenger, evaluation, candidate, dict(deployment)))

    # Registry approval persists across generations. It cannot substitute for
    # two independently verified horizon proofs against this complete baseline.
    complete = []
    for item in eligible:
        candidate = item[2]
        key = (str(candidate["deployment_id"]), str(candidate["deployment_sha256"]), baseline_sha256)
        missing = REQUIRED_PRODUCTION_HORIZONS - passed_horizons[key]
        if missing:
            skipped.append({"challenger_id": str(item[0]["challenger_id"]),
                            "reason": "missing exact fresh-holdout PASS for production horizon(s): " + ",".join(str(value) for value in sorted(missing))})
        else:
            complete.append(item)
    eligible = complete

    if not eligible:
        return {"status": "NO_PROMOTION", "revision": current["revision"], "skipped": skipped}

    # At most one production mutation per run. This keeps attribution and rollback
    # unambiguous; the next run can promote another independently proven module.
    eligible.sort(key=lambda item: (str(item[1].get("generated_at") or ""), str(item[0]["challenger_id"])))
    challenger, evaluation, candidate, deployment = eligible[0]
    component = str(candidate["component"])
    deployment_id = str(candidate["deployment_id"])
    version = str(deployment["version"])
    promotion_id = "stprom-" + hashlib.sha256(
        f"{challenger['challenger_id']}:{evaluation['evaluation_sha256']}:{current['revision']}:{deployment_id}".encode("utf-8")
    ).hexdigest()[:24]

    if dry_run:
        return {
            "status": "DRY_RUN_PROMOTION_READY",
            "component": component,
            "deployment_id": deployment_id,
            "version": version,
            "from_revision": current["revision"],
            "to_revision": int(current["revision"]) + 1,
            "promotion_id": promotion_id,
            "skipped": skipped,
        }

    gpw_before = _read_json(gpw_config_path)
    policy_before = _read_json(policy_path)
    prior, promoted = champion.promote_component(
        component=component,
        version=version,
        deployment_id=deployment_id,
        challenger_id=str(challenger["challenger_id"]),
        evidence_sha256=str(evaluation["evaluation_sha256"]),
        promotion_id=promotion_id,
        manifest_path=manifest_path,
        history_dir=history_dir,
        audit_path=audit_path,
        expected_revision=int(current["revision"]),
    )
    try:
        materialize_active_components(
            manifest_path=manifest_path,
            registry_path=registry_path,
            gpw_config_path=gpw_config_path,
            policy_path=policy_path,
        )
        health = health_check(
            manifest_path=manifest_path,
            registry_path=registry_path,
            gpw_config_path=gpw_config_path,
            policy_path=policy_path,
        )
        from scripts import stock_trading_component_monitor as monitor
        from scripts import stock_trading_v2_production_bridge as bridge
        monitored = monitor.register_activation(
            previous_policy=effective_policy, previous_manifest=prior,
            activated_policy=_read_json(policy_path), activated_manifest=promoted,
            initial_state=_read_json(portfolio_path or bridge.PORTFOLIO_PATH),
            bridge_config=_read_json(bridge_config_path or bridge.CONFIG_PATH),
            runtime=_read_json(runtime_path or bridge.STATE_PATH),
            root=monitor_root or monitor.MONITOR_ROOT,
        )
    except Exception as exc:
        _atomic_json(gpw_config_path, gpw_before)
        _atomic_json(policy_path, policy_before)
        champion.restore_manifest(
            prior,
            failed_revision=int(promoted["revision"]),
            reason=f"post_promotion_health_check_failed:{type(exc).__name__}:{exc}",
            manifest_path=manifest_path,
            audit_path=audit_path,
        )
        return {
            "status": "AUTO_ROLLBACK",
            "component": component,
            "failed_revision": promoted["revision"],
            "restored_revision": prior["revision"],
            "reason": str(exc),
        }
    return {
        "status": "PROMOTED",
        "component": component,
        "deployment_id": deployment_id,
        "version": version,
        "from_revision": prior["revision"],
        "to_revision": promoted["revision"],
        "promotion_id": promotion_id,
        "health": health,
        "monitoring": monitored,
        "skipped": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challengers", type=Path, required=True)
    parser.add_argument("--evaluations", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=champion.MANIFEST_PATH)
    parser.add_argument("--registry", type=Path, default=router.REGISTRY_PATH)
    parser.add_argument("--history", type=Path, default=champion.HISTORY_DIR)
    parser.add_argument("--audit", type=Path, default=champion.AUDIT_PATH)
    parser.add_argument("--gpw-config", type=Path, default=GPW_CONFIG_PATH)
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--frames-root", type=Path)
    parser.add_argument("--trials", type=Path)
    parser.add_argument("--monitor-root", type=Path)
    parser.add_argument("--portfolio", type=Path)
    parser.add_argument("--bridge-config", type=Path)
    parser.add_argument("--runtime", type=Path)
    args = parser.parse_args()
    result = run(
        challengers=args.challengers,
        evaluations=args.evaluations,
        manifest_path=args.manifest,
        registry_path=args.registry,
        history_dir=args.history,
        audit_path=args.audit,
        gpw_config_path=args.gpw_config,
        policy_path=args.policy,
        dry_run=args.dry_run,
        source_root=args.source_root, frames_root=args.frames_root, trials_path=args.trials,
        monitor_root=args.monitor_root, portfolio_path=args.portfolio,
        bridge_config_path=args.bridge_config, runtime_path=args.runtime,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
