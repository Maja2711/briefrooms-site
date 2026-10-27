#!/usr/bin/env python3
"""Main-owned prospective economics and bounded entry-component rollback.

The calling workflow shares the canonical stock-state writer concurrency group.
This module never opens, closes, settles or writes a trading portfolio.
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import stock_trading_component_champion as champion
    from scripts import stock_trading_portfolio as portfolio
    from scripts import stock_trading_portfolio_analytics as analytics
    from scripts import stock_trading_v2_production_replay as replay
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_component_champion as champion
    import stock_trading_portfolio as portfolio
    import stock_trading_portfolio_analytics as analytics
    import stock_trading_v2_production_replay as replay

ROOT = Path(__file__).resolve().parents[1]
MONITOR_ROOT = ROOT / "data/investments/stock_trading_component_monitor"
SCHEMA = "stock-trading-component-economic-deployment-v1"


class MonitorError(ValueError):
    pass


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise MonitorError("monitor input must be a JSON object")
    return payload


def _append_once(path: Path, body: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(body, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    except FileExistsError:
        if _read(path) != dict(body):
            raise MonitorError("immutable economic monitoring receipt conflict")


def _activation(raw: Mapping[str, Any]) -> dict[str, Any]:
    body = deepcopy(dict(raw))
    digest = body.pop("activation_sha256", None)
    if body.get("schema_version") != SCHEMA or digest != replay.payload_sha256(body):
        raise MonitorError("economic activation receipt invalid")
    return deepcopy(dict(raw))


def register_activation(
    *, previous_policy: Mapping[str, Any], previous_manifest: Mapping[str, Any],
    activated_policy: Mapping[str, Any], activated_manifest: Mapping[str, Any],
    initial_state: Mapping[str, Any], bridge_config: Mapping[str, Any],
    runtime: Mapping[str, Any] | None = None, market: str | None = None,
    activated_at: str | datetime | None = None, root: Path = MONITOR_ROOT,
) -> dict[str, Any]:
    """Freeze the fixed future monitoring contract before releasing writer lock.

    The default boundary is this receipt's real creation time, after promotion
    health checks. Production must remain serialized until this returns. No
    market input preceding this receipt is eligible for monitoring evidence.
    """
    champion.validate_manifest(previous_manifest)
    champion.validate_manifest(activated_manifest)
    changed = [name for name in champion.COMPONENTS if previous_manifest["components"][name] != activated_manifest["components"][name]]
    if changed != ["entry"] or int(activated_manifest["revision"]) != int(previous_manifest["revision"]) + 1:
        raise MonitorError("monitor activation requires exactly one entry-component promotion")
    changed_markets = [name for name in ("GPW", "US") if previous_policy["markets"][name].get("minimum_cash_edge_points") != activated_policy["markets"][name].get("minimum_cash_edge_points")]
    if len(changed_markets) != 1 or market is not None and market.upper() != changed_markets[0]:
        raise MonitorError("economic monitoring requires one market CASH-edge mutation")
    market = changed_markets[0]
    old_edge = analytics._number(previous_policy["markets"][market].get("minimum_cash_edge_points"))
    new_edge = analytics._number(activated_policy["markets"][market].get("minimum_cash_edge_points"))
    expected = deepcopy(dict(previous_policy))
    expected["markets"][market]["minimum_cash_edge_points"] = new_edge
    if old_edge is None or new_edge is None or abs(old_edge - new_edge) != 1 or not 1 <= old_edge <= 15 or not 1 <= new_edge <= 15 or expected != dict(activated_policy):
        raise MonitorError("economic monitoring cannot absorb other policy changes")
    component = activated_manifest["components"]["entry"]
    if not component.get("deployment_id"):
        raise MonitorError("promoted deployment identity missing")
    now = activated_at if isinstance(activated_at, datetime) else replay._time(activated_at) if activated_at is not None else datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise MonitorError("activation timestamp must be timezone-aware")
    costs = {market: activated_policy["markets"][market].get("transaction_costs")}
    portfolio.transaction_cost(5000, costs[market] or {})
    body = {
        "schema_version": SCHEMA, "authority": "MAIN_PRODUCTION_ENTRY_COMPONENT_ONLY",
        "deployment_id": str(component["deployment_id"]), "component": "entry", "market": market,
        "activated_at": replay._iso(now), "activation_revision": int(activated_manifest["revision"]),
        "policy_sha256": replay.payload_sha256(activated_policy), "cost_model_sha256": replay.payload_sha256(costs),
        "production_code_sha256": replay.production_code_sha256(),
        "previous_policy": deepcopy(dict(previous_policy)), "previous_manifest": deepcopy(dict(previous_manifest)),
        "activated_policy": deepcopy(dict(activated_policy)), "activated_manifest": deepcopy(dict(activated_manifest)),
        "initial_state": deepcopy(dict(initial_state)), "bridge_config": deepcopy(dict(bridge_config)),
        "runtime": deepcopy(dict(runtime or {"phase": "FULL"})),
        "monitoring_contract": {
            "frozen_at": replay._iso(now), "window_end_at": replay._iso(now + timedelta(days=30)),
            "initial_capital": float(activated_policy["markets"][market]["max_open_positions"]) * 5000,
            "minimum_paired_observations": 2, "rollback_max_drawdown_percent": 5.0,
            "rollback_min_net_delta_percent": -1.0, "maximum_terminal_observation_age_hours": 96,
        },
    }
    digest = replay.payload_sha256(body)
    payload = {**body, "activation_sha256": digest}
    path = Path(root) / "deployments" / f"{digest}.json"
    _append_once(path, payload)
    return {"status": "MONITORING_REGISTERED", "monitor_id": digest, "path": str(path), "activation": payload}


def _native_frames(root: Path, start: datetime, end: datetime) -> list[dict[str, Any]]:
    selected = []
    for path in sorted(Path(root).rglob("*.json")):
        frame = _read(path)
        if frame.get("schema_version") != replay.FRAME_SCHEMA:
            continue
        # A later persisted file cannot masquerade as an earlier observation.
        observed, recorded = replay._time(frame["observed_at"]), replay._time(frame["recorded_at"])
        if start < observed <= end and start <= recorded <= end:
            selected.append(frame)
    return sorted(selected, key=lambda row: replay._time(row["observed_at"]))


def _current_matches(deployment: Mapping[str, Any], manifest: Mapping[str, Any], policy: Mapping[str, Any]) -> bool:
    return (dict(manifest) == deployment["activated_manifest"]
            and replay.payload_sha256(policy) == deployment["policy_sha256"]
            and (manifest.get("components") or {}).get("entry", {}).get("deployment_id") == deployment["deployment_id"])


def _rollback(
    deployment: Mapping[str, Any], decision: Mapping[str, Any], *, now: datetime,
    manifest_path: Path, policy_path: Path, history_dir: Path, audit_path: Path,
) -> dict[str, Any]:
    current, policy = champion.load_manifest(manifest_path), _read(policy_path)
    if not _current_matches(deployment, current, policy):
        raise MonitorError("economic rollback rejected active revision or policy race")
    if replay.production_code_sha256() != deployment["production_code_sha256"]:
        raise MonitorError("economic rollback rejected runtime code drift")
    market = str(deployment["market"])
    restored_policy = deepcopy(policy)
    restored_policy["markets"][market]["minimum_cash_edge_points"] = deployment["previous_policy"]["markets"][market]["minimum_cash_edge_points"]
    restored = deepcopy(current)
    restored.update(revision=int(current["revision"]) + 1, previous_revision=current["revision"], updated_at=replay._iso(now),
                    promotion_id="economic-rollback-" + str(deployment["activation_sha256"])[:24], rollback_monitor_id=deployment["activation_sha256"])
    restored["components"]["entry"] = deepcopy(deployment["previous_manifest"]["components"]["entry"])
    champion.validate_manifest(restored)
    champion.snapshot_manifest(current, history_dir)
    # The workflow serializes all production writers. Re-read both authorities
    # immediately before replacing either file, then restore on write failure.
    if champion.load_manifest(manifest_path) != current or _read(policy_path) != policy:
        raise MonitorError("economic rollback rejected concurrent writer")
    event = {"event": "ECONOMIC_COMPONENT_ROLLBACK", "at": replay._iso(now), "monitor_id": deployment["activation_sha256"],
             "deployment_id": deployment["deployment_id"], "component": "entry", "market": market,
             "from_revision": current["revision"], "to_revision": restored["revision"],
             "old_component": current["components"]["entry"], "new_component": restored["components"]["entry"],
             "reason": decision["reason"], "economic_decision_sha256": replay.payload_sha256(decision),
             "future_only": True, "portfolio_mutated": False}
    champion._append_audit(audit_path, {**event, "event": "ECONOMIC_COMPONENT_ROLLBACK_PREPARED"})
    try:
        portfolio._atomic(policy_path, restored_policy)
        champion.save_manifest(restored, manifest_path)
        champion._append_audit(audit_path, event)
    except Exception:
        portfolio._atomic(policy_path, policy)
        champion.save_manifest(current, manifest_path)
        raise
    return {"status": "ROLLED_BACK", "revision": restored["revision"], "event": event}


def run(
    *, root: Path = MONITOR_ROOT, frames_root: Path = replay.DEFAULT_FRAME_ROOT,
    manifest_path: Path = champion.MANIFEST_PATH, policy_path: Path = portfolio.POLICY_PATH,
    history_dir: Path = champion.HISTORY_DIR, audit_path: Path = champion.AUDIT_PATH,
    as_of: datetime | None = None, dry_run: bool = False,
) -> dict[str, Any]:
    """Replay future paired books and materialize only predeclared entry rollback."""
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise MonitorError("monitor as_of must be timezone-aware")
    results = []
    for path in sorted((Path(root) / "deployments").glob("*.json")):
        deployment = _activation(_read(path))
        identity = str(deployment["activation_sha256"])
        status_path = Path(root) / "status" / f"{identity}.json"
        prior_status = _read(status_path) if status_path.exists() else {}
        if prior_status.get("status") == "ROLLED_BACK" or prior_status.get("status") == "COMPLETE" and prior_status.get("decision", {}).get("recommendation") == "RETAIN":
            results.append({**prior_status, "already_final": True, "production_mutated": False})
            continue
        current, policy = champion.load_manifest(manifest_path), _read(policy_path)
        result: dict[str, Any] = {"monitor_id": identity, "deployment_id": deployment["deployment_id"], "as_of": replay._iso(now), "production_mutated": False}
        if not _current_matches(deployment, current, policy):
            result.update(status="PENDING", reason="active_manifest_or_policy_changed")
        elif replay.production_code_sha256() != deployment["production_code_sha256"]:
            result.update(status="PENDING", reason="production_code_changed")
        else:
            start = replay._time(deployment["activated_at"])
            end = replay._time(deployment["monitoring_contract"]["window_end_at"])
            try:
                frames = _native_frames(frames_root, start, min(now, end))
                if any(frame.get("policy_sha256") != deployment["policy_sha256"] for frame in frames):
                    raise MonitorError("native observation policy changed")
                pair = replay.compare_policies(deployment["initial_state"], frames,
                    champion_policy=deployment["previous_policy"], challenger_policy=deployment["activated_policy"],
                    bridge_config=deployment["bridge_config"], runtime=deployment["runtime"], markets=[deployment["market"]])
            except (ValueError, KeyError, TypeError, OSError) as exc:
                pair = {"status": "PENDING", "reason": f"native_replay_rejected:{type(exc).__name__}"}
            if pair["status"] != "COMPLETE":
                result.update(status="PENDING", reason="paired_actual_control_replay_incomplete", replay_diagnostics={name: pair.get(name, {}).get("diagnostics", []) for name in ("champion", "challenger")}, replay_error=pair.get("reason"))
            else:
                market = deployment["market"]
                control = {point["observed_at"]: point for point in pair["champion"]["curves_by_market"][market]}
                observations = []
                for point in pair["challenger"]["curves_by_market"][market]:
                    stamp = point["observed_at"]
                    if stamp not in control:
                        continue
                    body = {key: deployment[key] for key in ("deployment_id", "policy_sha256", "cost_model_sha256", "market")}
                    body.update(schema_version="stock-trading-component-economic-observation-v1", status="COMPLETE", observed_at=stamp,
                                net_nav=point["net_nav"], control_net_nav=control[stamp]["net_nav"], production_code_sha256=deployment["production_code_sha256"])
                    row = {**body, "observation_sha256": replay.payload_sha256(body)}
                    if not dry_run:
                        _append_once(Path(root) / "observations" / identity / f"{replay.payload_sha256(stamp)}.json", row)
                    observations.append(row)
                decision = analytics.deployment_economic_monitor(deployment, observations, as_of=now)
                max_age = timedelta(hours=deployment["monitoring_contract"]["maximum_terminal_observation_age_hours"])
                if now >= end and (not observations or replay._time(observations[-1]["observed_at"]) < end - max_age):
                    decision = {"status": "PENDING", "recommendation": "WAIT_FOR_EVIDENCE", "reason": "terminal_paired_mark_unavailable"}
                result.update(status=decision["status"], reason=decision.get("reason"), decision=decision, paired_observations=len(observations), input_sha256=pair["input_sha256"])
                if decision.get("recommendation") == "RECOMMEND_ROLLBACK":
                    if dry_run:
                        result.update(status="DRY_RUN_ROLLBACK_READY")
                    else:
                        try:
                            result.update(_rollback(deployment, decision, now=now, manifest_path=manifest_path, policy_path=policy_path, history_dir=history_dir, audit_path=audit_path), production_mutated=True)
                        except MonitorError as exc:
                            result.update(status="PENDING", reason=str(exc), production_mutated=False)
        if not dry_run:
            portfolio._atomic(status_path, result)
        results.append(result)
    return {"schema_version": "stock-trading-component-economic-monitor-run-v1", "status": "OK", "results": results, "production_mutated": any(row.get("production_mutated") for row in results)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=MONITOR_ROOT)
    parser.add_argument("--frames-root", type=Path, default=replay.DEFAULT_FRAME_ROOT)
    parser.add_argument("--manifest", type=Path, default=champion.MANIFEST_PATH)
    parser.add_argument("--policy", type=Path, default=portfolio.POLICY_PATH)
    parser.add_argument("--history", type=Path, default=champion.HISTORY_DIR)
    parser.add_argument("--audit", type=Path, default=champion.AUDIT_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(root=args.root, frames_root=args.frames_root, manifest_path=args.manifest, policy_path=args.policy,
                         history_dir=args.history, audit_path=args.audit, dry_run=args.dry_run), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
