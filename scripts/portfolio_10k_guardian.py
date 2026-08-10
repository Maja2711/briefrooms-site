#!/usr/bin/env python3
"""Portfolio 10K control guardian.

Keeps the public paper portfolio operationally honest without weakening BRACE
risk gates.  The guardian has two responsibilities:

1. verify the latest mark-to-market accounting/freshness state and publish a
   compact health receipt;
2. when genuine free cash is large enough, create a governed BRACE ``ADD``
   decision for the strongest fresh candidate.  It never forces investment:
   cash is deployed only when every predeclared quality/risk gate passes.

This module is paper-only.  It does not connect to a broker and does not submit
orders itself; the existing BRACE paper execution path remains the sole order
executor.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from brace_portfolio_decision import DECISION_STATUSES, deterministic_id

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_PATH = ROOT / "data" / "investments" / "portfolio_10k.json"
CONFIG_PATH = ROOT / "data" / "portfolio10k" / "config.json"
ANALYSIS_PATH = ROOT / "data" / "portfolio10k" / "analysis.json"
PENDING_PATH = ROOT / "data" / "portfolio10k" / "pending_decisions.json"
ORDERS_PATH = ROOT / "data" / "portfolio10k" / "paper_orders.json"
OPERATIONAL_PATH = ROOT / "data" / "portfolio10k" / "operational_state.json"
STATE_PATH = ROOT / "data" / "portfolio10k" / "guardian_state.json"

ACTIVE_ORDER_STATUSES = {"QUEUED", "WAITING_FOR_MARKET", "READY"}
TERMINAL_DECISION_STATUSES = {"REJECTED", "EXPIRED", "EXECUTED", "CANCELLED"}
EXECUTION_COST_SAFETY = 1.004  # 40 bps: > default 35 bps execution+FX costs.


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(text)
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def active_positions(portfolio: Mapping[str, Any]) -> list[Dict[str, Any]]:
    rows = []
    for item in portfolio.get("positions", []) or []:
        status = str(item.get("status") or "active").lower()
        if "closed" in status or status in {"sold", "exited"}:
            continue
        rows.append(dict(item))
    return rows


def newest_quote_age_hours(
    positions: Iterable[Mapping[str, Any]], now: datetime
) -> tuple[Optional[float], Optional[float]]:
    ages = []
    for item in positions:
        observed = parse_dt(item.get("current_price_updated_at"))
        if observed is not None:
            ages.append(max(0.0, (now - observed).total_seconds() / 3600.0))
    if not ages:
        return None, None
    return min(ages), max(ages)


def health_receipt(
    portfolio: Mapping[str, Any], policy: Mapping[str, Any], now: datetime
) -> Dict[str, Any]:
    positions = active_positions(portfolio)
    cash = finite(portfolio.get("cash_pln"))
    reported = finite(portfolio.get("total_value_pln"))
    calculated = cash + sum(finite(item.get("current_value_pln")) for item in positions)
    accounting_gap = abs(reported - calculated)
    updated = parse_dt(portfolio.get("last_updated_at"))
    age_hours = (
        max(0.0, (now - updated).total_seconds() / 3600.0)
        if updated is not None
        else None
    )
    min_quote_age, max_quote_age = newest_quote_age_hours(positions, now)
    ids = [str(item.get("id") or "") for item in positions]
    errors = []
    if reported <= 0:
        errors.append("NON_POSITIVE_PORTFOLIO_VALUE")
    if cash < -0.01:
        errors.append("NEGATIVE_CASH")
    if accounting_gap > 0.10:
        errors.append("ACCOUNTING_INVARIANT_FAILED")
    if len(ids) != len(set(ids)):
        errors.append("DUPLICATE_POSITION_ID")
    if any(finite(item.get("current_value_pln"), -1.0) < 0 for item in positions):
        errors.append("INVALID_POSITION_VALUE")
    max_price_age = finite(policy.get("monitoring_max_price_age_hours"), 6.0)
    # Workflow schedules are hourly.  A 2h tolerance prevents false alarms while
    # still detecting a broken publication loop quickly during market windows.
    freshness_limit = max(2.0, max_price_age)
    if age_hours is None or age_hours > freshness_limit:
        errors.append("STALE_MARK_TO_MARKET")

    return {
        "status": "ACTIVE" if not errors else "DEGRADED",
        "errors": errors,
        "last_updated_at": portfolio.get("last_updated_at"),
        "valuation_age_hours": round(age_hours, 3) if age_hours is not None else None,
        "active_positions": len(positions),
        "cash_pln": round(cash, 2),
        "total_value_pln": round(reported, 2),
        "calculated_total_value_pln": round(calculated, 2),
        "accounting_gap_pln": round(accounting_gap, 4),
        "freshest_quote_age_hours": (
            round(min_quote_age, 3) if min_quote_age is not None else None
        ),
        "stalest_quote_age_hours": (
            round(max_quote_age, 3) if max_quote_age is not None else None
        ),
    }


def active_order_exists(orders: Mapping[str, Any]) -> bool:
    return any(
        str(item.get("status") or "") in ACTIVE_ORDER_STATUSES
        for item in orders.get("orders", []) or []
    )


def choose_cash_candidate(
    analysis: Mapping[str, Any],
    held_ids: set[str],
    policy: Mapping[str, Any],
) -> tuple[Optional[Dict[str, Any]], list[Dict[str, Any]]]:
    confidence_floor = max(
        finite(policy.get("minimum_confidence"), 0.65),
        finite(policy.get("probationary_minimum_confidence"), 0.70),
    )
    return_floor = finite(policy.get("target_annual_return"), 0.10)
    drawdown_ceiling = finite(policy.get("max_expected_drawdown"), 0.25)
    considered = []
    eligible = []
    for row in analysis.get("candidates", []) or []:
        item = dict(row)
        instrument_id = str(item.get("instrument_id") or "")
        confidence = finite(item.get("confidence_score"))
        expected_return = finite(item.get("expected_return_base"), -1.0)
        expected_drawdown = finite(item.get("expected_drawdown"), 1.0)
        risk_adjusted = finite(item.get("risk_adjusted_score"), -99.0)
        checks = {
            "not_already_held": bool(instrument_id and instrument_id not in held_ids),
            "candidate_eligible": bool(item.get("eligible_for_rotation")),
            "confidence": confidence >= confidence_floor,
            "expected_return": expected_return >= return_floor,
            "expected_drawdown": 0.0 <= expected_drawdown <= drawdown_ceiling,
            "positive_risk_adjusted_score": risk_adjusted > 0.0,
        }
        considered.append(
            {
                "instrument": instrument_id,
                "broker_symbol": item.get("broker_symbol"),
                "final_score": item.get("final_score"),
                "confidence": confidence,
                "expected_return": expected_return,
                "expected_drawdown": expected_drawdown,
                "risk_adjusted_score": risk_adjusted,
                "checks": checks,
                "eligible": all(checks.values()),
            }
        )
        if all(checks.values()):
            eligible.append(item)
    eligible.sort(
        key=lambda item: (
            finite(item.get("risk_adjusted_score"), -99.0),
            finite(item.get("final_score"), -99.0),
            finite(item.get("confidence_score"), 0.0),
        ),
        reverse=True,
    )
    return (dict(eligible[0]) if eligible else None), considered


def cash_deployment_plan(
    portfolio: Mapping[str, Any],
    analysis: Mapping[str, Any],
    pending: Mapping[str, Any],
    orders: Mapping[str, Any],
    operational: Mapping[str, Any],
    policy: Mapping[str, Any],
    now: datetime,
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    total = finite(portfolio.get("total_value_pln"))
    cash = finite(portfolio.get("cash_pln"))
    cash_weight = cash / total if total > 0 else 0.0
    minimum_weight = finite(policy.get("minimum_position_weight"), 0.05)
    minimum_funded_weight = minimum_weight * EXECUTION_COST_SAFETY
    analysis_at = parse_dt(analysis.get("generated_at"))
    analysis_age = (
        max(0.0, (now - analysis_at).total_seconds() / 3600.0)
        if analysis_at is not None
        else None
    )
    analysis_max_age = finite(policy.get("analysis_max_price_age_hours"), 72.0)
    positions = active_positions(portfolio)
    held_ids = {str(item.get("id") or "") for item in positions}
    base = {
        "cash_pln": round(cash, 2),
        "cash_weight": round(cash_weight, 6),
        "minimum_position_weight": minimum_weight,
        "minimum_funded_cash_weight": round(minimum_funded_weight, 6),
        "analysis_generated_at": analysis.get("generated_at"),
        "analysis_age_hours": round(analysis_age, 3) if analysis_age is not None else None,
        "max_analysis_age_hours": analysis_max_age,
        "paper_only": True,
    }

    if total <= 0:
        return {**base, "status": "BLOCKED_INVALID_PORTFOLIO"}, None
    if operational.get("safe_mode"):
        return {**base, "status": "BLOCKED_SAFE_MODE"}, None
    if active_order_exists(orders):
        return {**base, "status": "WAITING_ACTIVE_PAPER_ORDER"}, None
    if len(positions) >= int(policy.get("max_positions") or 12):
        return {**base, "status": "BLOCKED_MAX_POSITIONS"}, None
    if cash_weight < minimum_funded_weight:
        return {
            **base,
            "status": "WAITING_MINIMUM_CASH",
            "cash_shortfall_pln": round(
                max(0.0, total * minimum_funded_weight - cash), 2
            ),
        }, None
    if analysis_age is None or analysis_age > analysis_max_age:
        return {**base, "status": "BLOCKED_STALE_BRACE_ANALYSIS"}, None
    if not bool((analysis.get("optimization") or {}).get("rules_passed")):
        return {**base, "status": "BLOCKED_OPTIMIZATION_RULES"}, None

    candidate, considered = choose_cash_candidate(analysis, held_ids, policy)
    if candidate is None:
        return {
            **base,
            "status": "NO_QUALIFIED_CASH_CANDIDATE",
            "considered_candidates": considered,
        }, None

    asset_cap = (
        finite(policy.get("max_single_stock_weight"), 0.18)
        if str(candidate.get("asset_type") or "").upper() == "STOCK"
        else finite(policy.get("max_broad_etf_weight"), 0.30)
    )
    target_weight = min(
        cash_weight / EXECUTION_COST_SAFETY,
        finite(policy.get("max_probation_new_position_weight"), 0.10),
        asset_cap,
    )
    if target_weight < minimum_weight:
        return {**base, "status": "WAITING_MINIMUM_CASH_AFTER_COSTS"}, None

    instrument_id = str(candidate.get("instrument_id"))
    methodology_version = str(analysis.get("methodology_version") or "brace-portfolio-v3")
    decision_id = deterministic_id(
        "cash-add",
        {
            "date": now.date().isoformat(),
            "instrument": instrument_id,
            "methodology": methodology_version,
        },
    )
    prior = next(
        (
            item
            for item in pending.get("decisions", []) or []
            if str(item.get("decision_id")) == decision_id
        ),
        None,
    )
    if prior and str(prior.get("status") or "") not in TERMINAL_DECISION_STATUSES:
        return {
            **base,
            "status": "SIGNAL_ALREADY_PRESENT",
            "candidate": instrument_id,
            "broker_symbol": candidate.get("broker_symbol"),
            "target_weight": round(target_weight, 6),
            "decision_id": decision_id,
        }, None

    confidence = finite(candidate.get("confidence_score"))
    expected_return = finite(candidate.get("expected_return_base"))
    expected_drawdown = finite(candidate.get("expected_drawdown"))
    decision = {
        "decision_id": decision_id,
        "generated_at": now.isoformat(timespec="seconds"),
        "action": "ADD",
        "instrument": instrument_id,
        "replacement_instrument": None,
        "current_weight": 0.0,
        "proposed_weight": round(target_weight, 6),
        "expected_benefit": round(expected_return, 6),
        "expected_risk": round(expected_drawdown, 6),
        "confidence": round(confidence, 4),
        "rationale_pl": (
            f"Wolna gotówka osiągnęła {cash_weight:.1%} portfela. "
            f"{candidate.get('broker_symbol') or instrument_id} jest najwyżej ocenionym "
            "świeżym kandydatem BRACE spełniającym bramki pewności, oczekiwanej "
            "stopy zwrotu, drawdown i limit nowej pozycji. Zlecenie pozostaje paper-only."
        ),
        "rationale_en": (
            f"Free cash reached {cash_weight:.1%} of the portfolio. "
            f"{candidate.get('broker_symbol') or instrument_id} is the highest-ranked "
            "fresh BRACE candidate passing confidence, expected-return, drawdown and "
            "new-position-size gates. Execution remains paper-only."
        ),
        "data_timestamp": analysis.get("generated_at"),
        "methodology_version": methodology_version,
        "status": "PROPOSED",
        "checks": {
            "cash_funds_minimum_position": True,
            "fresh_brace_analysis": True,
            "candidate_eligible": True,
            "confidence": True,
            "expected_return": True,
            "expected_drawdown": True,
            "optimization_rules": True,
            "paper_only": True,
        },
        "transaction_cost_buffer": finite(policy.get("transaction_cost_buffer"), 0.01),
        "source": "portfolio_10k_guardian_cash_deployment",
    }
    plan = {
        **base,
        "status": "CASH_DEPLOYMENT_SIGNAL_CREATED",
        "candidate": instrument_id,
        "broker_symbol": candidate.get("broker_symbol"),
        "target_weight": round(target_weight, 6),
        "decision_id": decision_id,
        "considered_candidates": considered,
    }
    return plan, decision


def append_cash_decision(
    pending: Dict[str, Any], decision: Mapping[str, Any], now: datetime
) -> Dict[str, Any]:
    updated = dict(pending)
    decisions = [dict(item) for item in pending.get("decisions", []) or []]
    if not any(item.get("decision_id") == decision.get("decision_id") for item in decisions):
        decisions.append(dict(decision))
    updated["decisions"] = decisions
    updated["generated_at"] = now.isoformat(timespec="seconds")
    updated["data_freshness"] = "current"
    source = dict(updated.get("source_metadata") or {})
    source["cash_deployment_guardian"] = "portfolio_10k_guardian.py"
    source["paper_only"] = True
    updated["source_metadata"] = source
    return updated


def main() -> int:
    now = datetime.now(timezone.utc)
    portfolio = read_json(PORTFOLIO_PATH)
    config = read_json(CONFIG_PATH)
    policy = config.get("policy") or {}
    analysis = read_json(ANALYSIS_PATH)
    pending = read_json(PENDING_PATH)
    orders = read_json(ORDERS_PATH)
    operational = read_json(OPERATIONAL_PATH)

    health = health_receipt(portfolio, policy, now)
    plan, decision = cash_deployment_plan(
        portfolio,
        analysis,
        pending,
        orders,
        operational,
        policy,
        now,
    )
    if decision is not None:
        pending = append_cash_decision(pending, decision, now)
        write_json_atomic(PENDING_PATH, pending)

    state = {
        "schema_version": "portfolio-10k-guardian-v1",
        "generated_at": now.isoformat(timespec="seconds"),
        "paper_only": True,
        "real_broker_connected": False,
        "health": health,
        "cash_deployment": plan,
        "decision_written": decision is not None,
    }
    write_json_atomic(STATE_PATH, state)
    print(
        json.dumps(
            {
                "health": health["status"],
                "valuation_age_hours": health["valuation_age_hours"],
                "cash_pln": health["cash_pln"],
                "cash_weight": plan["cash_weight"],
                "cash_plan": plan["status"],
                "decision_written": decision is not None,
            },
            ensure_ascii=False,
        )
    )
    # Accounting corruption is fail-closed.  Staleness itself is repaired by the
    # refresh workflow and therefore does not make this command fail.
    fatal = {
        "NON_POSITIVE_PORTFOLIO_VALUE",
        "NEGATIVE_CASH",
        "ACCOUNTING_INVARIANT_FAILED",
        "DUPLICATE_POSITION_ID",
        "INVALID_POSITION_VALUE",
    }
    return 1 if fatal.intersection(health.get("errors") or []) else 0


if __name__ == "__main__":
    raise SystemExit(main())
