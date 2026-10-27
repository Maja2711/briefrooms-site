#!/usr/bin/env python3
"""Generate exact, bounded Stock Trading v2 Challenger deployment proposals."""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_regret_engine as regret
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_regret_engine as regret

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "data/investments/stock_trading_v2_factory_candidates"
SCHEMA_VERSION = "stock-trading-v2-factory-candidate-v1"
# The production Daily Trading mandate is explicitly 1-2 sessions. Longer v2
# horizons remain valuable research evidence, but may not manufacture a
# production-deployable artifact for this system.
DEPLOYABLE_HORIZONS = frozenset({1, 2})
CASH_COMPONENT = "cash_admission_margin"


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def deployment_sha256(spec: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(spec), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temp = Path(handle.name)
    temp.replace(path)


def _entry_threshold_candidates(*, hypothesis: Mapping[str, Any], manifest: Mapping[str, Any], gpw_config: Mapping[str, Any], policy: Mapping[str, Any]) -> list[dict[str, Any]]:
    horizon = int(hypothesis.get("horizon_sessions") or 0)
    if horizon not in DEPLOYABLE_HORIZONS:
        raise contracts.ContractError("entry Factory horizon is outside the 1-2 session production mandate")
    gpw_threshold = int(gpw_config.get("minimum_composite_score") or 0)
    policy_threshold = int((((policy.get("markets") or {}).get("GPW") or {}).get("minimum_entry_score")) or 0)
    if gpw_threshold <= 0 or gpw_threshold != policy_threshold:
        raise contracts.ContractError("production GPW entry thresholds are missing or inconsistent")
    component_state = ((manifest.get("components") or {}).get("entry") or {})
    revision = int(manifest.get("revision") or 0)
    base_version = str(component_state.get("version") or "")
    if revision < 1 or not base_version:
        raise contracts.ContractError("production Champion entry baseline missing")

    values = [value for value in (gpw_threshold - 1, gpw_threshold - 2, gpw_threshold - 3) if value >= 60]
    rows: list[dict[str, Any]] = []
    for value in values:
        spec = {
            "component": "entry",
            "version": f"entry-threshold-{value}-r{revision}",
            "type": "config_patch",
            "targets": {
                "gpw_daily_config": [{"op": "replace", "path": ["minimum_composite_score"], "value": value}],
                "stock_trading_policy": [{"op": "replace", "path": ["markets", "GPW", "minimum_entry_score"], "value": value}],
            },
        }
        spec_sha = deployment_sha256(spec)
        deployment_id = "stdep-entry-" + spec_sha[:20]
        identity = {
            "deployment_sha256": spec_sha,
            "base_manifest_revision": revision,
            "base_component_version": base_version,
            "horizon_sessions": horizon,
        }
        candidate_id = "stfactv2-" + contracts.payload_sha256(identity)[:24]
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "candidate_id": candidate_id,
            "created_at": contracts.iso_utc(),
            "research_component": "entry_score_below_threshold",
            "production_component": "entry",
            "horizon_sessions": horizon,
            "base_manifest_revision": revision,
            "base_component_version": base_version,
            "deployment_id": deployment_id,
            "deployment_sha256": spec_sha,
            "deployment_spec": spec,
            "replay_contract": {
                "adapter": "entry_threshold_v1",
                "champion_threshold": gpw_threshold,
                "challenger_threshold": value,
                "market": "GPW",
                "requires_single_entry_blocker": True,
            },
            "origin_evidence": hypothesis.get("evidence"),
            "governance": {
                "production_decision_influence": False,
                "automatic_policy_writeback": False,
                "exact_artifact_required": True,
                "bounded_parameter_search": True,
                "production_horizon_sessions": [1, 2],
            },
        }
        body = dict(payload)
        payload["candidate_sha256"] = contracts.payload_sha256(body)
        validate_candidate(payload)
        rows.append(payload)
    return rows


def _cash_edge_candidates(*, hypothesis: Mapping[str, Any], manifest: Mapping[str, Any], policy: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One production parameter, a predeclared two-member family, no gate edits."""
    revision = int(manifest.get("revision") or 0)
    base_version = str(((manifest.get("components") or {}).get("entry") or {}).get("version") or "")
    if revision < 1 or not base_version:
        raise contracts.ContractError("production Champion entry baseline missing")
    horizon = int(hypothesis.get("horizon_sessions") or 0)
    if horizon not in DEPLOYABLE_HORIZONS:
        raise contracts.ContractError("CASH edge horizon must be 1 or 2 sessions")
    market = str(hypothesis.get("market") or (hypothesis.get("evidence") or {}).get("market") or "").upper()
    if market not in {"GPW", "US"}:
        raise contracts.ContractError("CASH edge hypothesis requires exact market")
    baseline = float(((policy.get("markets") or {}).get(market) or {})["minimum_cash_edge_points"])
    if not 1 <= baseline <= 15:
        raise contracts.ContractError("production CASH edge outside bounded domain")
    rows = []
    for delta in (-1, 1):
        value = baseline + delta
        if not 1 <= value <= 15:
            continue
        spec = {
            "component": "entry", "version": f"cash-edge-{market.lower()}-{value:g}-r{revision}",
            "type": "config_patch", "targets": {"stock_trading_policy": [{
                "op": "replace", "path": ["markets", market, "minimum_cash_edge_points"], "value": value,
            }]},
        }
        spec_sha = deployment_sha256(spec)
        identity = {"deployment_sha256": spec_sha, "base_manifest_revision": revision,
                    "base_component_version": base_version, "horizon_sessions": horizon,
                    "champion_policy_sha256": contracts.payload_sha256(policy)}
        row = {
            "schema_version": SCHEMA_VERSION, "candidate_id": "stfactv2-" + contracts.payload_sha256(identity)[:24],
            "created_at": str(hypothesis.get("generated_at") or contracts.iso_utc()),
            "research_component": CASH_COMPONENT, "production_component": "entry", "market": market,
            "horizon_sessions": horizon, "base_manifest_revision": revision, "base_component_version": base_version,
            "champion_policy_snapshot": dict(policy), "champion_policy_sha256": contracts.payload_sha256(policy),
            "champion_manifest_snapshot": dict(manifest),
            "deployment_id": "stdep-entry-" + spec_sha[:20], "deployment_sha256": spec_sha, "deployment_spec": spec,
            "replay_contract": {"adapter": "cash_edge_v2", "market": market,
                                "champion_cash_edge_points": baseline, "challenger_cash_edge_points": value,
                                "requires_free_slot": True, "requires_production_lifecycle": True,
                                "policy_sha256": contracts.payload_sha256(policy)},
            "origin_evidence": hypothesis.get("evidence"),
            "governance": {"production_decision_influence": False, "automatic_policy_writeback": False,
                           "exact_artifact_required": True, "bounded_parameter_search": True,
                           "production_horizon_sessions": [1, 2], "predeclared_family": [-1, 1]},
        }
        row["candidate_sha256"] = contracts.payload_sha256(row)
        validate_candidate(row)
        rows.append(row)
    return rows


def validate_candidate(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise contracts.ContractError("factory candidate schema mismatch")
    if payload.get("production_component") not in {"entry", "ranking", "risk", "portfolio", "exit", "universe", "meta_label", "regime"}:
        raise contracts.ContractError("factory candidate production component invalid")
    if int(payload.get("horizon_sessions") or 0) not in DEPLOYABLE_HORIZONS:
        raise contracts.ContractError("factory candidate horizon is outside the 1-2 session production mandate")
    if int(payload.get("base_manifest_revision") or 0) < 1:
        raise contracts.ContractError("factory candidate base revision invalid")
    spec = payload.get("deployment_spec")
    if not isinstance(spec, Mapping):
        raise contracts.ContractError("factory candidate deployment spec missing")
    if deployment_sha256(spec) != payload.get("deployment_sha256"):
        raise contracts.ContractError("factory candidate deployment hash mismatch")
    if str(spec.get("component") or "") != str(payload.get("production_component") or ""):
        raise contracts.ContractError("factory candidate component mismatch")
    if (payload.get("replay_contract") or {}).get("adapter") == "cash_edge_v2":
        replay = payload["replay_contract"]
        market = str(replay.get("market") or "")
        base, value = float(replay.get("champion_cash_edge_points")), float(replay.get("challenger_cash_edge_points"))
        expected = {"stock_trading_policy": [{"op": "replace", "path": ["markets", market, "minimum_cash_edge_points"], "value": value}]}
        if market not in {"GPW", "US"} or abs(value - base) != 1 or not 1 <= value <= 15 or spec.get("targets") != expected:
            raise contracts.ContractError("CASH edge deployment is not one bounded market mutation")
        if contracts.payload_sha256(payload.get("champion_policy_snapshot") or {}) != payload.get("champion_policy_sha256"):
            raise contracts.ContractError("CASH edge baseline hash mismatch")
    governance = payload.get("governance") or {}
    if governance.get("production_decision_influence") is not False or governance.get("automatic_policy_writeback") is not False:
        raise contracts.ContractError("factory candidate escaped research governance")
    body = dict(payload)
    stored = str(body.pop("candidate_sha256", ""))
    if not stored or stored != contracts.payload_sha256(body):
        raise contracts.ContractError("factory candidate integrity hash mismatch")


def build_candidates(*, report: Mapping[str, Any], manifest: Mapping[str, Any], gpw_config: Mapping[str, Any], policy: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    regret.validate_report(report)
    generated: list[dict[str, Any]] = []
    unsupported: list[dict[str, str]] = []
    for raw in report.get("challenger_hypotheses") or []:
        if not isinstance(raw, Mapping) or raw.get("status") != "ELIGIBLE_FOR_CHALLENGER_HOLDOUT":
            continue
        component = str(raw.get("component") or "")
        horizon = int(raw.get("horizon_sessions") or 0)
        if horizon not in DEPLOYABLE_HORIZONS:
            unsupported.append({
                "research_component": component,
                "reason": "outside_1_2_session_production_mandate",
            })
            continue
        if component == CASH_COMPONENT:
            generated.extend(_cash_edge_candidates(hypothesis=raw, manifest=manifest, policy=policy))
        elif component == "entry_score_below_threshold" and policy.get("champion_engine") != "v2":
            generated.extend(_entry_threshold_candidates(hypothesis=raw, manifest=manifest, gpw_config=gpw_config, policy=policy))
        else:
            unsupported.append({"research_component": component, "reason": "exact_replay_adapter_not_yet_available"})
    if policy.get("champion_engine") == "v2":
        # Initial collection must not depend on exact tapes which can exist only
        # after the first prospective trial. The two bounded deltas are fixed
        # research declarations, independently of historical return rankings.
        # Historical evidence remains useful for diagnostics; all formal
        # members begin after main's registration receipt.
        declared = {(str(row.get("market")), int(row["horizon_sessions"])) for row in generated
                    if row.get("research_component") == CASH_COMPONENT}
        for market in ("GPW", "US"):
            if not ((policy.get("markets") or {}).get(market) or {}).get("minimum_cash_edge_points"):
                continue
            for horizon in sorted(DEPLOYABLE_HORIZONS):
                if (market, horizon) in declared:
                    continue
                generated.extend(_cash_edge_candidates(hypothesis={
                    "component": CASH_COMPONENT, "market": market, "horizon_sessions": horizon,
                    "evidence": {"market": market, "diagnostic_only": True,
                                 "formal_holdout_reuse_forbidden": True,
                                 "initialization": "predeclared_bounded_family_before_outcomes"},
                }, manifest=manifest, policy=policy))
    return generated, unsupported


def run(*, report_path: Path, manifest_path: Path, gpw_config_path: Path, policy_path: Path, output_root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    report = _read(report_path)
    manifest = _read(manifest_path)
    gpw = _read(gpw_config_path)
    policy = _read(policy_path)
    candidates, unsupported = build_candidates(report=report, manifest=manifest, gpw_config=gpw, policy=policy)
    output_root.mkdir(parents=True, exist_ok=True)
    written = existing = 0
    for candidate in candidates:
        path = output_root / f"{candidate['candidate_id']}.json"
        if path.exists():
            validate_candidate(_read(path))
            existing += 1
            continue
        _atomic_json(path, candidate)
        written += 1
    return {
        "schema_version": "stock-trading-v2-challenger-factory-run-v1",
        "generated": len(candidates),
        "written": written,
        "existing": existing,
        "unsupported": unsupported,
        "production_decision_influence": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--gpw-config", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        count = 0
        for path in sorted(args.output_root.glob("*.json")) if args.output_root.exists() else []:
            validate_candidate(_read(path))
            count += 1
        print(json.dumps({"ok": True, "count": count, "production_decision_influence": False}, indent=2, sort_keys=True))
        return 0
    result = run(report_path=args.report, manifest_path=args.manifest, gpw_config_path=args.gpw_config, policy_path=args.policy, output_root=args.output_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
