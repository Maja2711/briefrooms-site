#!/usr/bin/env python3
"""Shadow Portfolio Opportunity Engine for Stock Trading v2.

The engine compares cash, current canonical positions and Deep Evidence
candidates. It emits a primary research decision plus the complete ranked
candidate frontier. Production may consume multiple qualified frontier members
in one cycle up to market capacity; rejection of one member never terminates
the search. It never mutates the production portfolio and never treats an empty
slot as a reason to trade. Replacement requires explicit hysteresis to avoid
churn.
"""
from __future__ import annotations

import argparse
import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_deep_evidence as evidence_engine
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_deep_evidence as evidence_engine

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data/investments/stock_trading_v2_opportunity_config.json"
EVIDENCE_ROOT = ROOT / "data/investments/stock_trading_v2_evidence"
DEFAULT_PORTFOLIO = ROOT / "data/investments/stock_trading_portfolio.json"
OUTPUT_ROOT = ROOT / "data/investments/stock_trading_v2_opportunity"
SCHEMA_VERSION = "stock-trading-v2-portfolio-opportunity-v1"
ACTIONS = {"BUY", "HOLD", "CASH", "REPLACE"}


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _symbol_key(value: Any) -> str:
    symbol = str(value or "").upper().strip()
    if symbol.endswith(".WA"):
        symbol = symbol[:-3]
    return symbol.replace(".", "-")


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = _read_json(path)
    if not isinstance(config, dict) or config.get("schema_version") != "stock-trading-v2-opportunity-config-v1":
        raise contracts.ContractError("opportunity configuration schema mismatch")
    governance = config.get("governance") or {}
    if governance.get("production_decision_influence") is not False or governance.get("automatic_portfolio_admission") is not False:
        raise contracts.ContractError("opportunity configuration must remain shadow-only")
    return config


def market_positions(portfolio: Mapping[str, Any] | None, market: str) -> list[dict[str, Any]]:
    if not isinstance(portfolio, Mapping):
        return []
    row = ((portfolio.get("markets") or {}).get(market) or {}) if isinstance(portfolio.get("markets"), Mapping) else {}
    positions = row.get("open_positions") or []
    return [
        deepcopy(dict(position))
        for position in positions
        if isinstance(position, Mapping) and str(position.get("status") or "OPEN").upper() == "OPEN"
    ]


def held_position_utility(position: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    cfg = config.get("held_position") or {}
    default = float(cfg.get("default_utility") or 50.0)
    entry_score = _finite(position.get("entry_score"))
    thesis_score = _finite(position.get("thesis_score"))
    entry = _finite(position.get("entry"))
    mark = _finite(position.get("last_mark"))
    mark_return = None
    if entry is not None and entry > 0 and mark is not None:
        mark_return = mark / entry - 1.0
    mark_scale = float(cfg.get("mark_return_scale") or 120.0)
    mark_cap = float(cfg.get("mark_return_cap_points") or 20.0)
    mark_score = default
    if mark_return is not None:
        points = max(-mark_cap, min(mark_cap, mark_return * mark_scale))
        mark_score = _clamp(default + points)

    components = {
        "entry": entry_score if entry_score is not None else default,
        "thesis": thesis_score if thesis_score is not None else (entry_score if entry_score is not None else default),
        "mark": mark_score,
    }
    weights = {
        "entry": float(cfg.get("entry_score_weight") or 0.35),
        "thesis": float(cfg.get("thesis_score_weight") or 0.50),
        "mark": float(cfg.get("mark_return_weight") or 0.15),
    }
    denominator = sum(weights.values()) or 1.0
    utility = sum(components[key] * weights[key] for key in components) / denominator
    return {
        "position_id": position.get("position_id"),
        "symbol": str(position.get("symbol") or position.get("ticker") or ""),
        "symbol_key": _symbol_key(position.get("symbol") or position.get("ticker")),
        "utility": round(_clamp(utility), 6),
        "components": {key: round(float(value), 6) for key, value in components.items()},
        "entry": entry,
        "last_mark": mark,
        "unrealized_return": round(mark_return, 8) if mark_return is not None else None,
    }


def candidate_gate(candidate: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[bool, str]:
    gates = config.get("hard_gates") or {}
    if gates.get("require_non_data_error_evidence") and candidate.get("evidence_status") == "DATA_ERROR":
        return False, "deep_evidence_data_error"
    liquidity = candidate.get("liquidity") or {}
    if gates.get("require_production_reference_liquidity") and liquidity.get("production_reference_pass") is not True:
        return False, "production_reference_liquidity_not_met"
    risk = candidate.get("research_risk_plan") or {}
    if gates.get("require_valid_research_risk_plan") and risk.get("status") != "VALID_RESEARCH_REFERENCE":
        return False, "research_risk_plan_invalid"
    if gates.get("require_open_ended_holding_policy") and risk.get("holding_policy") != "OPEN_ENDED_MODEL_CONTROLLED":
        return False, "holding_policy_not_open_ended"
    if gates.get("require_no_fixed_exit_deadline"):
        for key in ("time_stop", "valid_until", "scheduled_exit"):
            if risk.get(key) is not None:
                return False, "fixed_exit_deadline_present"
    entry = _finite(risk.get("reference_price"))
    stop = _finite(risk.get("stop"))
    target = _finite(risk.get("target"))
    if entry is None or stop is None or target is None or not stop < entry < target:
        return False, "risk_geometry_invalid"
    if risk.get("execution_ready") is not False:
        return False, "research_plan_must_not_be_execution_ready"
    return True, "eligible_shadow_opportunity"


def candidate_utility(candidate: Mapping[str, Any], config: Mapping[str, Any]) -> float:
    score = float(candidate.get("deep_opportunity_score") or candidate.get("opportunity_score") or 0.0)
    status = str(candidate.get("evidence_status") or "DATA_ERROR")
    penalty = float((config.get("data_quality_penalties") or {}).get(status, 100.0))
    return _clamp(score - penalty)


def compare_opportunities(
    deep_evidence: Mapping[str, Any],
    portfolio: Mapping[str, Any] | None,
    config: Mapping[str, Any],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    evidence_engine.validate_deep_evidence(deep_evidence)
    market = str(deep_evidence.get("market") or "").upper()
    generated = generated_at or datetime.now(timezone.utc)
    cash_utility = float(config.get("cash_utility") or 50.0)
    edge = float(config.get("minimum_new_position_edge_points") or 5.0)
    replacement_hysteresis = float(config.get("replacement_hysteresis_points") or 8.0)
    cap = int(config.get("maximum_open_positions_per_market") or 3)

    positions = market_positions(portfolio, market)
    held = [held_position_utility(position, config) for position in positions]
    held_keys = {row["symbol_key"] for row in held if row["symbol_key"]}
    weakest = min(held, key=lambda row: float(row["utility"])) if held else None
    available_slots = max(0, cap - len(held))

    evaluated: list[dict[str, Any]] = []
    for candidate in deep_evidence.get("candidates") or []:
        ok, reason = candidate_gate(candidate, config)
        utility = candidate_utility(candidate, config)
        symbol_key = _symbol_key(candidate.get("symbol"))
        already_held = symbol_key in held_keys
        evaluated.append(
            {
                "symbol": candidate.get("symbol"),
                "market_data_symbol": candidate.get("market_data_symbol"),
                "name": candidate.get("name"),
                "deep_rank": candidate.get("deep_rank"),
                "utility": round(utility, 6),
                "eligible": bool(ok),
                "eligibility_reason": reason,
                "already_held": already_held,
                "evidence_status": candidate.get("evidence_status"),
                "evidence_metrics": deepcopy(candidate.get("evidence_metrics") or {}),
                "research_risk_plan": deepcopy(candidate.get("research_risk_plan") or {}),
            }
        )
    evaluated.sort(key=lambda row: (bool(row["eligible"]), float(row["utility"])), reverse=True)
    eligible_new = [row for row in evaluated if row["eligible"] and not row["already_held"]]
    best = eligible_new[0] if eligible_new else None

    decision: dict[str, Any]
    if best is not None and float(best["utility"]) >= cash_utility + edge and available_slots > 0:
        decision = {
            "action": "BUY",
            "reason": "new_opportunity_exceeds_cash_edge_with_free_slot",
            "candidate": deepcopy(best),
            "replace": None,
        }
    elif (
        best is not None
        and weakest is not None
        and available_slots == 0
        and float(best["utility"]) >= cash_utility + edge
        and float(best["utility"]) >= float(weakest["utility"]) + replacement_hysteresis
    ):
        decision = {
            "action": "REPLACE",
            "reason": "new_opportunity_exceeds_weakest_position_with_hysteresis",
            "candidate": deepcopy(best),
            "replace": deepcopy(weakest),
        }
    elif held:
        top_held = max(held, key=lambda row: float(row["utility"]))
        decision = {
            "action": "HOLD",
            "reason": "no_new_opportunity_clears_cash_or_replacement_edge",
            "candidate": None,
            "replace": None,
            "strongest_held": deepcopy(top_held),
        }
    else:
        decision = {
            "action": "CASH",
            "reason": "no_eligible_opportunity_clears_cash_edge",
            "candidate": None,
            "replace": None,
        }

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "market": market,
        "generated_at": _iso(generated),
        "source_evidence_sha256": deep_evidence.get("evidence_sha256"),
        "source_evidence_generated_at": deep_evidence.get("generated_at"),
        "cash": {"utility": cash_utility, "minimum_new_position_edge_points": edge},
        "portfolio": {
            "max_open_positions": cap,
            "open_position_count": len(held),
            "available_slots": available_slots,
            "held_position_utilities": held,
            "weakest_position": deepcopy(weakest),
        },
        "evaluated_candidates": evaluated,
        "decision": decision,
        "governance": {
            "production_decision_influence": False,
            "automatic_portfolio_admission": False,
            "automatic_position_replacement": False,
            "execution_ready": False,
            "no_forced_trade": True,
            "maximum_actions_per_cycle": int(config.get("maximum_actions_per_market_cycle") or 1),
            "production_promotion_requires_separate_statistical_gate": True,
        },
    }
    payload["opportunity_sha256"] = contracts.payload_sha256(payload)
    validate_opportunity(payload)
    return payload


def validate_opportunity(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise contracts.ContractError("portfolio opportunity schema mismatch")
    if payload.get("market") not in contracts.SUPPORTED_MARKETS:
        raise contracts.ContractError("portfolio opportunity market unsupported")
    governance = payload.get("governance") or {}
    if governance.get("production_decision_influence") is not False:
        raise contracts.ContractError("portfolio opportunity escaped shadow governance")
    if governance.get("automatic_portfolio_admission") is not False or governance.get("automatic_position_replacement") is not False:
        raise contracts.ContractError("portfolio opportunity may not mutate production")
    if governance.get("execution_ready") is not False:
        raise contracts.ContractError("shadow portfolio opportunity cannot be execution-ready")
    decision = payload.get("decision") or {}
    if decision.get("action") not in ACTIONS:
        raise contracts.ContractError("portfolio opportunity action invalid")
    portfolio = payload.get("portfolio") or {}
    count = int(portfolio.get("open_position_count") or 0)
    cap = int(portfolio.get("max_open_positions") or 0)
    slots = int(portfolio.get("available_slots") or 0)
    if cap <= 0 or count < 0 or slots != max(0, cap - count):
        raise contracts.ContractError("portfolio opportunity slot arithmetic invalid")
    if decision.get("action") == "BUY" and slots <= 0:
        raise contracts.ContractError("BUY emitted without free slot")
    if decision.get("action") == "REPLACE" and (slots != 0 or not decision.get("replace")):
        raise contracts.ContractError("REPLACE emitted without a full book/target")
    body = dict(payload)
    stored = str(body.pop("opportunity_sha256", ""))
    if not stored or stored != contracts.payload_sha256(body):
        raise contracts.ContractError("portfolio opportunity hash mismatch")


def run_market(
    market: str,
    *,
    evidence_path: Path | None = None,
    portfolio_path: Path | None = None,
    config_path: Path = CONFIG_PATH,
) -> dict[str, Any]:
    market = str(market).upper()
    config = load_config(config_path)
    evidence_path = evidence_path or (EVIDENCE_ROOT / f"{market.lower()}.json")
    deep_evidence = _read_json(evidence_path)
    if not isinstance(deep_evidence, Mapping):
        raise contracts.ContractError("deep evidence snapshot missing")
    portfolio = _read_json(portfolio_path or DEFAULT_PORTFOLIO, {})
    if not isinstance(portfolio, Mapping):
        portfolio = {}
    return compare_opportunities(deep_evidence, portfolio, config)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["US", "GPW", "us", "gpw"], required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--portfolio", type=Path)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    market = str(args.market).upper()
    payload = run_market(
        market,
        evidence_path=args.evidence,
        portfolio_path=args.portfolio,
        config_path=args.config,
    )
    output = args.output or (OUTPUT_ROOT / f"{market.lower()}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    decision = payload["decision"]
    print(json.dumps({
        "market": market,
        "action": decision["action"],
        "reason": decision["reason"],
        "candidate": (decision.get("candidate") or {}).get("symbol"),
        "replace": (decision.get("replace") or {}).get("symbol"),
        "available_slots": payload["portfolio"]["available_slots"],
        "production_decision_influence": False,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
