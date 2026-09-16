#!/usr/bin/env python3
"""Create auditable Challenger policies from Stock Trading v2 learning evidence.

The learner never edits production thresholds. Once the regret engine has enough
causal evidence for a learnable component, this module freezes a Challenger
experiment with a *future-only* holdout start. Promotion is delegated to the
statistical gate and remains disabled for production by configuration.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import stock_trading_portfolio as portfolio
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_regret_engine as regret
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_portfolio as portfolio
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_regret_engine as regret

ROOT = Path(__file__).resolve().parents[1]
LEARNING_CONFIG_PATH = ROOT / "data/investments/stock_trading_v2_learning_config.json"
PROMOTION_CONFIG_PATH = ROOT / "data/investments/statistical_promotion_gate_v2_config.json"
REGRET_REPORT_PATH = ROOT / "data/investments/stock_trading_v2_learning/regret_report.json"
DEFAULT_ROOT = ROOT / "data/investments/stock_trading_v2_challengers"
SCHEMA_VERSION = "stock-trading-v2-challenger-policy-v1"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def make_challenger(
    hypothesis: Mapping[str, Any],
    *,
    regret_report: Mapping[str, Any],
    champion_policy: Mapping[str, Any],
    promotion_config: Mapping[str, Any],
) -> dict[str, Any]:
    if hypothesis.get("status") != "ELIGIBLE_FOR_CHALLENGER_HOLDOUT":
        raise contracts.ContractError("hypothesis is not eligible for a fresh holdout")
    component = str(hypothesis.get("component") or "").strip()
    if not component:
        raise contracts.ContractError("challenger component missing")
    horizon = int(hypothesis.get("horizon_sessions") or 0)
    if horizon <= 0:
        raise contracts.ContractError("challenger horizon invalid")
    champion_sha = contracts.payload_sha256(champion_policy)
    identity = {
        "component": component,
        "horizon_sessions": horizon,
        "champion_policy_sha256": champion_sha,
        "experiment_type": (hypothesis.get("suggested_experiment") or {}).get("type"),
    }
    challenger_id = "stchallv2-" + contracts.payload_sha256(identity)[:24]
    start_at = str(regret_report.get("generated_at") or contracts.iso_utc())
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "challenger_id": challenger_id,
        "created_at": start_at,
        "validation_start_at": start_at,
        "component": component,
        "horizon_sessions": horizon,
        "champion": {
            "policy_version": champion_policy.get("policy_version"),
            "policy_sha256": champion_sha,
        },
        "change": dict(hypothesis.get("suggested_experiment") or {}),
        "origin_evidence": {
            "regret_report_sha256": regret_report.get("report_sha256"),
            "evidence": hypothesis.get("evidence"),
        },
        "holdout": {
            "fixed_paired_n": int(promotion_config.get("fixed_paired_n") or 30),
            "confidence_level": float(promotion_config.get("confidence_level") or 0.9),
            "bootstrap_samples": int(promotion_config.get("bootstrap_samples") or 4000),
            "minimum_net_incremental_return_percent": float(promotion_config.get("minimum_net_incremental_return_percent") or 0.0),
            "minimum_net_positive_rate": float(promotion_config.get("minimum_net_positive_rate") or 0.55),
            "minimum_unique_symbols": int(promotion_config.get("minimum_unique_symbols") or 5),
            "minimum_span_days": int(promotion_config.get("minimum_span_days") or 10),
            "maximum_single_positive_contribution_share": float(promotion_config.get("maximum_single_positive_contribution_share") or 0.5),
            "single_formal_look": True,
            "historical_evidence_reuse_forbidden": True,
        },
        "state": "COLLECTING_FRESH_HOLDOUT",
        "governance": {
            "production_decision_influence": False,
            "automatic_policy_writeback": False,
            "production_promotion_enabled": bool(promotion_config.get("production_promotion_enabled")) and False,
            "future_only_holdout": True,
            "safety_gates_unchanged": True,
        },
    }
    body = dict(payload)
    payload["challenger_sha256"] = contracts.payload_sha256(body)
    validate_challenger(payload)
    return payload


def validate_challenger(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise contracts.ContractError("challenger schema mismatch")
    for key in ("challenger_id", "created_at", "validation_start_at", "component"):
        if not str(payload.get(key) or "").strip():
            raise contracts.ContractError(f"challenger missing {key}")
    if int(payload.get("horizon_sessions") or 0) <= 0:
        raise contracts.ContractError("challenger horizon invalid")
    holdout = payload.get("holdout") or {}
    if int(holdout.get("fixed_paired_n") or 0) <= 0 or holdout.get("single_formal_look") is not True:
        raise contracts.ContractError("challenger holdout contract invalid")
    governance = payload.get("governance") or {}
    if governance.get("production_decision_influence") is not False or governance.get("automatic_policy_writeback") is not False:
        raise contracts.ContractError("challenger escaped shadow governance")
    if governance.get("safety_gates_unchanged") is not True:
        raise contracts.ContractError("challenger cannot alter safety invariants")
    body = dict(payload)
    stored = str(body.pop("challenger_sha256", ""))
    if not stored or stored != contracts.payload_sha256(body):
        raise contracts.ContractError("challenger hash mismatch")


def persist(root: Path, payload: Mapping[str, Any]) -> bool:
    validate_challenger(payload)
    path = root / f"{payload['challenger_id']}.json"
    if path.exists():
        existing = _read_json(path)
        validate_challenger(existing)
        if existing == payload:
            return False
        # Created-at is tied to the first qualifying report. Never move the
        # holdout boundary later just because more historical evidence arrives.
        return False
    root.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=root, delete=False) as handle:
        handle.write(text)
        temp = Path(handle.name)
    temp.replace(path)
    return True


def create_from_report(
    *,
    report_path: Path = REGRET_REPORT_PATH,
    champion_policy_path: Path = portfolio.POLICY_PATH,
    promotion_config_path: Path = PROMOTION_CONFIG_PATH,
    root: Path = DEFAULT_ROOT,
) -> dict[str, Any]:
    if not report_path.exists():
        return {"schema_version": "stock-trading-v2-policy-learner-run-v1", "eligible": 0, "created": 0, "reason": "regret_report_missing", "production_decision_influence": False}
    report = _read_json(report_path)
    regret.validate_report(report)
    champion = _read_json(champion_policy_path)
    promotion = _read_json(promotion_config_path)
    eligible = [row for row in report.get("challenger_hypotheses") or [] if isinstance(row, Mapping) and row.get("status") == "ELIGIBLE_FOR_CHALLENGER_HOLDOUT"]
    created = existing = 0
    ids: list[str] = []
    for hypothesis in eligible:
        challenger = make_challenger(
            hypothesis,
            regret_report=report,
            champion_policy=champion,
            promotion_config=promotion,
        )
        ids.append(str(challenger["challenger_id"]))
        if persist(root, challenger):
            created += 1
        else:
            existing += 1
    return {
        "schema_version": "stock-trading-v2-policy-learner-run-v1",
        "eligible": len(eligible),
        "created": created,
        "existing": existing,
        "challenger_ids": ids,
        "production_decision_influence": False,
    }


def verify(root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    count = 0
    for path in sorted(root.glob("*.json")) if root.exists() else []:
        validate_challenger(_read_json(path))
        count += 1
    return {"schema_version": SCHEMA_VERSION, "ok": True, "count": count, "production_decision_influence": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=REGRET_REPORT_PATH)
    parser.add_argument("--policy", type=Path, default=portfolio.POLICY_PATH)
    parser.add_argument("--promotion-config", type=Path, default=PROMOTION_CONFIG_PATH)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = verify(args.root) if args.verify else create_from_report(
        report_path=args.report,
        champion_policy_path=args.policy,
        promotion_config_path=args.promotion_config,
        root=args.root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
