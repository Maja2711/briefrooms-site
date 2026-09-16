#!/usr/bin/env python3
"""Freeze Stock Trading v2 Opportunity decisions into the immutable Experience Store.

Continuous discovery may run many times per session. To avoid overweighting the
same intraday observation, this adapter records at most one rejected and one
selected observation per market/symbol/UTC decision date. A later transition
from REJECTED to SELECTED is therefore learnable, while repeated identical
hourly states are not multiplied.
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_deep_evidence as deep
    from scripts import stock_trading_v2_experience_store as store
    from scripts import stock_trading_v2_opportunity_engine as opportunity
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_deep_evidence as deep
    import stock_trading_v2_experience_store as store
    import stock_trading_v2_opportunity_engine as opportunity

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OPPORTUNITY_ROOT = ROOT / "data/investments/stock_trading_v2_opportunity"
DEFAULT_EVIDENCE_ROOT = ROOT / "data/investments/stock_trading_v2_evidence"
SOURCE_ENGINE = "stock-trading-v2-opportunity-engine"
SOURCE_SCHEMA = "stock-trading-v2-portfolio-opportunity-v1"
DEFAULT_CANDIDATES_PER_CYCLE = 5


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def _day(value: Any) -> str:
    text = str(value or "").replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return str(value or "")[:10]


def _existing_keys(root: Path) -> set[tuple[str, str, str, bool]]:
    keys: set[tuple[str, str, str, bool]] = set()
    for path in store.iter_event_files(root):
        try:
            event = _read_json(path)
            contracts.validate_experience_event(event)
        except Exception:
            continue
        source = event.get("source") or {}
        if source.get("engine") != SOURCE_ENGINE:
            continue
        keys.add((
            str(event.get("market") or ""),
            str(event.get("symbol") or ""),
            _day(event.get("decision_at")),
            bool(event.get("selected")),
        ))
    return keys


def _evidence_index(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("symbol") or "").upper(): dict(row)
        for row in payload.get("candidates") or []
        if isinstance(row, Mapping) and str(row.get("symbol") or "").strip()
    }


def _selected_symbol(payload: Mapping[str, Any]) -> str | None:
    decision = payload.get("decision") or {}
    if decision.get("action") not in {"BUY", "REPLACE"}:
        return None
    candidate = decision.get("candidate") or {}
    symbol = str(candidate.get("symbol") or "").upper().strip()
    return symbol or None


def candidate_state(
    evaluated: Mapping[str, Any],
    evidence_row: Mapping[str, Any],
    *,
    selected: bool,
    portfolio_action: str,
    opportunity_reason: str,
) -> dict[str, Any]:
    market = str(evidence_row.get("market") or "")
    risk = deepcopy(evaluated.get("research_risk_plan") or evidence_row.get("research_risk_plan") or {})
    metrics = deepcopy(evaluated.get("evidence_metrics") or evidence_row.get("evidence_metrics") or {})
    return {
        "identity": {
            "name": evaluated.get("name") or evidence_row.get("name"),
            "ticker": evaluated.get("symbol") or evidence_row.get("symbol"),
            "market_data_symbol": evaluated.get("market_data_symbol") or evidence_row.get("market_data_symbol"),
        },
        "decision_path": {
            "producer_decision": "TRADE" if selected else "NO_TRADE",
            "selection_mode": "V2_CONTINUOUS_OPPORTUNITY_FRONTIER",
            "portfolio_action": portfolio_action if selected else "REJECTED_SHADOW",
            "portfolio_reason": opportunity_reason,
            "first_blocking_gate": None if selected else evaluated.get("eligibility_reason"),
        },
        "score_state": {
            "score": evaluated.get("utility"),
            "opportunity_score": evidence_row.get("opportunity_score"),
            "deep_opportunity_score": evidence_row.get("deep_opportunity_score"),
            "deep_rank": evidence_row.get("deep_rank"),
            "evidence_metrics": metrics,
        },
        "market_state": {
            "evidence_status": evaluated.get("evidence_status") or evidence_row.get("evidence_status"),
            "liquidity": deepcopy(evidence_row.get("liquidity") or {}),
            "freshness": deepcopy(evidence_row.get("freshness") or {}),
            "features": deepcopy(evidence_row.get("features") or {}),
        },
        "risk_plan": risk,
        "thesis_state": {
            "evidence_items": [
                {
                    "evidence_id": item.get("evidence_id"),
                    "provider": item.get("provider"),
                    "authority": item.get("authority"),
                    "event_type": item.get("event_type"),
                    "materiality": item.get("materiality"),
                    "direction": item.get("direction"),
                    "title": item.get("title"),
                    "published_at": item.get("published_at"),
                    "url": item.get("url"),
                }
                for item in evidence_row.get("evidence") or []
            ],
            "provider_health": deepcopy(evidence_row.get("provider_health") or {}),
        },
        "evidence_state": {
            "status": evidence_row.get("evidence_status"),
            "metrics": metrics,
        },
        "settlement_eligibility": {
            "eligible": risk.get("status") == "VALID_RESEARCH_REFERENCE",
            "mode": "next_session_open_from_frozen_research_plan",
        },
        "governance": {
            "source_is_shadow": True,
            "production_decision_influence": False,
            "execution_ready": False,
            "no_lookahead": True,
        },
    }


def events_from_snapshots(
    opportunity_payload: Mapping[str, Any],
    evidence_payload: Mapping[str, Any],
    *,
    candidates_per_cycle: int = DEFAULT_CANDIDATES_PER_CYCLE,
    recorded_at: str | None = None,
) -> list[dict[str, Any]]:
    opportunity.validate_opportunity(opportunity_payload)
    deep.validate_deep_evidence(evidence_payload)
    if opportunity_payload.get("source_evidence_sha256") != evidence_payload.get("evidence_sha256"):
        raise contracts.ContractError("opportunity/evidence hash mismatch")
    market = str(opportunity_payload.get("market") or "").upper()
    selected_symbol = _selected_symbol(opportunity_payload)
    evidence_by_symbol = _evidence_index(evidence_payload)
    decision = opportunity_payload.get("decision") or {}
    decision_at = str(opportunity_payload.get("generated_at") or "")
    if not decision_at:
        raise contracts.ContractError("opportunity generated_at is required")
    decision_day = _day(decision_at)
    rows = list(opportunity_payload.get("evaluated_candidates") or [])[: max(1, int(candidates_per_cycle))]
    events: list[dict[str, Any]] = []
    source_sha = str(opportunity_payload.get("opportunity_sha256") or "")
    for evaluated in rows:
        symbol = str(evaluated.get("symbol") or "").upper().strip()
        evidence_row = evidence_by_symbol.get(symbol)
        if not symbol or not evidence_row:
            continue
        market_symbol = str(evaluated.get("market_data_symbol") or evidence_row.get("market_data_symbol") or symbol).strip()
        selected = selected_symbol == symbol
        state = candidate_state(
            evaluated,
            evidence_row,
            selected=selected,
            portfolio_action=str(decision.get("action") or "CASH"),
            opportunity_reason=str(decision.get("reason") or ""),
        )
        # GPW/US legacy admission adapters key off market-specific producer
        # decision labels only for selected candidates.
        state["decision_path"]["producer_decision"] = (
            "TRANSAKCJA" if market == "GPW" and selected else
            "TRADE" if market == "US" and selected else
            "BRAK_TRANSAKCJI" if market == "GPW" else "NO_TRADE"
        )
        events.append(
            contracts.make_experience_event(
                market=market,
                symbol=market_symbol,
                decision_at=decision_at,
                session_date=decision_day,
                selected=selected,
                source_engine=SOURCE_ENGINE,
                source_schema_version=SOURCE_SCHEMA,
                source_policy_version="stock-trading-v2-shadow-opportunity-v1",
                source_payload_sha256=source_sha,
                candidate_state=state,
                recorded_at=recorded_at,
            )
        )
    return events


def ingest(
    opportunity_payload: Mapping[str, Any],
    evidence_payload: Mapping[str, Any],
    *,
    root: Path = store.DEFAULT_STORE_ROOT,
    candidates_per_cycle: int = DEFAULT_CANDIDATES_PER_CYCLE,
) -> dict[str, Any]:
    events = events_from_snapshots(
        opportunity_payload,
        evidence_payload,
        candidates_per_cycle=candidates_per_cycle,
    )
    existing_keys = _existing_keys(root)
    written = skipped_daily_duplicate = existing = 0
    for event in events:
        key = (
            str(event["market"]),
            str(event["symbol"]),
            _day(event["decision_at"]),
            bool(event["selected"]),
        )
        if key in existing_keys:
            skipped_daily_duplicate += 1
            continue
        if store.persist_event(root, event):
            written += 1
            existing_keys.add(key)
        else:
            existing += 1
    return {
        "schema_version": "stock-trading-v2-opportunity-experience-run-v1",
        "market": opportunity_payload.get("market"),
        "events_seen": len(events),
        "events_written": written,
        "events_existing": existing,
        "events_skipped_daily_duplicate": skipped_daily_duplicate,
        "production_decision_influence": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["US", "GPW", "us", "gpw"], required=True)
    parser.add_argument("--opportunity", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--root", type=Path, default=store.DEFAULT_STORE_ROOT)
    parser.add_argument("--candidates", type=int, default=DEFAULT_CANDIDATES_PER_CYCLE)
    args = parser.parse_args()
    market = str(args.market).lower()
    opportunity_path = args.opportunity or (DEFAULT_OPPORTUNITY_ROOT / f"{market}.json")
    evidence_path = args.evidence or (DEFAULT_EVIDENCE_ROOT / f"{market}.json")
    result = ingest(
        _read_json(opportunity_path),
        _read_json(evidence_path),
        root=args.root,
        candidates_per_cycle=args.candidates,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
