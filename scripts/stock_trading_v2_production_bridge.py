#!/usr/bin/env python3
"""Promote fresh Stock Trading v2 opportunities into the canonical portfolio.

V2 research artifacts are intentionally non-executable.  This bridge is the
production boundary: it revalidates opportunity age, eligibility, live session,
quote freshness and risk geometry before a canonical admission.  It never stops
at rank #1: rejected candidates are audited and the next eligible candidate is
checked until capacity is filled or the frontier is exhausted.
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from scripts import stock_trading_portfolio as portfolio
    from scripts import stock_trading_quote_enricher as quotes
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_portfolio as portfolio
    import stock_trading_quote_enricher as quotes

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data/investments/stock_trading_v2_production_config.json"
STATE_PATH = ROOT / "data/investments/stock_trading_v2_production_state.json"
PORTFOLIO_PATH = ROOT / "data/investments/stock_trading_portfolio.json"
UTC = timezone.utc


def _load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        name = handle.name
    Path(name).replace(path)


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    cfg = _load(path, {})
    if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
        raise RuntimeError("v2 production bridge is disabled or misconfigured")
    if cfg.get("champion_engine") != "v2" or cfg.get("challenger_engine") != "v1":
        raise RuntimeError("Champion Inversion contract is invalid")
    if int(cfg.get("canary_max_open_positions_per_market") or 0) != 1:
        raise RuntimeError("technical canary must be limited to one position per market")
    if int(cfg.get("full_max_open_positions_per_market") or 0) != 3:
        raise RuntimeError("full v2 production cap must be three positions per market")
    if float(cfg.get("maximum_execution_quote_age_minutes") or 0) <= 0:
        raise RuntimeError("execution quote freshness limit must be positive")
    if int(cfg.get("full_cycle_max_new_positions_per_market") or 0) != 3:
        raise RuntimeError("FULL cycle must be able to fill all three market slots")
    if int(cfg.get("healthy_sessions_per_market_required") or 0) != 1:
        raise RuntimeError("technical canary must promote after one healthy cycle per market")
    return cfg


def load_runtime(config: Mapping[str, Any], path: Path = STATE_PATH) -> dict[str, Any]:
    state = _load(path)
    if isinstance(state, dict) and state.get("schema_version") == "stock-trading-v2-production-state-v1":
        state.setdefault("healthy_market_sessions", [])
        state.setdefault("audit", [])
        state.setdefault("phase", str(config.get("initial_phase") or "CANARY").upper())
        return state
    return {
        "schema_version": "stock-trading-v2-production-state-v1",
        "champion_engine": "v2",
        "challenger_engine": "v1",
        "phase": str(config.get("initial_phase") or "CANARY").upper(),
        "healthy_market_sessions": [],
        "promoted_to_full_at": None,
        "last_run_at": None,
        "audit": [],
    }


def candidate_frontier(opportunity: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return every eligible candidate, best utility first, with rank-1 included."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    decision_candidate = ((opportunity.get("decision") or {}).get("candidate") or {})
    raw = []
    if isinstance(decision_candidate, Mapping):
        raw.append(dict(decision_candidate))
    raw.extend(dict(row) for row in (opportunity.get("evaluated_candidates") or []) if isinstance(row, Mapping))
    for row in raw:
        symbol = str(row.get("market_data_symbol") or row.get("symbol") or "").upper()
        if not symbol or symbol in seen or row.get("eligible") is not True:
            continue
        seen.add(symbol)
        rows.append(row)
    rows.sort(key=lambda row: (_float(row.get("utility")) or -1e9, -int(row.get("deep_rank") or 999999)), reverse=True)
    return rows


def _opportunity_age_minutes(opportunity: Mapping[str, Any], now_utc: datetime) -> float | None:
    generated = _parse_iso(opportunity.get("generated_at"))
    if generated is None:
        return None
    return max(0.0, (now_utc - generated).total_seconds() / 60.0)


def _preflight_candidate(
    market: str,
    candidate: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    now_utc: datetime,
    quote_fetcher: Callable[..., dict[str, Any]],
) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
    plan = candidate.get("research_risk_plan") or {}
    risk = _float(plan.get("risk_percent"))
    rr = _float(plan.get("reward_risk"))
    if risk is None or risk <= 0 or risk > float(config["maximum_risk_percent"]):
        return None, "research_risk_invalid", None
    if rr is None or rr < float(config["minimum_reward_risk"]):
        return None, "research_reward_risk_invalid", None
    symbol = str(candidate.get("market_data_symbol") or candidate.get("symbol") or "").upper()
    if not symbol:
        return None, "symbol_missing", None
    try:
        quote = quote_fetcher(symbol, market, now_utc=now_utc)
    except quotes.ExecutionQuoteUnavailable as exc:
        return None, "execution_quote_waiting_fresh", {
            "provider_candidates": list(exc.diagnostics),
            "execution_quote_error": str(exc),
        }
    except Exception as exc:
        return None, f"quote_error:{type(exc).__name__}", None
    if config.get("require_regular_session_for_new_entry") is True and quote.get("market_state") != "REGULAR":
        return None, f"market_not_regular:{quote.get('market_state')}", quote
    capture_age = _float(quote.get("capture_age_seconds"))
    max_age = float(config["maximum_execution_quote_age_minutes"]) * 60.0
    if capture_age is not None and capture_age > max_age:
        return None, "execution_quote_stale", quote
    entry = _float(quote.get("price"))
    if entry is None or entry <= 0:
        return None, "execution_price_invalid", quote
    stop = entry * (1.0 - risk)
    target = entry + (entry - stop) * rr
    if not portfolio.valid_long_risk(entry, stop, target, max_risk_percent=float(config["maximum_risk_percent"])):
        return None, "fresh_risk_geometry_invalid", quote
    selection = {
        "symbol": symbol,
        "ticker": candidate.get("symbol") or symbol,
        "name": candidate.get("name") or candidate.get("symbol") or symbol,
        "sector": candidate.get("sector"),
        "score": round(_float(candidate.get("utility")) or 0.0, 6),
        "research_reference_price": _float(plan.get("reference_price")),
        "execution_price": round(entry, 8),
        "reference_price": round(entry, 8),
        "stop": round(stop, 8),
        "target": round(target, 8),
        "risk_percent": round(risk, 6),
        "reward_risk": round(rr, 6),
        "selection_mode": "V2_PRODUCTION_REVALIDATED",
        "holding_policy": "OPEN_ENDED_MODEL_CONTROLLED",
        "valid_until": None,
        "time_stop": None,
        "early_exit": "Canonical model-controlled SL/TP and thesis invalidation.",
        "v2_deep_rank": candidate.get("deep_rank"),
        "v2_utility": candidate.get("utility"),
        "v2_evidence_status": candidate.get("evidence_status"),
        "market_snapshot": {
            "provider": quote.get("provider"),
            "symbol": symbol,
            "date": now_utc.astimezone(portfolio.MARKET_TZ[market]).date().isoformat(),
            "observed_at": quote.get("observed_at") or quote.get("received_at"),
            "last": round(entry, 8),
            "status": quote.get("market_state"),
            "capture_age_seconds": quote.get("capture_age_seconds"),
            "delay_status": quote.get("delay_status"),
            "is_realtime": quote.get("is_realtime"),
            "execution_quote_policy": quote.get("execution_quote_policy"),
        },
        "execution_quote": {
            "provider": quote.get("provider"),
            "observed_at": quote.get("observed_at"),
            "received_at": quote.get("received_at"),
            "capture_age_seconds": quote.get("capture_age_seconds"),
            "delay_status": quote.get("delay_status"),
            "delay_minutes": quote.get("delay_minutes"),
        },
    }
    return selection, "execution_revalidated", quote


def _candidate_payload(market: str, selection: Mapping[str, Any], opportunity: Mapping[str, Any], now_utc: datetime) -> dict[str, Any]:
    return {
        "schema_version": "stock-trading-v2-production-candidate-v1",
        "policy_version": "stock-trading-v2-champion",
        "date": now_utc.astimezone(portfolio.MARKET_TZ[market]).date().isoformat(),
        "generated_at": opportunity.get("generated_at") or now_utc.isoformat(),
        "decision": "TRANSAKCJA" if market == "GPW" else "TRADE",
        "reason": "Freshly revalidated Stock Trading v2 Champion opportunity.",
        "selection": dict(selection),
        "data_quality": {"status": "healthy", "source": "v2_opportunity_plus_fresh_execution_quote"},
    }


def _tag_opened_position(state: dict[str, Any], market: str, action: Mapping[str, Any], candidate: Mapping[str, Any], quote: Mapping[str, Any] | None, phase: str) -> None:
    if action.get("action") != "open":
        return
    position_id = action.get("position_id")
    row = portfolio.market_state(state, market)
    for position in row.get("open_positions") or []:
        if position.get("position_id") != position_id:
            continue
        position["source_engine"] = "stock_trading_v2"
        position["champion_role"] = phase
        position["v2_deep_rank"] = candidate.get("deep_rank")
        position["v2_utility"] = candidate.get("utility")
        position["v2_evidence_status"] = candidate.get("evidence_status")
        position["entry_validation"] = dict(quote or {})
        break


def _market_session_key(market: str, now_utc: datetime) -> str:
    return f"{market}:{now_utc.astimezone(portfolio.MARKET_TZ[market]).date().isoformat()}"


def process_market(
    canonical: dict[str, Any],
    market: str,
    opportunity: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    runtime: dict[str, Any],
    now_utc: datetime,
    quote_fetcher: Callable[..., dict[str, Any]] = quotes.execution_quote_for_symbol,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    market = market.upper()
    audits: list[dict[str, Any]] = []
    age = _opportunity_age_minutes(opportunity, now_utc)
    if age is None:
        return canonical, [{"market": market, "action": "skip", "reason": "opportunity_timestamp_missing"}], False
    if age > float(config["maximum_opportunity_age_minutes"]):
        return canonical, [{"market": market, "action": "skip", "reason": "opportunity_stale", "age_minutes": round(age, 2)}], False
    if str((opportunity.get("decision") or {}).get("action") or "").upper() != "BUY":
        return canonical, [{"market": market, "action": "cash", "reason": "v2_frontier_did_not_beat_cash"}], True

    phase = str(runtime.get("phase") or "CANARY").upper()
    phase_cap = int(config["canary_max_open_positions_per_market"] if phase == "CANARY" else config["full_max_open_positions_per_market"])
    current_count = len(portfolio.open_positions(canonical, market))
    if current_count >= phase_cap:
        return canonical, [{"market": market, "action": "hold_capacity", "phase": phase, "open_positions": current_count}], True

    production_policy = portfolio.load_policy()
    frontier = candidate_frontier(opportunity)
    if not frontier:
        return canonical, [{"market": market, "action": "cash", "reason": "eligible_frontier_empty"}], True

    healthy = False
    cash = opportunity.get("cash") or {}
    cash_utility = _float(cash.get("utility"))
    edge = _float(cash.get("minimum_new_position_edge_points"))
    minimum_utility = (cash_utility if cash_utility is not None else 50.0) + (edge if edge is not None else 5.0)
    opened_this_cycle = 0
    max_new_this_cycle = 1 if phase == "CANARY" else int(config.get("full_cycle_max_new_positions_per_market") or 3)

    for candidate in frontier:
        if len(portfolio.open_positions(canonical, market)) >= phase_cap or opened_this_cycle >= max_new_this_cycle:
            break
        candidate_utility = _float(candidate.get("utility"))
        symbol = candidate.get("market_data_symbol") or candidate.get("symbol")
        if candidate_utility is None or candidate_utility < minimum_utility:
            audits.append({
                "market": market,
                "symbol": symbol,
                "action": "reject_continue",
                "reason": "candidate_does_not_beat_cash_edge",
                "utility": candidate_utility,
                "minimum_utility": round(minimum_utility, 6),
            })
            continue
        selection, reason, quote = _preflight_candidate(
            market,
            candidate,
            config=config,
            now_utc=now_utc,
            quote_fetcher=quote_fetcher,
        )
        if selection is None:
            if reason == "execution_quote_waiting_fresh":
                audits.append({
                    "market": market,
                    "symbol": symbol,
                    "action": "ready_waiting_fresh_quote",
                    "reason": reason,
                    "quote_diagnostics": (quote or {}).get("provider_candidates") or [],
                })
            else:
                audits.append({"market": market, "symbol": symbol, "action": "reject_continue", "reason": reason})
            continue
        healthy = True
        payload = _candidate_payload(market, selection, opportunity, now_utc)
        canonical, action = portfolio.admit_candidate(
            canonical,
            market,
            payload,
            now=now_utc.astimezone(portfolio.MARKET_TZ[market]),
            policy=production_policy,
            authority="v2_production_bridge",
        )
        action = {**action, "source_engine": "v2", "phase": phase, "deep_rank": candidate.get("deep_rank"), "utility": candidate.get("utility")}
        if action.get("action") == "open":
            # The production-state audit is itself scanned by the NO RETROACTIVE
            # EXECUTION airlock.  Every newly recorded open event therefore needs
            # explicit execution provenance, not only the canonical position.
            opened_at = now_utc.astimezone(portfolio.MARKET_TZ[market]).isoformat()
            position_id = action.get("position_id")
            for position in portfolio.open_positions(canonical, market):
                if position.get("position_id") == position_id and position.get("opened_at"):
                    opened_at = str(position["opened_at"])
                    break
            action["opened_at"] = opened_at
            action["entry_decision_at"] = opened_at
            action["execution_provenance"] = {
                "source": "stock_trading_v2_production_bridge",
                "quote_observed_at": (quote or {}).get("observed_at"),
                "quote_received_at": (quote or {}).get("received_at"),
                "recorded_at": now_utc.isoformat().replace("+00:00", "Z"),
            }
        audits.append(action)
        _tag_opened_position(canonical, market, action, candidate, quote, phase)
        if action.get("action") == "open":
            opened_this_cycle += 1
        # A rejection here is not terminal.  Keep searching the ranked frontier.
        if action.get("action") in {"cash", "candidate_already_reviewed", "hold_existing_symbol"}:
            continue
    return canonical, audits, healthy


def maybe_promote(runtime: dict[str, Any], config: Mapping[str, Any], healthy_sessions: list[str], now_utc: datetime) -> None:
    runtime["healthy_market_sessions"] = sorted(set(healthy_sessions))
    counts = {"GPW": 0, "US": 0}
    for item in runtime["healthy_market_sessions"]:
        if ":" not in item:
            continue
        market = item.split(":", 1)[0]
        if market in counts:
            counts[market] += 1
    runtime["canary_health_counts"] = counts
    if str(runtime.get("phase") or "").upper() != "CANARY" or config.get("auto_promote_canary") is not True:
        return

    required_each = int(config.get("healthy_sessions_per_market_required") or 1)
    if config.get("require_both_markets_before_full") is True:
        if not all(counts[market] >= required_each for market in ("GPW", "US")):
            return
    else:
        if sum(counts.values()) < required_each:
            return

    runtime["phase"] = "FULL"
    runtime["promoted_to_full_at"] = now_utc.isoformat().replace("+00:00", "Z")


def run(opportunity_paths: Mapping[str, Path], *, now_utc: datetime | None = None) -> dict[str, Any]:
    now_utc = (now_utc or datetime.now(UTC)).astimezone(UTC)
    config = load_config()
    runtime = load_runtime(config)
    production_policy = portfolio.load_policy()
    canonical = portfolio.load_state(PORTFOLIO_PATH, now=now_utc, policy=production_policy)
    all_audits: list[dict[str, Any]] = []
    healthy_sessions = list(runtime.get("healthy_market_sessions") or [])

    for market in ("GPW", "US"):
        path = opportunity_paths.get(market)
        opportunity = _load(path, {}) if path else {}
        if not isinstance(opportunity, Mapping) or not opportunity:
            all_audits.append({"market": market, "action": "skip", "reason": "opportunity_file_missing"})
            continue
        canonical, audits, healthy = process_market(
            canonical,
            market,
            opportunity,
            config=config,
            runtime=runtime,
            now_utc=now_utc,
        )
        all_audits.extend(audits)
        if healthy:
            healthy_sessions.append(_market_session_key(market, now_utc))

    maybe_promote(runtime, config, healthy_sessions, now_utc)
    verified = portfolio.verify_state(canonical, production_policy)
    if verified["status"] != "OK":
        raise RuntimeError("canonical portfolio invariant violation: " + "; ".join(verified["errors"]))
    portfolio.save_state(canonical, PORTFOLIO_PATH, now=now_utc)
    runtime["last_run_at"] = now_utc.isoformat().replace("+00:00", "Z")
    runtime["audit"] = (list(runtime.get("audit") or []) + [{"run_at": runtime["last_run_at"], "phase": runtime["phase"], "actions": all_audits}])[-100:]
    _atomic(STATE_PATH, runtime)
    return {"champion": "v2", "phase": runtime["phase"], "actions": all_audits, **verified}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpw-opportunity", type=Path)
    parser.add_argument("--us-opportunity", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        cfg = load_config()
        state = load_runtime(cfg)
        print(json.dumps({"status": "OK", "champion": "v2", "phase": state.get("phase")}, ensure_ascii=False))
        return 0
    result = run({"GPW": args.gpw_opportunity, "US": args.us_opportunity})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
