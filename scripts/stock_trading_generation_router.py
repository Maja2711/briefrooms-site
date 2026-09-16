#!/usr/bin/env python3
"""Production router for the proven Stock Trading generation.

V1 remains the default production entry source. When the generation promotion
state proves V2 for a market, this router uses the current V2 opportunity as the
entry-decision source while retaining the canonical Stock Trading portfolio as
the non-learnable risk/execution kernel.

V2 REPLACE remains separately gated and is therefore never allowed to evict a
production position here. A promoted REPLACE candidate may only enter if the
shared portfolio already has a free slot. Operational V2 failures can fall back
to V1; intentional V2 CASH/HOLD decisions never do.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

try:
    from scripts import stock_trading_generation_promotion as generation
    from scripts import stock_trading_portfolio as portfolio
    from scripts import stock_trading_v2_opportunity_engine as opportunity
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_generation_promotion as generation
    import stock_trading_portfolio as portfolio
    import stock_trading_v2_opportunity_engine as opportunity

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data/investments/stock_trading_generation_config.json"
STATE_PATH = ROOT / "data/investments/stock_trading_generation_state.json"
V1_PATHS = {
    "GPW": ROOT / "data/investments/gpw_daily_pick.json",
    "US": ROOT / "data/investments/us_daily_stock.json",
}
V2_PATHS = {
    "GPW": ROOT / "data/investments/stock_trading_v2_opportunity/gpw.json",
    "US": ROOT / "data/investments/stock_trading_v2_opportunity/us.json",
}
MARKET_TZ = {"GPW": ZoneInfo("Europe/Warsaw"), "US": ZoneInfo("America/New_York")}


def _read(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse(value: Any) -> datetime:
    text = str(value or "").replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _market_symbol(market: str, candidate: Mapping[str, Any]) -> str:
    symbol = str(candidate.get("market_data_symbol") or candidate.get("symbol") or "").strip()
    if market == "GPW" and symbol and "." not in symbol:
        symbol += ".WA"
    return symbol


def active_generation(
    market: str,
    *,
    repo_root: Path = ROOT,
    state: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    market = market.upper()
    config = dict(config or generation.load_config(repo_root / "data/investments/stock_trading_generation_config.json"))
    state = dict(state or generation.load_state(repo_root / "data/investments/stock_trading_generation_state.json"))
    row = (state.get("markets") or {}).get(market) or {}
    if row.get("active_generation") != "v2":
        return "v1", "generation_state_v1"
    current_hash = generation.definition_hash(repo_root, config)
    if row.get("promoted_definition_sha256") != current_hash or row.get("v2_definition_sha256") != current_hash:
        return "v1", "v2_definition_not_proven_fail_closed"
    return "v2", "v2_formally_promoted"


def _v2_fresh(payload: Mapping[str, Any], market: str, *, now: datetime, config: Mapping[str, Any]) -> bool:
    try:
        generated = _parse(payload.get("generated_at"))
    except Exception:
        return False
    age = (now.astimezone(timezone.utc) - generated).total_seconds() / 60.0
    limit = float((((config.get("production") or {}).get("v2_max_opportunity_age_minutes") or {})[market]))
    return -5.0 <= age <= limit and generated.astimezone(MARKET_TZ[market]).date() == now.astimezone(MARKET_TZ[market]).date()


def _fresh_quote(symbol: str, market: str, now: datetime) -> float:
    result = portfolio._chart(symbol, interval="5m", range_value="1d")
    meta = result.get("meta") or {}
    for value in (meta.get("regularMarketPrice"), meta.get("chartPreviousClose"), meta.get("previousClose")):
        number = _finite(value)
        if number is not None and number > 0:
            return number
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    for value in reversed(closes):
        number = _finite(value)
        if number is not None and number > 0:
            return number
    raise RuntimeError(f"fresh quote unavailable for {market}:{symbol}")


def _translated_v2_candidate(
    market: str,
    opportunity_payload: Mapping[str, Any],
    *,
    now: datetime,
    policy: Mapping[str, Any],
    quote_provider: Callable[[str, str, datetime], float],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    opportunity.validate_opportunity(opportunity_payload)
    decision = opportunity_payload.get("decision") or {}
    action = str(decision.get("action") or "")
    if action in {"CASH", "HOLD"}:
        return None, {"action": "v2_no_new_entry", "v2_action": action, "reason": decision.get("reason")}
    if action not in {"BUY", "REPLACE"}:
        raise RuntimeError(f"unsupported promoted V2 action: {action}")
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), Mapping) else {}
    if candidate.get("eligible") is not True:
        raise RuntimeError("promoted V2 candidate is not eligible")
    risk = candidate.get("research_risk_plan") if isinstance(candidate.get("research_risk_plan"), Mapping) else {}
    if risk.get("status") != "VALID_RESEARCH_REFERENCE" or risk.get("execution_ready") is not False:
        raise RuntimeError("promoted V2 candidate has invalid research risk plan")
    reference = _finite(risk.get("reference_price"))
    stop = _finite(risk.get("stop"))
    target = _finite(risk.get("target"))
    if reference is None or stop is None or target is None or not (0 < stop < reference < target):
        raise RuntimeError("promoted V2 frozen risk geometry invalid")
    symbol = _market_symbol(market, candidate)
    if not symbol:
        raise RuntimeError("promoted V2 market symbol missing")
    fresh = float(quote_provider(symbol, market, now))
    if not math.isfinite(fresh) or fresh <= 0:
        raise RuntimeError("promoted V2 fresh quote invalid")
    risk_fraction = (reference - stop) / reference
    frozen_rr = (target - reference) / (reference - stop)
    cfg = policy["markets"][market]
    if risk_fraction <= 0 or risk_fraction > float(cfg["maximum_risk_percent"]):
        raise RuntimeError("promoted V2 risk exceeds canonical maximum")
    rr = max(float(cfg["minimum_reward_risk"]), float(frozen_rr))
    fresh_stop = fresh * (1.0 - risk_fraction)
    fresh_target = fresh + (fresh - fresh_stop) * rr
    if not portfolio.valid_long_risk(fresh, fresh_stop, fresh_target, max_risk_percent=float(cfg["maximum_risk_percent"])):
        raise RuntimeError("promoted V2 fresh risk revalidation failed")
    ticker = str(candidate.get("symbol") or symbol).strip()
    payload = {
        "schema_version": "stock-trading-generation-routed-candidate-v1",
        "policy_version": "stock-trading-v2-formally-promoted-entry-source",
        "date": now.astimezone(MARKET_TZ[market]).date().isoformat(),
        "generated_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "decision": "TRANSAKCJA" if market == "GPW" else "TRADE",
        "reason": "formally_promoted_v2_entry_decision",
        "source_generation": "v2",
        "selection": {
            "symbol": ticker,
            "ticker": ticker,
            "market_data_symbol": symbol,
            "name": candidate.get("name"),
            "score": float(candidate.get("utility") or 0.0),
            "reference_price": fresh,
            "market_snapshot": {"last": fresh},
            "stop": fresh_stop,
            "target": fresh_target,
            "risk_percent": risk_fraction,
            "reward_risk": rr,
            "holding_policy": "OPEN_ENDED_MODEL_CONTROLLED",
            "valid_until": None,
            "time_stop": None,
            "selection_mode": "V2_FORMALLY_PROMOTED_ENTRY_SOURCE",
            "generation_route": {
                "v2_action": action,
                "source_opportunity_sha256": opportunity_payload.get("opportunity_sha256"),
                "source_generated_at": opportunity_payload.get("generated_at"),
                "execution_revalidated_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "fresh_quote": fresh,
                "replacement_authority_enabled": False,
            },
        },
    }
    return payload, {"action": "v2_candidate_execution_revalidated", "v2_action": action, "symbol": ticker, "fresh_quote": fresh}


def _admit_v2(
    state: Mapping[str, Any],
    market: str,
    payload: Mapping[str, Any],
    *,
    now: datetime,
    policy: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    updated = copy.deepcopy(dict(state))
    row = portfolio.market_state(updated, market)
    selection = payload.get("selection") or {}
    symbol = str(selection.get("symbol") or "").upper()
    if any(str(position.get("symbol") or "").upper() == symbol for position in portfolio.open_positions(updated, market)):
        return updated, {"action": "hold_existing_symbol", "market": market, "symbol": symbol, "generation": "v2"}
    if portfolio.available_slots(updated, market, policy) <= 0:
        return updated, {
            "action": "portfolio_full",
            "market": market,
            "generation": "v2",
            "reason": "shared_kernel_cap_3_replacement_not_yet_authorized",
            "requested_v2_action": (selection.get("generation_route") or {}).get("v2_action"),
        }
    position = portfolio.position_from_candidate(market, payload, now=now, market_cfg=policy["markets"][market])
    position["source_generation"] = "v2"
    position["source_generation_revision"] = "formally_promoted"
    row["open_positions"] = portfolio.open_positions(updated, market) + [position]
    row["last_candidate_key"] = portfolio.candidate_key(payload)
    row["last_candidate_decision"] = payload.get("decision")
    row["last_candidate_reason"] = "formally_promoted_v2_entry_admitted"
    return updated, {"action": "open", "market": market, "generation": "v2", "position_id": position["position_id"], "symbol": symbol}


def apply_entry_source(
    state: Mapping[str, Any],
    market: str,
    *,
    now: datetime,
    policy: Mapping[str, Any],
    generation_state: Mapping[str, Any],
    config: Mapping[str, Any],
    v1_payload: Mapping[str, Any],
    v2_payload: Mapping[str, Any],
    quote_provider: Callable[[str, str, datetime], float] = _fresh_quote,
) -> tuple[dict[str, Any], dict[str, Any]]:
    market = market.upper()
    resolved, reason = active_generation(market, state=generation_state, config=config)
    if resolved == "v1":
        updated, audit = portfolio.admit_candidate(state, market, v1_payload, now=now, policy=policy)
        return updated, {**audit, "generation": "v1", "route_reason": reason}
    try:
        if not _v2_fresh(v2_payload, market, now=now, config=config):
            raise RuntimeError("V2 opportunity snapshot is stale for production routing")
        routed, route_audit = _translated_v2_candidate(
            market, v2_payload, now=now, policy=policy, quote_provider=quote_provider
        )
        if routed is None:
            return copy.deepcopy(dict(state)), {**route_audit, "generation": "v2", "route_reason": reason}
        updated, audit = _admit_v2(state, market, routed, now=now, policy=policy)
        return updated, {**audit, "route": route_audit, "route_reason": reason}
    except Exception as exc:
        if ((config.get("production") or {}).get("v1_operational_fallback_on_v2_failure")) is not True:
            return copy.deepcopy(dict(state)), {"action": "v2_operational_failure_cash", "generation": "v2", "error": f"{type(exc).__name__}:{str(exc)[:180]}"}
        updated, audit = portfolio.admit_candidate(state, market, v1_payload, now=now, policy=policy)
        return updated, {
            **audit,
            "generation": "v1_fallback",
            "route_reason": "promoted_v2_operational_failure",
            "v2_error": f"{type(exc).__name__}:{str(exc)[:180]}",
        }


def _review_positions(state: Mapping[str, Any], market: str, *, now: datetime, policy: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    observations: dict[str, dict[str, Any]] = {}
    audits: list[dict[str, Any]] = []
    for position in portfolio.open_positions(state, market):
        symbol = str(position.get("symbol") or "")
        try:
            observations[symbol] = portfolio._daily_observation(symbol, market, now)
        except Exception as exc:
            audits.append({"action": "observation_error", "market": market, "symbol": symbol, "error": f"{type(exc).__name__}:{str(exc)[:160]}"})
    updated, review_audit = portfolio.review_market(state, market, observations=observations, now=now, policy=policy)
    audits.extend(review_audit)
    return updated, audits


def run(
    *,
    markets: Sequence[str],
    review_positions: bool,
    now: datetime | None = None,
    quote_provider: Callable[[str, str, datetime], float] = _fresh_quote,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    config = generation.load_config(CONFIG_PATH)
    gen_state = generation.load_state(STATE_PATH)
    policy = portfolio.load_policy()
    state = portfolio.load_state(portfolio.STATE_PATH, now=now, policy=policy)
    audits: list[dict[str, Any]] = []
    for market in markets:
        market = market.upper()
        local_now = now.astimezone(MARKET_TZ[market])
        if review_positions:
            state, rows = _review_positions(state, market, now=local_now, policy=policy)
            audits.extend(rows)
        v1 = _read(V1_PATHS[market], {})
        v2 = _read(V2_PATHS[market], {})
        if not isinstance(v1, Mapping):
            v1 = {}
        if not isinstance(v2, Mapping):
            v2 = {}
        state, audit = apply_entry_source(
            state,
            market,
            now=local_now,
            policy=policy,
            generation_state=gen_state,
            config=config,
            v1_payload=v1,
            v2_payload=v2,
            quote_provider=quote_provider,
        )
        audits.append(audit)
    portfolio.save_state(state, portfolio.STATE_PATH, now=now)
    verification = portfolio.verify_state(state, policy)
    if verification.get("status") != "OK":
        raise RuntimeError(f"canonical portfolio verification failed: {verification}")
    return {
        "schema_version": "stock-trading-generation-router-run-v1",
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "markets": list(markets),
        "review_positions": review_positions,
        "active_generation": {market: active_generation(market, state=gen_state, config=config)[0] for market in markets},
        "audits": audits,
        "portfolio": verification,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Route canonical Stock Trading through the proven generation")
    parser.add_argument("--market", choices=["all", "GPW", "US"], default="all")
    parser.add_argument("--mode", choices=["apply", "review-and-apply", "status"], default="apply")
    args = parser.parse_args()
    markets = ("GPW", "US") if args.market == "all" else (args.market,)
    if args.mode == "status":
        config = generation.load_config(CONFIG_PATH)
        state = generation.load_state(STATE_PATH)
        print(json.dumps({market: {"active_generation": active_generation(market, state=state, config=config)[0], "state": state["markets"][market]} for market in markets}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    result = run(markets=markets, review_positions=args.mode == "review-and-apply")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
