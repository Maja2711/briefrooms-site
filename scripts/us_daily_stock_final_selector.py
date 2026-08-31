#!/usr/bin/env python3
"""Deterministic final selector for BriefRooms US Daily Stock.

The normal US selector keeps its richer catalyst/evidence path. This module is
its final participation stage inside the same publication workflow: when the
portfolio is empty and the normal selector did not publish a TRADE after the
09:35 ET analysis window, choose the highest-ranked *valid* stock instead of
ending the session because of soft conviction.

Hard gates remain hard: sufficient history coverage, at most one completed
session of feature-data lag, minimum liquidity, finite ATR/risk geometry,
configured max risk, minimum reward/risk and a fresh current-session quote.
It never opens a second position, never selects randomly and never fabricates a
market price.
"""
from __future__ import annotations

from datetime import date, datetime, time as clock_time
from typing import Any, Callable, Mapping

try:
    from scripts import daily_stock_us_adapter as core_adapter
    from scripts import us_daily_stock as us
    from scripts import us_daily_stock_position_lifecycle as lifecycle
    from scripts import us_daily_stock_runtime as runtime
except ModuleNotFoundError:
    import daily_stock_us_adapter as core_adapter
    import us_daily_stock as us
    import us_daily_stock_position_lifecycle as lifecycle
    import us_daily_stock_runtime as runtime

core_adapter.install()

MAX_HISTORICAL_LAG_SESSIONS = 1
FINAL_ENTRY_CUTOFF = clock_time(15, 50)
MAX_EXECUTION_QUOTE_AGE_MINUTES = 20.0


def _session_lag(feature_day: date, expected: date, config: dict[str, Any]) -> int | None:
    if feature_day == expected:
        return 0
    cursor = expected
    for lag in range(1, MAX_HISTORICAL_LAG_SESSIONS + 1):
        cursor = us.previous_session(cursor, config)
        if feature_day == cursor:
            return lag
    return None


def _feature_day(bars: list[us.Bar], expected: date, config: dict[str, Any]) -> tuple[date, int] | None:
    completed = [bar for bar in (bars or []) if bar.day <= expected]
    if len(completed) < 60:
        return None
    day = completed[-1].day
    lag = _session_lag(day, expected, config)
    return (day, lag) if lag is not None else None


def build_ranked_candidates(config, cache, expected):
    candidates = []
    usable_history = 0
    failures = {}
    lag_counts = {}
    for company in config["universe"]:
        symbol = str(company["symbol"])
        cached = cache.get(symbol)
        if not cached:
            failures[symbol] = "history_unavailable"
            continue
        bars, _meta = cached
        feature = _feature_day(bars, expected, config)
        if feature is None:
            failures[symbol] = "historical_feature_stale_or_insufficient"
            continue
        feature_day, lag = feature
        usable_history += 1
        lag_counts[str(lag)] = lag_counts.get(str(lag), 0) + 1
        try:
            candidate = us.build_candidate(company, bars, feature_day, config)
        except Exception as exc:
            failures[symbol] = f"candidate_error:{type(exc).__name__}"
            continue
        if not candidate:
            failures[symbol] = "liquidity_or_risk_gate"
            continue
        candidate = dict(candidate)
        candidate["historical_feature_session"] = feature_day.isoformat()
        candidate["historical_feature_lag_sessions"] = lag
        candidates.append(candidate)
    coverage = usable_history / max(len(config["universe"]), 1)
    audit = {
        "usable_history_symbols": usable_history,
        "universe_size": len(config["universe"]),
        "market_coverage": round(coverage, 4),
        "historical_lag_counts": lag_counts,
        "screening_failures": failures,
    }
    if coverage < float(config["minimum_data_completeness"]) or not candidates:
        return [], audit
    us.normalize_cross_section(candidates)
    for candidate in candidates:
        candidate["final_score"] = us.composite(candidate, {"catalyst_score": 50.0}, config)
    candidates.sort(
        key=lambda row: (float(row.get("final_score") or 0), float(row.get("quant_pre_score") or 0), str(row.get("symbol") or "")),
        reverse=True,
    )
    audit["ranked_candidates"] = len(candidates)
    return candidates, audit


def _quote_is_fresh(snapshot: Mapping[str, Any], now: datetime) -> bool:
    if str(snapshot.get("date") or "") != now.date().isoformat():
        return False
    try:
        if float(snapshot.get("last") or 0) <= 0:
            return False
        observed = datetime.fromisoformat(str(snapshot.get("observed_at") or "").replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=us.NEW_YORK)
        observed = observed.astimezone(us.NEW_YORK)
    except (TypeError, ValueError):
        return False
    age = (now - observed).total_seconds() / 60
    return -2 <= age <= MAX_EXECUTION_QUOTE_AGE_MINUTES


def _reprice(candidate, snapshot, config):
    try:
        old_ref = float(candidate["reference_price"]); old_stop = float(candidate["stop"])
        reference = float(snapshot["last"]); rr = float(candidate["reward_risk"])
    except (KeyError, TypeError, ValueError):
        return None
    if old_ref <= 0 or reference <= 0 or rr < float(config["minimum_reward_risk"]):
        return None
    risk_fraction = max((old_ref - old_stop) / old_ref, 0.011)
    if not 0 < risk_fraction <= float(config["maximum_risk_percent"]):
        return None
    stop = reference * (1 - risk_fraction)
    target = reference + (reference - stop) * rr
    if not 0 < stop < reference < target:
        return None
    result = dict(candidate)
    result["reference_price"] = us.round2(reference)
    result["entry_zone"] = [us.round2(reference * .997), us.round2(reference * 1.006)]
    result["skip_above"] = result["entry_zone"][1]
    result["stop"] = us.round2(stop); result["target"] = us.round2(target)
    result["market_snapshot"] = dict(snapshot)
    return result


def _hard_failure_payload(current, *, now, config, reason, audit):
    payload = us.base_payload(now, config, "DATA_ERROR", reason)
    payload["locked"] = False
    payload["data_quality"] = {"status": "failed", "failed_stage": "mandatory_daily_final", "final_selection": dict(audit)}
    if isinstance(current, Mapping):
        payload["forced_from_reason"] = current.get("reason")
    return payload


def make_forced_payload(current, *, now, config, cache, opening_fetcher: Callable[[str], Mapping[str, Any]]):
    if not us.is_session_day(now.date(), config) or now.time() < us.parse_clock(config["analysis_not_before"]):
        return None
    action = str((current or {}).get("position_action") or "")
    if (current or {}).get("decision") == "TRADE" or action in {"HOLD", "ROTATE_OPEN", "OPEN", "CLOSED"}:
        return None
    expected = us.previous_session(now.date(), config)
    ranked, audit = build_ranked_candidates(config, cache, expected)
    if float(audit.get("market_coverage") or 0) < float(config["minimum_data_completeness"]):
        return _hard_failure_payload(current, now=now, config=config, reason="US Daily final selector stopped: completed-session data coverage is below the hard minimum.", audit=audit)
    if not ranked:
        return _hard_failure_payload(current, now=now, config=config, reason="US Daily final selector stopped: no stock passed hard liquidity and risk gates.", audit=audit)
    quote_failures = {}
    chosen = None
    selected_rank = 0
    for rank, candidate in enumerate(ranked, 1):
        symbol = str(candidate["symbol"])
        try:
            snapshot = dict(opening_fetcher(symbol))
            if not _quote_is_fresh(snapshot, now):
                quote_failures[symbol] = "stale_or_invalid_current_session_quote"; continue
            chosen = _reprice(candidate, snapshot, config)
            if chosen is None:
                quote_failures[symbol] = "invalid_execution_risk_geometry"; continue
            selected_rank = rank; break
        except Exception as exc:
            quote_failures[symbol] = f"{type(exc).__name__}:{str(exc)[:120]}"
    if chosen is None:
        return _hard_failure_payload(current, now=now, config=config, reason="US Daily final selector stopped: no ranked stock had a fresh executable quote.", audit={**audit, "opening_quote_failures": quote_failures})
    score = float(chosen["final_score"]); target_score = float(config["target_score"])
    snapshot = dict(chosen["market_snapshot"]); source_id = f"market-open:{chosen['symbol']}:{now.date().isoformat()}"
    source = {"id": source_id, "title": f"Yahoo current-session quote — {chosen['symbol']}", "publisher": "Yahoo", "url": f"https://finance.yahoo.com/quote/{chosen['symbol']}", "source_kind": "market_quote", "channel": "Yahoo", "published_at": snapshot.get("observed_at"), "quality": "execution_quote"}
    payload = us.base_payload(now, config, "TRADE", "Final Daily selection: highest-ranked US stock passing hard data, liquidity, risk and fresh execution-price gates.")
    payload["locked"] = True
    payload["selection"] = {
        "symbol": chosen["symbol"], "ticker": chosen["symbol"], "name": chosen["name"], "sector": chosen["sector"],
        "score": us.round2(score), "quant_pre_score": chosen.get("quant_pre_score"), "score_target": target_score,
        "score_target_met": score >= target_score, "conviction": "moderate" if score < target_score else "solid",
        "selection_mode": "MANDATORY_DAILY_FINAL", "reference_price": chosen["reference_price"], "entry_zone": chosen["entry_zone"],
        "skip_above": chosen["skip_above"], "stop": chosen["stop"], "target": chosen["target"], "risk_percent": chosen.get("risk_percent"),
        "reward_risk": chosen["reward_risk"], "valid_until": us.add_sessions(now.date(), 2, config).isoformat(),
        "time_stop": "Maximum 2 regular US sessions from selection.",
        "early_exit": "Exit at SL, setup invalidation or material execution-data deterioration; never widen the SL.",
        "activation": "Post-open setup: enter only inside the stated zone; do not chase above skip_above.",
        "thesis": f"{chosen['name']} has the highest final quantitative ranking among currently valid and executable US Daily candidates.",
        "why_now": "Selection uses relative momentum, market context, liquidity, risk geometry and US-only historical learning. A weak catalyst is not a hard veto in mandatory Daily mode.",
        "risk_factors": ["Mandatory final mode can carry lower conviction than the catalyst-confirmed path.", "Liquidity, max-risk and current-session quote gates remain mandatory.", "Stop loss remains binding and must not be widened after entry."],
        "scores": {**chosen.get("scores", {}), "catalyst": 50.0}, "sources": [source],
        "review": {"approved": True, "mode": "mandatory_daily_final_quant_policy", "reason": "Highest-ranked candidate passing hard data, liquidity, risk and current-session execution-price gates.", "supported_source_ids": [source_id]},
        "market_snapshot": snapshot, "historical_feature_session": chosen.get("historical_feature_session"),
        "historical_feature_lag_sessions": chosen.get("historical_feature_lag_sessions"),
    }
    payload["forced_from_reason"] = (current or {}).get("reason")
    payload["data_quality"] = {"status": "healthy", "expected_session": expected.isoformat(), "final_selection": {**audit, "applied": True, "selected_rank": selected_rank, "selected_symbol": chosen["symbol"], "opening_quote_failures_before_selection": quote_failures, "historical_feature_session": chosen.get("historical_feature_session"), "historical_feature_lag_sessions": chosen.get("historical_feature_lag_sessions")}}
    methodology = payload.setdefault("methodology", {})
    methodology["score_policy"] = "mandatory_daily_best_valid_ranked"
    methodology["hard_admission_gates"] = ["market_data_completeness", "historical_feature_freshness_max_1_session", "minimum_liquidity", "finite_atr_and_price", "fresh_current_session_quote", "bounded_published_risk", "minimum_reward_risk"]
    methodology["mandatory_daily_selection"] = {"enabled": True, "integrated_final_stage": True, "trigger": "empty_portfolio_and_primary_not_trade_after_09_35_ET", "target_score_role": "ranking_target_not_veto", "minimum_median_turnover_usd": float(config["minimum_median_turnover_usd"]), "maximum_published_risk_percent": float(config["maximum_risk_percent"]), "maximum_historical_lag_sessions": 1, "opening_quote_required": True, "final_entry_cutoff": FINAL_ENTRY_CUTOFF.strftime("%H:%M"), "one_open_position": True}
    return payload


def _position_view(position):
    return {"status": "OPEN", "position_id": position.get("position_id"), "opened_at": position.get("opened_at"), "source_history_date": position.get("source_history_date"), "symbol": position.get("symbol"), "entry": position.get("entry"), "stop": position.get("stop"), "target": position.get("target"), "mark": position.get("last_mark"), "unrealized_percent": position.get("unrealized_percent"), "current_r": position.get("current_r")}


def run(*, now=None):
    now = now or us.now_ny(); config = us.load_config(); current = us.load_json(us.PUBLIC_PATH)
    if not us.is_session_day(now.date(), config) or now.time() < us.parse_clock(config["analysis_not_before"]):
        return current if isinstance(current, dict) else us.base_payload(now, config, "PENDING", "Waiting for the US regular session.")
    book = lifecycle.load_or_bootstrap(runtime.BOOK_PATH, us.HISTORY_DIR, now=now)
    if isinstance(book.get("open_position"), Mapping):
        return current if isinstance(current, dict) else us.base_payload(now, config, "DATA_ERROR", "US open-position state is unavailable.")
    if isinstance(current, Mapping) and str(current.get("position_action") or "") == "CLOSED":
        return dict(current)
    if isinstance(current, Mapping) and current.get("decision") == "TRADE":
        return dict(current)
    if now.time() > FINAL_ENTRY_CUTOFF:
        return current if isinstance(current, dict) else us.base_payload(now, config, "DATA_ERROR", "US Daily final-entry window has closed.")
    cache = runtime.prefetch(config)
    payload = make_forced_payload(current if isinstance(current, Mapping) else None, now=now, config=config, cache=cache, opening_fetcher=lambda symbol: us.opening_snapshot(symbol, now=now))
    if payload is None:
        return current if isinstance(current, dict) else us.base_payload(now, config, "DATA_ERROR", "US Daily final selector produced no state.")
    payload.setdefault("data_quality", {})["prefetch"] = runtime.LAST_PREFETCH_AUDIT
    if payload.get("decision") == "TRADE" and lifecycle.activated_at_selection(payload):
        book = lifecycle.open_from_payload(book, payload); lifecycle.save_book(runtime.BOOK_PATH, book, now=now)
        payload["position_action"] = "OPEN"; payload["position"] = _position_view(book["open_position"])
    runtime.publish(payload, now=now)
    return payload


def enforce_daily_contract(payload, *, now, config):
    if not us.is_session_day(now.date(), config) or now.time() < us.parse_clock(config["analysis_not_before"]):
        return
    if str(payload.get("date") or "") != now.date().isoformat():
        raise us.PublicationError("US Daily publication is stale after the analysis window opened.")
    action = str(payload.get("position_action") or "")
    if action == "CLOSED":
        return
    if payload.get("decision") == "TRADE" and (payload.get("selection") or {}).get("symbol"):
        return
    quality = payload.get("data_quality") or {}
    if payload.get("decision") == "DATA_ERROR" and quality.get("failed_stage") in {"mandatory_daily_final", "runtime"}:
        return
    raise us.PublicationError(f"US Daily contract violated: {payload.get('decision')} / {payload.get('reason')}")


def main():
    now = us.now_ny(); payload = run(now=now); enforce_daily_contract(payload, now=now, config=us.load_config())
    print("US_FINAL_SELECTION", now.isoformat(timespec="seconds"), payload.get("position_action") or payload.get("decision"), (payload.get("selection") or {}).get("symbol"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
