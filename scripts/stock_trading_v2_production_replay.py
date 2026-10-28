#!/usr/bin/env python3
"""Frozen-input replay of the canonical production portfolio and bridge.

This is a counterfactual production-POLICY replay, never a broker-fill claim.
Unlike the legacy next-session-open horizon lab, this runner uses only native
recorded quotes and lifecycle observations, with the actual three-slot book,
ratchet, thesis exits and profit runner.  Missing inputs leave evidence pending.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts import stock_trading_portfolio as portfolio
    from scripts import stock_trading_portfolio_analytics as analytics
    from scripts import stock_trading_v2_production_bridge as bridge
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_portfolio as portfolio
    import stock_trading_portfolio_analytics as analytics
    import stock_trading_v2_production_bridge as bridge

ROOT = Path(__file__).resolve().parents[1]
FRAME_ROOT = ROOT / "data/investments/stock_trading_v2_production_replay_frames"
DEFAULT_FRAME_ROOT = FRAME_ROOT
DEFAULT_WATCH_PATH = ROOT / "data/investments/stock_trading_v2_replay_watchlist.json"
FRAME_SCHEMA = "stock-trading-v2-production-input-frame-v1"
REPLAY_SCHEMA = "stock-trading-v2-production-policy-replay-v1"


def payload_sha256(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def production_code_sha256() -> str:
    paths = sorted(ROOT / "scripts" / name for name in (
        "stock_trading_v2_production_bridge.py", "stock_trading_portfolio.py",
        "stock_trading_quote_enricher.py", "stock_trading_v2_reentry_learning.py",
        "stock_trading_portfolio_analytics.py", "stock_trading_v2_production_replay.py",
        "provenance_contract.py", "stock_trading_v2_contracts.py",
        "stock_trading_sourced_metadata.py", "company_logo_resolver.py",
        "stock_trading_session_calendar.py", "nyse_session_calendar.py",
        "stock_trading_v2_replay_capture.py",
    ))
    return payload_sha256({path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})


def _time(value: Any) -> datetime:
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("replay requires timezone-aware timestamps")
    return result.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def active_replay_watches(*, now: datetime, market: str | None = None, root: Path = DEFAULT_WATCH_PATH) -> list[dict[str, Any]]:
    """Research collection only. A watch has no admission or position authority."""
    if not Path(root).exists():
        return []
    payload = json.loads(Path(root).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "stock-trading-v2-replay-watchlist-v1":
        raise ValueError("native replay watchlist schema invalid")
    selected = []
    for item in payload.get("watches") or []:
        body = dict(item)
        digest = body.pop("watch_id", None)
        duration = _time(item["expires_at"]) - _time(item["decision_at"])
        retention = item.get("retention_contract")
        valid_retention = (duration == timedelta(hours=168) and retention is None) or (
            duration == timedelta(days=180) and retention == "FROZEN_PROSPECTIVE_60_SESSIONS_MAX_180_DAYS_V1")
        if (digest != payload_sha256(body) or item.get("market") not in portfolio.MARKET_TZ
                or item.get("symbol") != str(item.get("symbol") or "").upper().strip()
                or not item.get("symbol") or not valid_retention):
            raise ValueError("native replay watch receipt invalid")
        if market is not None and item.get("market") != market.upper():
            continue
        if _time(item["decision_at"]) <= now < _time(item["expires_at"]):
            selected.append(deepcopy(dict(item)))
    return selected


def register_replay_watch(
    *, market: str, symbol: str, decision_at: str | datetime, root: Path = DEFAULT_WATCH_PATH,
    expires_hours: int = 180 * 24, maximum_new_symbols_per_market_per_week: int = 40,
) -> dict[str, Any]:
    """The sole main bridge writer subscribes bounded future input collection.

    Repeated calls do not extend a watch or rewrite its original receipt.
    Lifecycle reads the ledger without admitting any watched instrument.
    """
    market, symbol = market.upper(), symbol.upper().strip()
    moment = decision_at if isinstance(decision_at, datetime) else _time(decision_at)
    if market not in portfolio.MARKET_TZ or not symbol or moment.tzinfo is None:
        raise ValueError("invalid replay watch identity")
    weekly_cap = 2 if expires_hours == 168 else 40
    if expires_hours not in {168, 180 * 24} or not 1 <= maximum_new_symbols_per_market_per_week <= weekly_cap:
        raise ValueError("replay watch requires legacy 168h/2 weekly or bounded 180d/40 weekly")
    path = Path(root)
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": "stock-trading-v2-replay-watchlist-v1", "authority": "NON_EXECUTABLE_RESEARCH_COLLECTION_ONLY", "watches": []}
    if payload.get("schema_version") != "stock-trading-v2-replay-watchlist-v1":
        raise ValueError("native replay watchlist schema invalid")
    active = active_replay_watches(now=moment, market=market, root=path)
    for watch in active:
        enough_remaining = expires_hours == 168 or _time(watch["expires_at"]) - moment >= timedelta(days=120)
        if watch["symbol"] == symbol and enough_remaining and _time(watch["expires_at"]) - _time(watch["decision_at"]) >= timedelta(hours=expires_hours):
            return {"status": "EXISTING", "watch": watch}
    if symbol not in {watch["symbol"] for watch in active} and len({watch["symbol"] for watch in active}) >= 40:
        return {"status": "ACTIVE_CAPACITY_REACHED", "market": market, "maximum_active_symbols": 40}
    week = moment.astimezone(portfolio.MARKET_TZ[market]).isocalendar()[:2]
    same_week = {item["symbol"] for item in payload["watches"] if item["market"] == market and _time(item["decision_at"]).astimezone(portfolio.MARKET_TZ[market]).isocalendar()[:2] == week}
    if symbol not in same_week and len(same_week) >= maximum_new_symbols_per_market_per_week:
        return {"status": "CAPACITY_REACHED", "market": market, "symbols": sorted(same_week)}
    body = {"market": market, "symbol": symbol, "decision_at": _iso(moment), "expires_at": _iso(moment + timedelta(hours=expires_hours)), "purpose": "FUTURE_LIFECYCLE_INPUT_COLLECTION_ONLY"}
    if expires_hours == 180 * 24:
        body["retention_contract"] = "FROZEN_PROSPECTIVE_60_SESSIONS_MAX_180_DAYS_V1"
    watch = {**body, "watch_id": payload_sha256(body)}
    payload["watches"].append(watch)
    portfolio._atomic(path, payload)
    return {"status": "REGISTERED", "watch": deepcopy(watch)}


def capture_frame(
    root: Path = FRAME_ROOT, *, kind: str, observed_at: str | datetime, policy: Mapping[str, Any], initial_state: Mapping[str, Any],
    observations: Mapping[str, Any] | None = None, opportunities: Mapping[str, Any] | None = None,
    quotes: Mapping[str, Any] | None = None, bridge_config: Mapping[str, Any] | None = None,
    runtime: Mapping[str, Any] | None = None, manifest: Mapping[str, Any] | None = None,
    benchmark: Mapping[str, Any] | None = None, market_sessions: Mapping[str, Any] | None = None,
    reentry_inputs: Mapping[str, Any] | None = None,
    risk_metadata: Mapping[str, Any] | None = None,
    session_calendar: Mapping[str, Any] | None = None,
    close_observations: Mapping[str, Any] | None = None,
    collection_coverage: Mapping[str, Any] | None = None,
    decision_started_at: str | datetime | None = None,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    """Append an immutable native-input receipt; this never mutates trade state.

    Only canonical main writers call this at runtime.  Supplying a root supports
    isolated deterministic tests.  Inputs are preserved rather than projected
    from later candles; receipt time is distinct from the decision/input times.
    """
    moment = observed_at if isinstance(observed_at, datetime) else _time(observed_at)
    started = (decision_started_at if isinstance(decision_started_at, datetime) else _time(decision_started_at)) if decision_started_at is not None else moment
    receipt = recorded_at or datetime.now(timezone.utc)
    if moment.tzinfo is None or receipt.tzinfo is None or started.tzinfo is None or moment > receipt or started > moment:
        raise ValueError("native input receipt cannot precede its decision")
    body = {
        "schema_version": FRAME_SCHEMA, "artifact_type": "NON_EXECUTABLE_RESEARCH_INPUT",
        "authority": "research_input_only", "kind": str(kind), "observed_at": _iso(moment),
        "decision_started_at": _iso(started),
        "recorded_at": _iso(receipt), "production_code_sha256": production_code_sha256(),
        "policy_sha256": payload_sha256(policy), "policy": deepcopy(dict(policy)),
        "initial_state": deepcopy(dict(initial_state)), "observations": deepcopy(dict(observations or {})),
        "opportunities": deepcopy(dict(opportunities or {})), "quotes": deepcopy(dict(quotes or {})),
        "bridge_config": deepcopy(dict(bridge_config or {})), "runtime": deepcopy(dict(runtime or {})),
        "manifest": deepcopy(dict(manifest or {})), "benchmark": deepcopy(dict(benchmark or {})),
        "market_sessions": deepcopy(dict(market_sessions or {})),
        "reentry_inputs": deepcopy(dict(reentry_inputs or {})),
        "risk_metadata": deepcopy(dict(risk_metadata or {})),
        "session_calendar": deepcopy(dict(session_calendar or {})),
        "close_observations": deepcopy(dict(close_observations or {})),
        "collection_coverage": deepcopy(dict(collection_coverage or {})),
    }
    digest = payload_sha256(body)
    frame = {**body, "frame_sha256": digest}
    path = Path(root) / moment.astimezone(timezone.utc).date().isoformat() / f"{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(frame, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
    except FileExistsError:
        if json.loads(path.read_text(encoding="utf-8")) != frame:
            raise ValueError(f"immutable native frame conflict: {path}")
    return {"frame": frame, "frame_id": digest, "frame_sha256": digest, "path": str(path)}


def _frame(raw: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy(dict(raw.get("frame") or raw))


def mark_at_sourced_close(state: dict[str, Any], market: str, session: Mapping[str, Any],
                         observations: Mapping[str, Any], *, now: datetime) -> list[dict[str, Any]]:
    """Mark only the virtual book at an exact formed regular-close bar.

    No lifecycle rule, stop, target, quantity, position status or execution fact
    changes. Validate the whole open book before applying any valuation.
    """
    positions = portfolio.open_positions(state, market)
    errors, valuations = [], {}
    try:
        opening, closing = _time(session["regular_start"]), _time(session["regular_end"])
        received = _time(session["received_at"])
        if (session.get("closed") is not True or session.get("state") not in {"POST", "CLOSED"}
                or not session.get("source") or not session.get("provider")
                or not opening < closing <= received <= now
                or session.get("date") != opening.astimezone(portfolio.MARKET_TZ[market]).date().isoformat()):
            raise ValueError("close session invalid")
    except (KeyError, TypeError, ValueError):
        return [{"market": market, "reason": "native_close_valuation_session_invalid"}]
    for position in positions:
        symbol = str(position.get("symbol") or "")
        observation = observations.get(symbol)
        try:
            if (not isinstance(observation, Mapping) or observation.get("symbol") != symbol
                    or observation.get("market") != market or observation.get("currency") != portfolio.POSITION_CURRENCY[market]
                    or not observation.get("provider") or not str(observation.get("source_url") or "").startswith("https://query1.finance.yahoo.com/v8/finance/chart/")):
                raise ValueError("close quote identity missing")
            quote_received = _time(observation["received_at"])
            if not closing <= quote_received <= now:
                raise ValueError("close quote receipt invalid")
            candidates = []
            for bar in observation.get("source_bars") or []:
                start, end, receipt = _time(bar["start"]), _time(bar["end"]), _time(bar["observed_at"])
                if end != closing:
                    continue
                high, low, close = (portfolio._float(bar.get(key)) for key in ("high", "low", "close"))
                if (bar.get("interval_seconds") != 300 or end - start != timedelta(minutes=5)
                        or not opening <= start < end <= receipt <= quote_received or not bar.get("provider")
                        or any(value is None or value <= 0 for value in (high, low, close))
                        or not low <= close <= high):
                    raise ValueError("close bar invalid")
                candidates.append((close, receipt))
            if len(candidates) != 1:
                raise ValueError("exact terminal close bar missing or duplicated")
            opened = portfolio._market_datetime(position.get("opened_at"), market)
            if opened is not None and opened >= closing:
                raise ValueError("position opened after sourced close")
            valuations[str(position["position_id"])] = {"price": candidates[0][0], "regular_end": _iso(closing),
                "received_at": _iso(candidates[0][1]), "provider": observation["provider"],
                "basis": "VIRTUAL_MARK_ONLY_EXACT_FORMED_REGULAR_CLOSE_BAR", "live_execution_claim": False}
        except (KeyError, TypeError, ValueError):
            errors.append({"market": market, "symbol": symbol, "reason": "native_exact_close_bar_missing_or_invalid"})
    if errors:
        return errors
    for position in portfolio.market_state(state, market).get("open_positions") or []:
        value = valuations.get(str(position.get("position_id")))
        if value is not None:
            position["last_mark"] = value["price"]
            position["replay_close_valuation"] = value
    return []


def _validate_frame(frame: Mapping[str, Any], code: str) -> list[str]:
    failures = []
    body = dict(frame)
    digest = body.pop("frame_sha256", None)
    if frame.get("schema_version") != FRAME_SCHEMA or not digest or digest != payload_sha256(body):
        failures.append("native_frame_receipt_missing_or_invalid")
    if frame.get("production_code_sha256") != code:
        failures.append("production_code_fingerprint_mismatch")
    try:
        if _time(frame["recorded_at"]) < _time(frame["observed_at"]):
            failures.append("frame_recorded_before_decision")
        if _time(frame.get("decision_started_at", frame["observed_at"])) > _time(frame["observed_at"]):
            failures.append("frame_decision_start_after_capture")
    except (ValueError, KeyError, TypeError):
        failures.append("frame_timestamps_invalid")
    return failures


def anchor_regular_interval(anchor: Mapping[str, Any], frame: Mapping[str, Any], market: str,
                            *, now: datetime) -> tuple[bool | None, str | None]:
    """Research expectation from the calendar frozen before the trial only.

    This narrows the existing executable-input window on holidays/early closes;
    it grants no new production session or entry authority.
    """
    receipt = (anchor.get("session_calendar") or {}).get(market)
    if not isinstance(receipt, Mapping):
        return None, None
    try:
        body = dict(receipt)
        digest = body.pop("calendar_sha256", None)
        available_at = _time(anchor.get("decision_started_at", anchor["observed_at"]))
        if (digest != payload_sha256(body) or receipt.get("schema_version") != "stock-trading-session-calendar-v1"
                or receipt.get("market") != market or receipt.get("timezone") != str(portfolio.MARKET_TZ[market])
                or not receipt.get("source") or not receipt.get("source_url")
                or not _time(receipt["observed_at"]) <= _time(receipt["received_at"]) <= available_at
                or not _time(receipt["coverage_start"]) <= now < _time(receipt["coverage_end"])):
            raise ValueError("anchor calendar invalid")
        date = now.astimezone(portfolio.MARKET_TZ[market]).date().isoformat()
        sessions = [row for row in receipt.get("sessions") or [] if row.get("date") == date]
        if len(sessions) > 1:
            raise ValueError("duplicate calendar session")
        session = (frame.get("market_sessions") or {}).get(market) or {}
        if session.get("date") == date and session.get("source") and session.get("provider"):
            if (not sessions or _time(session["regular_start"]) != _time(sessions[0]["regular_start"])
                    or _time(session["regular_end"]) != _time(sessions[0]["regular_end"])):
                return None, "provider_calendar_disagreement"
        if not sessions:
            return False, None
        opening, closing = _time(sessions[0]["regular_start"]), _time(sessions[0]["regular_end"])
        if opening >= closing or opening.astimezone(portfolio.MARKET_TZ[market]).date().isoformat() != date:
            raise ValueError("calendar session bounds invalid")
        return opening <= now < closing, None
    except (KeyError, TypeError, ValueError):
        return None, "native_anchor_session_calendar_invalid_or_uncovered"


def _observation_errors(position: Mapping[str, Any], observation: Mapping[str, Any], now: datetime) -> list[str]:
    errors = []
    snapshot = observation.get("snapshot") or {}
    try:
        stamp = _time(snapshot["observed_at"])
        start = _time(snapshot["trigger_window_start"])
        closes_time = _time(observation["closes_as_of"])
        effective = portfolio.risk_geometry_effective_from(position)
        if stamp > now or closes_time > now or start > stamp:
            errors.append("future_or_inverted_lifecycle_input")
        if effective is not None and (start < effective or snapshot.get("post_effective_only") is not True):
            errors.append("unverified_post_effective_trigger_window")
    except (ValueError, KeyError, TypeError):
        errors.append("lifecycle_input_timestamp_missing")
    if not observation.get("closes") or analytics._number(observation.get("atr")) is None:
        errors.append("lifecycle_thesis_or_atr_missing")
    return errors


def project_observation(position: Mapping[str, Any], observation: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """Reproject recorded raw bars against this simulated book's current risk.

    Real and simulated positions may enter or ratchet at different times. The
    real book's already reduced OHLC window cannot stand in for either input.
    Old receipts without raw bars remain usable only when their explicit
    post-effective snapshot passes the usual lifecycle validation.
    """
    result = deepcopy(dict(observation))
    if "source_bars" in observation:
        bars = observation["source_bars"]
        if not isinstance(bars, list):
            raise ValueError("native source bars must be a list")
        result["snapshot"] = portfolio.snapshot_from_source_bars(
            bars, str(position.get("market") or "").upper(), now=now,
            opened_at=position.get("opened_at"), risk_effective_at=position.get("risk_last_changed_at"),
        )
    return result


def _period_economics(snapshot: Mapping[str, Any], initial: Mapping[str, Any], capital: float) -> dict[str, Any]:
    """Subtract pre-window marks/costs from every initial open contribution.

    This keeps NAV, cost totals and winner attribution on the same prospective
    window. An inherited winner's old gain must not become this trial's alpha.
    """
    result = deepcopy(dict(snapshot))
    baseline = {str(item.get("position_id")): item for item in initial.get("positions") or []}
    contributions = []
    for item in result.get("positions") or []:
        previous = baseline.get(str(item.get("position_id"))) or {}
        for key in ("gross_pnl_amount", "entry_cost_amount", "exit_cost_amount", "cost_amount", "net_pnl_amount"):
            item[key] = float(item[key]) - float(previous.get(key) or 0.0)
        contributions.append(item)
    gross = sum(item["gross_pnl_amount"] for item in contributions)
    costs = sum(item["cost_amount"] for item in contributions)
    net = gross - costs
    realized = sum(item["net_pnl_amount"] for item in contributions if item["is_closed"])
    result.update(positions=contributions, gross_pnl_amount=gross, cost_amount=costs,
                  net_pnl_amount=net, net_nav=capital + net, net_return_percent=net / capital * 100.0,
                  realized_net_pnl_amount=realized, unrealized_liquidation_net_pnl_amount=net - realized)
    return result


def replay_portfolio(
    initial_state: Mapping[str, Any], frames: Sequence[Mapping[str, Any]], *,
    policy: Mapping[str, Any], bridge_config: Mapping[str, Any], runtime: Mapping[str, Any] | None = None,
    initial_capital_by_market: Mapping[str, float] | None = None,
    benchmark_start_by_market: Mapping[str, Any] | None = None,
    markets: Sequence[str] | None = None,
    prospective_initial_basis: bool = False,
) -> dict[str, Any]:
    """Run exact canonical functions over strictly chronological native receipts.

    The policy may be the frozen Champion or a bounded prospective Challenger.
    Neither frame membership nor quote/bar observations are recomputed here.
    Caller must separately prove the experiment was registered before outcomes.
    """
    state = deepcopy(dict(initial_state))
    native = [_frame(raw) for raw in frames]
    code = production_code_sha256()
    discovered = {str(market).upper() for frame in native for key in ("observations", "opportunities", "benchmark") for market in (frame.get(key) or {})} | {market for market in ("GPW", "US") if portfolio.open_positions(state, market)}
    explicit_markets = markets is not None
    markets = sorted(discovered if markets is None else {str(market).upper() for market in markets})
    if explicit_markets and not markets:
        raise ValueError("at least one replay market is required")
    if any(market not in portfolio.MARKET_TZ for market in markets):
        raise ValueError("unknown replay market")
    capital = {market: float((initial_capital_by_market or {}).get(market, int(policy["markets"][market]["max_open_positions"]) * 5000.0)) for market in markets}
    baseline_closed = {market: {str(position.get("position_id")) for position in portfolio.market_state(state, market).get("closed_positions") or []} for market in markets}
    def economics(book: Mapping[str, Any], market: str) -> dict[str, Any]:
        if prospective_initial_basis:
            return analytics.prospective_period_economics(book, initial_state, market, policy=policy, initial_capital=capital[market])
        return analytics.market_economics(book, market, initial_capital=capital[market], baseline_closed_ids=baseline_closed[market])
    base_economics = {market: economics(state, market) for market in markets}
    curves: dict[str, list[dict[str, Any]]] = {market: [] for market in markets}
    previous_nav = dict(capital)
    increments, diagnostics, actions = [], [], []
    benchmark_start = deepcopy(dict(benchmark_start_by_market or {}))
    for market in markets:
        if base_economics[market]["status"] != "COMPLETE":
            diagnostics.append({"market": market, "reason": "initial_portfolio_costs_or_marks_missing"})
        model = policy["markets"][market].get("transaction_costs")
        try:
            portfolio.transaction_cost(5000.0, model or {})
        except ValueError:
            diagnostics.append({"market": market, "reason": "explicit_cost_model_missing"})
    run_state = deepcopy(dict(runtime if runtime is not None else (native[0].get("runtime") if native else {}) or {"phase": "FULL"}))
    previous_time = None
    for index, frame in enumerate(native):
        try:
            now = _time(frame.get("observed_at"))
        except (ValueError, TypeError):
            diagnostics.append({"frame": index, "reason": "frame_decision_time_invalid"})
            continue
        if previous_time is not None and now <= previous_time:
            raise ValueError("replay frames must be strictly chronological, without duplicate decisions")
        previous_time = now
        errors = _validate_frame(frame, code)
        diagnostics.extend({"frame": index, "reason": reason} for reason in errors)
        if errors:
            continue  # Never execute an untrusted or changed native input.
        healthy_sessions = list(run_state.get("healthy_market_sessions") or [])
        admission_due = False
        for market in markets:
            if not any(market in (frame.get(key) or {}) for key in ("observations", "opportunities", "benchmark")):
                continue
            observations = (frame.get("observations") or {}).get(market) or {}
            valid = {}
            positions = portfolio.open_positions(state, market)
            # Admission-only frames don't invent a lifecycle review.  Lifecycle
            # frames must cover every actual open position under either policy.
            review_due = market in (frame.get("observations") or {})
            if review_due:
                regular_review = portfolio._regular_session_open(market, now)
                if prospective_initial_basis:
                    expected_regular, calendar_error = anchor_regular_interval(native[0], frame, market, now=now)
                    if calendar_error:
                        diagnostics.append({"frame": index, "market": market, "reason": calendar_error})
                    if expected_regular is not None:
                        regular_review = regular_review and expected_regular
                for position in positions:
                    symbol = str(position.get("symbol") or "")
                    if not regular_review:
                        # The canonical collector cannot obtain an executable
                        # observation outside its existing session contract.
                        # Reproduce its data-error HOLD, without converting a
                        # known closed interval into missing trading evidence.
                        continue
                    observation = observations.get(symbol)
                    if isinstance(observation, Mapping):
                        try:
                            observation = project_observation(position, observation, now)
                            missing = _observation_errors(position, observation, now)
                        except (ValueError, RuntimeError, TypeError, KeyError):
                            missing = ["lifecycle_source_bar_projection_failed"]
                    else:
                        missing = ["lifecycle_observation_missing"]
                    if missing:
                        diagnostics.extend({"frame": index, "market": market, "symbol": symbol, "reason": reason} for reason in missing)
                    else:
                        valid[symbol] = observation
                if positions:
                    state, audit = portfolio.review_market(state, market, observations=valid, now=now.astimezone(portfolio.MARKET_TZ[market]), policy=policy)
                    actions.extend({"frame": index, **item} for item in audit)
                    for item in audit:
                        if item.get("action") == "hold_data_error" and regular_review:
                            diagnostics.append({"frame": index, "market": market, "reason": item.get("reason") or "lifecycle_data_error"})
            opportunity = (frame.get("opportunities") or {}).get(market)
            if isinstance(opportunity, Mapping):
                admission_due = True
                quote_map = (frame.get("quotes") or {}).get(market) or {}
                decision_at = _time(frame.get("decision_started_at", frame["observed_at"]))
                reentry = frame.get("reentry_inputs") or {}
                evidence = (reentry.get("evidence") or {}).get(market)
                features = (reentry.get("features") or {}).get(market) or {}
                charts = (reentry.get("charts") or {}).get(market) or {}
                if evidence is None and any(bridge.reentry_learning.recent_stop(state, market, str(candidate.get("market_data_symbol") or candidate.get("symbol") or ""), now) is not None for candidate in bridge.candidate_frontier(opportunity)):
                    diagnostics.append({"frame": index, "market": market, "reason": "native_reentry_evidence_input_missing"})

                def frozen_reentry_features(requested_market: str, symbol: str, *args: Any, **kwargs: Any) -> Any:
                    if symbol in charts:
                        try:
                            return bridge.reentry_learning.features_from_chart(
                                charts[symbol], requested_market, symbol, *args, **kwargs, decision_at=decision_at)
                        except (ValueError, TypeError, KeyError):
                            diagnostics.append({"frame": index, "market": market, "symbol": symbol, "reason": "native_reentry_chart_invalid"})
                            return None
                    if symbol not in features:
                        diagnostics.append({"frame": index, "market": market, "symbol": symbol, "reason": "native_reentry_features_missing"})
                        return None
                    # Legacy calculated features belong only to the exact
                    # real stop frozen in that frame, never another virtual SL.
                    actual_stop = bridge.reentry_learning.recent_stop(frame.get("initial_state") or {}, market, symbol, now)
                    virtual_stop = args[0] if args else None
                    if actual_stop is None or payload_sha256(actual_stop) != payload_sha256(virtual_stop):
                        diagnostics.append({"frame": index, "market": market, "symbol": symbol, "reason": "native_reentry_features_stop_context_mismatch"})
                        return None
                    return deepcopy(features[symbol])

                def frozen_quote(symbol: str, requested_market: str, **kwargs: Any) -> dict[str, Any]:
                    nonlocal decision_at
                    quote = quote_map.get(symbol)
                    if not isinstance(quote, Mapping):
                        diagnostics.append({"frame": index, "market": market, "symbol": symbol, "reason": "exact_execution_quote_missing"})
                        raise ValueError("exact frozen execution quote missing")
                    try:
                        observed = _time(quote["observed_at"])
                        received = _time(quote["received_at"])
                        decided = _time(quote["decision_at"])
                        if observed > received or received > decided or decided > now or decided < decision_at:
                            raise ValueError("future execution quote")
                    except (ValueError, KeyError, TypeError):
                        diagnostics.append({"frame": index, "market": market, "symbol": symbol, "reason": "execution_quote_time_invalid"})
                        raise ValueError("invalid frozen execution quote time")
                    result = deepcopy(dict(quote))
                    result.setdefault("capture_age_seconds", (received - observed).total_seconds())
                    decision_at = decided
                    return result

                state, audit, healthy = bridge.process_market(
                    state, market, deepcopy(dict(opportunity)), config=deepcopy(dict(bridge_config)),
                    runtime=run_state, now_utc=_time(frame.get("decision_started_at", frame["observed_at"])), quote_fetcher=frozen_quote,
                    production_policy=deepcopy(dict(policy)),
                    production_manifest=deepcopy(dict(frame.get("manifest") or {})),
                    decision_clock=lambda: decision_at,
                    reentry_evidence_by_symbol=deepcopy(dict(evidence or {})),
                    reentry_feature_fetcher=frozen_reentry_features,
                    risk_metadata=deepcopy(dict(frame.get("risk_metadata") or {})),
                )
                actions.extend({"frame": index, **item} for item in audit)
                if healthy:
                    healthy_sessions.append(bridge._market_session_key(market, now))
            if prospective_initial_basis and (market in (frame.get("close_observations") or {}) or frame.get("kind") == "SESSION_CLOSE"):
                close_errors = mark_at_sourced_close(state, market, (frame.get("market_sessions") or {}).get(market) or {},
                    (frame.get("close_observations") or {}).get(market) or {}, now=now)
                if close_errors:
                    diagnostics.extend({"frame": index, **item} for item in close_errors)
                    continue
            snapshot = economics(state, market)
            if snapshot["status"] != "COMPLETE":
                diagnostics.append({"frame": index, "market": market, "reason": "portfolio_economics_unscorable"})
                continue
            if not prospective_initial_basis:
                snapshot = _period_economics(snapshot, base_economics[market], capital[market])
            benchmark = (frame.get("benchmark") or {}).get(market)
            snapshot["benchmark_return_percent"] = None
            if isinstance(benchmark, Mapping):
                price = analytics._number(benchmark.get("price"))
                try:
                    benchmark_valid = price is not None and price > 0 and _time(benchmark.get("observed_at")) <= now and bool(benchmark.get("provider"))
                except (ValueError, TypeError):
                    benchmark_valid = False
                if benchmark_valid:
                    if market not in benchmark_start:
                        benchmark_start[market] = deepcopy(dict(benchmark))
                    start = benchmark_start[market]
                    starting_price = analytics._number(start.get("price")) if isinstance(start, Mapping) else analytics._number(start)
                    if starting_price is not None and starting_price > 0:
                        snapshot["benchmark_return_percent"] = (price / starting_price - 1.0) * 100.0
            point = {"observed_at": _iso(now), **{key: value for key, value in snapshot.items() if key != "positions"}}
            curves[market].append(point)
            increments.append({"observed_at": _iso(now), "market": market, "net_nav": snapshot["net_nav"], "net_increment": snapshot["net_nav"] - previous_nav[market]})
            previous_nav[market] = snapshot["net_nav"]
        if admission_due:
            bridge.maybe_promote(run_state, bridge_config, healthy_sessions, _time(frame.get("decision_started_at", frame["observed_at"])))
    metrics = {}
    period_contributions = {}
    for market in markets:
        final_economics = economics(state, market)
        if final_economics["status"] == "COMPLETE":
            if not prospective_initial_basis:
                final_economics = _period_economics(final_economics, base_economics[market], capital[market])
            period_contributions[market] = deepcopy(final_economics.get("positions") or [])
        else:
            period_contributions[market] = []
        metrics[market] = analytics.curve_metrics(curves[market], initial_capital=capital[market], positions=final_economics.get("positions") or [])
        if not curves[market]:
            diagnostics.append({"market": market, "reason": "no_scorable_portfolio_curve"})
        if not curves[market] or curves[market][-1].get("benchmark_return_percent") is None:
            diagnostics.append({"market": market, "reason": "sourced_benchmark_unavailable"})
    if not native:
        diagnostics.append({"reason": "native_production_frames_unavailable"})
    return {
        "schema_version": REPLAY_SCHEMA, "classification": "COUNTERFACTUAL_PRODUCTION_POLICY_REPLAY",
        "status": "PENDING" if diagnostics else "COMPLETE", "formal_evidence_eligible": not diagnostics,
        "formal_registration_required_separately": True, "live_execution_claim": False,
        "prospective_initial_basis": prospective_initial_basis,
        "input_sha256": payload_sha256({"initial_state": initial_state, "frames": native}),
        "policy_sha256": payload_sha256(policy), "code_sha256": code,
        "cost_model_sha256": payload_sha256({market: policy["markets"][market].get("transaction_costs") for market in markets}),
        "curves_by_market": curves, "metrics_by_market": metrics,
        "period_position_contributions_by_market": period_contributions,
        "increments": increments, "final_state": state, "final_runtime": run_state,
        "diagnostics": diagnostics, "actions": actions,
    }


def compare_policies(
    initial_state: Mapping[str, Any], frames: Sequence[Mapping[str, Any]], *,
    champion_policy: Mapping[str, Any], challenger_policy: Mapping[str, Any], bridge_config: Mapping[str, Any],
    runtime: Mapping[str, Any] | None = None, initial_capital_by_market: Mapping[str, float] | None = None,
    benchmark_start_by_market: Mapping[str, Any] | None = None,
    markets: Sequence[str] | None = None,
    prospective_initial_basis: bool = False,
) -> dict[str, Any]:
    """Pair the same prospective quote tape and whole-portfolio NAV increments."""
    kwargs = dict(bridge_config=bridge_config, runtime=runtime, initial_capital_by_market=initial_capital_by_market, benchmark_start_by_market=benchmark_start_by_market, markets=markets, prospective_initial_basis=prospective_initial_basis)
    champion = replay_portfolio(initial_state, frames, policy=champion_policy, **kwargs)
    challenger = replay_portfolio(initial_state, frames, policy=challenger_policy, **kwargs)
    markets = sorted(set(champion["metrics_by_market"]) | set(challenger["metrics_by_market"]))
    results = {}
    for market in markets:
        control = champion["metrics_by_market"].get(market) or {}
        alternative = challenger["metrics_by_market"].get(market) or {}
        left = {(item["observed_at"], item["market"]): item for item in champion["increments"] if item["market"] == market}
        right = {(item["observed_at"], item["market"]): item for item in challenger["increments"] if item["market"] == market}
        paired = [{"observed_at": key[0], "champion_net_increment": left[key]["net_increment"], "challenger_net_increment": right[key]["net_increment"], "net_delta_amount": right[key]["net_increment"] - left[key]["net_increment"]} for key in sorted(left.keys() & right.keys())]
        results[market] = {"champion": control, "challenger": alternative, "paired_increments": paired,
                           "net_delta_percent": float(alternative.get("net_return_percent") or 0.0) - float(control.get("net_return_percent") or 0.0),
                           "drawdown_delta_percent": float(alternative.get("max_drawdown_percent") or 0.0) - float(control.get("max_drawdown_percent") or 0.0)}
    complete = champion["formal_evidence_eligible"] and challenger["formal_evidence_eligible"]
    return {"schema_version": "stock-trading-v2-paired-production-policy-replay-v1", "status": "COMPLETE" if complete else "PENDING",
            "formal_evidence_eligible": complete, "input_sha256": champion["input_sha256"],
            "code_sha256": champion["code_sha256"], "cost_model_sha256": champion["cost_model_sha256"],
            "champion": champion, "challenger": challenger, "market_results": results}
