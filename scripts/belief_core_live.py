#!/usr/bin/env python3
"""Live shadow orchestrator using small Observation -> Evidence adapters."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from belief_adapter_contract import stable_id, strength_from_return
from belief_core import BeliefCore, BeliefDefinition, iso_z, parse_time
from belief_closed_loop import transform_probability
from nyse_session_calendar import session_for
from belief_liquidity_adapter import LiquidityEvidenceAdapter
from belief_market_data_adapter import Bar, MarketDataAdapter, MarketSnapshot, YahooChartClient
from belief_regime_adapter import RegimeCrossAssetAdapter
from belief_technical_adapter import TechnicalEvidenceAdapter
from belief_v3_candidate_adapter import (
    CANDIDATE_DEFINITIONS,
    READY_CANDIDATE_IDS,
    V3CandidateEvidenceAdapter,
    candidate_market_symbol,
    candidate_outcome_spec,
)
from belief_macro_release_adapter import MACRO_BELIEFS
from belief_macro_calendar_adapter import MacroEventCalendarAdapter
from belief_wes_assets_adapter import (
    WES_ASSET_BELIEFS,
    WESAssetEvidenceAdapter,
    belief_market_symbol,
    coverage_report as wes_asset_coverage_report,
    evaluate_spec as evaluate_wes_asset_spec,
    outcome_spec as wes_asset_outcome_spec,
    required_symbols as required_wes_asset_symbols,
)

NY = ZoneInfo("America/New_York")
MODE = "shadow"
TRADE_EXECUTION_ENABLED = False
POLICY_OUTPUT_ENABLED = False
AUTOMATIC_TUNING_ENABLED = False

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 20)
US_REGULAR_SESSION_OPEN = time(9, 30)
US_REGULAR_SESSION_CLOSE = time(16, 0)
US_REGULAR_SESSION_HOURS = 6.5
US_SESSION_SYMBOLS = frozenset({"SPY", "RSP", "IWM", "^VIX", "HYG", "LQD", "TLT", "UUP"})
FORECAST_SLOTS = (time(10, 0), time(13, 0), time(16, 0))
SLOT_GRACE_MINUTES = 45
RESEARCH_HORIZONS_HOURS = (3, 12, 24, 72, 120)
PRIMARY_RESEARCH_HORIZON_HOURS = 24
CLOCK_HORIZON_LABELS = {3:"3H", 12:"12H", 24:"24H", 72:"3D", 120:"5D"}
SESSION_HORIZON_LABELS = {3:"0.125S", 12:"0.5S", 24:"1S", 72:"3S", 120:"5S"}
PRODUCTION_POLICY_PATH = SCRIPT_DIR.parent / "data" / "investments" / "belief_core_production_overrides.json"

# Live market APIs can publish the current partial bar a second or two after the
# workflow captures its cycle clock. Treat only that tiny same-cycle skew as
# contemporaneous. Larger future timestamps remain untouched and are still
# rejected by BeliefAuditor as look-ahead.
LIVE_INGEST_CLOCK_SKEW_SECONDS = 5.0


def bounded_live_recompute_time(cycle_now: datetime, evidence: Sequence[Any]) -> datetime:
    base = parse_time(cycle_now)
    out = base
    for item in evidence:
        raw = getattr(item, "observed_at", None)
        if not raw:
            continue
        try:
            stamp = parse_time(raw)
        except (TypeError, ValueError):
            continue
        skew = (stamp - base).total_seconds()
        if 0.0 < skew <= LIVE_INGEST_CLOCK_SKEW_SECONDS and stamp > out:
            out = stamp
    return out


def load_production_policy() -> Dict[str, Any]:
    try:
        payload = json.loads(PRODUCTION_POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"overrides": {}}
    return payload if isinstance(payload, dict) else {"overrides": {}}


def production_probability(belief_id: str, raw_probability: float) -> Tuple[float, Optional[Dict[str, Any]]]:
    policy = load_production_policy()
    overrides = policy.get("overrides") or {}
    row = overrides.get(belief_id)
    if not isinstance(row, dict) or not row.get("active"):
        row = overrides.get("__GLOBAL__")
    if not isinstance(row, dict) or not row.get("active"):
        return float(raw_probability), None
    transform = row.get("transform") or {}
    return transform_probability(float(raw_probability), transform), row

SPX_BELIEFS: Tuple[BeliefDefinition, ...] = (
    BeliefDefinition(
        "spx.trend.bullish", "SPX/US equity trend is bullish into the target horizon",
        prior_probability=.50, half_life_hours=18, entity="SPX", domain="trend",
        tags=("shared", "BRACE", "BRACE-SPX", "WES"), horizon_hours=24,
        outcome_rule="spy_close_above_reference",
    ),
    BeliefDefinition(
        "spx.breadth.healthy", "US equity breadth improves into the target horizon",
        prior_probability=.50, half_life_hours=24, entity="SPX", domain="breadth",
        tags=("shared", "BRACE", "BRACE-SPX", "WES"), horizon_hours=24,
        outcome_rule="breadth_ratio_above_reference",
    ),
    BeliefDefinition(
        "spx.volatility.benign", "Equity volatility remains contained into the target horizon",
        prior_probability=.55, half_life_hours=12, entity="SPX", domain="volatility",
        tags=("shared", "BRACE", "BRACE-SPX", "WES"), horizon_hours=24,
        outcome_rule="vix_below_dynamic_cap",
    ),
    BeliefDefinition(
        "spx.liquidity.supportive", "Credit/liquidity conditions remain supportive into the target horizon",
        prior_probability=.52, half_life_hours=36, entity="US_RISK", domain="liquidity",
        tags=("shared", "BRACE", "BRACE-SPX", "WES"), horizon_hours=24,
        outcome_rule="credit_ratio_above_reference",
    ),
    BeliefDefinition(
        "spx.financial_conditions.supportive", "Rates/USD/credit financial conditions remain supportive into the target horizon",
        prior_probability=.50, half_life_hours=48, entity="US_MACRO", domain="macro",
        tags=("shared", "BRACE", "BRACE-SPX", "WES"), horizon_hours=24,
        outcome_rule="financial_conditions_majority_supportive",
    ),
)
BELIEFS: Tuple[BeliefDefinition, ...] = SPX_BELIEFS + WES_ASSET_BELIEFS + CANDIDATE_DEFINITIONS + MACRO_BELIEFS


def floor_half_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0 if dt.minute < 30 else 30, second=0, microsecond=0)


def in_market_window(local_dt: datetime) -> bool:
    session = session_for(local_dt.date())
    if not session["session_open"]:
        return False
    close = datetime.combine(local_dt.date(), session["close_time"], tzinfo=NY)
    end = (close + timedelta(minutes=20)).time()
    return MARKET_OPEN <= local_dt.time().replace(tzinfo=None) <= end


def in_fx_window(local_dt: datetime) -> bool:
    """Approximate the continuous FX week in New York time.

    Sunday after 17:00 NY through Friday before 17:00 NY is treated as the
    tradable EUR/USD week. This is a data-collection window only; EPE remains
    the fill authority.
    """
    weekday = local_dt.weekday()
    clock = local_dt.time().replace(tzinfo=None)
    if weekday == 6:
        return clock >= time(17, 0)
    if weekday in {0, 1, 2, 3}:
        return True
    if weekday == 4:
        return clock < time(17, 0)
    return False


def due_planned_slot(local_dt: datetime, planned: time, already_done: bool) -> bool:
    """Return True while the planned market phase is still live.

    GitHub scheduled workflows can be delayed. We therefore do not use a narrow
    grace period that can silently starve prospective forecasts. A missed slot is
    never backfilled after its phase: 10:00 is valid until 13:00, 13:00 until
    16:00, and 16:00 until MARKET_CLOSE. The forecast timestamp remains the real
    execution time, so no look-ahead or retroactive reconstruction is introduced.
    """
    if already_done or local_dt.weekday() >= 5:
        return False
    planned_dt = datetime.combine(local_dt.date(), planned, tzinfo=NY)
    if local_dt < planned_dt:
        return False
    later_slots = [slot for slot in FORECAST_SLOTS if slot > planned]
    if later_slots:
        end_dt = datetime.combine(local_dt.date(), min(later_slots), tzinfo=NY)
        return local_dt < end_dt
    end_dt = datetime.combine(local_dt.date(), MARKET_CLOSE, tzinfo=NY)
    return local_dt <= end_dt


def _next_us_weekday(day):
    day = day + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _us_session_anchor(when: datetime) -> datetime:
    """Map forecast runtime to the nearest valid regular-session phase.

    Belief collection can legitimately finish a few minutes after 16:00 NY.
    Session-based outcomes must anchor that late run to the 16:00 close rather
    than turning a nominal +1 session forecast into the next day's open.
    """
    local = when.astimezone(NY)
    if local.weekday() >= 5:
        day = local.date()
        while day.weekday() >= 5:
            day += timedelta(days=1)
        return datetime.combine(day, US_REGULAR_SESSION_OPEN, tzinfo=NY)
    clock = local.time().replace(tzinfo=None)
    if clock < US_REGULAR_SESSION_OPEN:
        return datetime.combine(local.date(), US_REGULAR_SESSION_OPEN, tzinfo=NY)
    if clock > US_REGULAR_SESSION_CLOSE:
        return datetime.combine(local.date(), US_REGULAR_SESSION_CLOSE, tzinfo=NY)
    return local


def advance_us_regular_session_equivalent(when: datetime, declared_horizon_hours: float) -> datetime:
    """Advance an elapsed-time horizon in US regular-session equivalents.

    24H == 1 regular US session (6.5 trading hours), 72H == 3 sessions,
    120H == 5 sessions. Short horizons preserve the same proportional fraction
    of a session. Closed overnight/weekend time is never counted as outcome time.
    """
    if declared_horizon_hours <= 0:
        raise ValueError("declared_horizon_hours must be positive")
    cursor = _us_session_anchor(when)
    remaining = float(declared_horizon_hours) / 24.0 * US_REGULAR_SESSION_HOURS * 3600.0
    epsilon = 1e-9
    while remaining > epsilon:
        if cursor.weekday() >= 5:
            day = cursor.date()
            while day.weekday() >= 5:
                day += timedelta(days=1)
            cursor = datetime.combine(day, US_REGULAR_SESSION_OPEN, tzinfo=NY)
        close_dt = datetime.combine(cursor.date(), US_REGULAR_SESSION_CLOSE, tzinfo=NY)
        open_dt = datetime.combine(cursor.date(), US_REGULAR_SESSION_OPEN, tzinfo=NY)
        if cursor < open_dt:
            cursor = open_dt
        if cursor >= close_dt:
            cursor = datetime.combine(_next_us_weekday(cursor.date()), US_REGULAR_SESSION_OPEN, tzinfo=NY)
            continue
        available = (close_dt - cursor).total_seconds()
        if remaining <= available + epsilon:
            return cursor + timedelta(seconds=remaining)
        remaining -= available
        cursor = datetime.combine(_next_us_weekday(cursor.date()), US_REGULAR_SESSION_OPEN, tzinfo=NY)
    return cursor


def horizon_target_plan(spec: Mapping[str, Any], when: datetime, declared_horizon_hours: float) -> Dict[str, Any]:
    """Return the prospective target contract without consulting future data."""
    symbols = required_symbols(spec)
    nominal_elapsed = when + timedelta(hours=declared_horizon_hours)
    session_dependent = any(symbol in US_SESSION_SYMBOLS for symbol in symbols)
    if session_dependent:
        target = advance_us_regular_session_equivalent(when, declared_horizon_hours)
        label = SESSION_HORIZON_LABELS.get(int(declared_horizon_hours), f"{declared_horizon_hours / 24.0:g}S")
        basis = "US_REGULAR_SESSION_EQUIVALENT"
        calibration_bucket = f"{label}_US_SESSION"
        session_equivalent = declared_horizon_hours / 24.0
    else:
        target = nominal_elapsed
        label = CLOCK_HORIZON_LABELS.get(int(declared_horizon_hours), f"{declared_horizon_hours:g}H")
        basis = "ELAPSED_TIME"
        calibration_bucket = label
        session_equivalent = None
    return {
        "target": target,
        "label": label,
        "basis": basis,
        "calibration_bucket": calibration_bucket,
        "declared_horizon_hours": float(declared_horizon_hours),
        "elapsed_nominal_target_at": iso_z(nominal_elapsed),
        "session_equivalent_count": session_equivalent,
        "required_symbols": symbols,
    }


def next_weekday_close(local_dt: datetime) -> datetime:
    day = local_dt.date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, time(16, 0), tzinfo=NY)


def weekly_target(local_dt: datetime) -> datetime:
    return datetime.combine(local_dt.date() + timedelta(days=7), time(16, 0), tzinfo=NY)


def fetch_snapshot(client: YahooChartClient) -> MarketSnapshot:
    return MarketDataAdapter(client=client).fetch_snapshot()


def build_adapter_payload(snapshot: MarketSnapshot) -> Dict[str, Any]:
    adapters = (
        MarketDataAdapter(),
        TechnicalEvidenceAdapter(),
        LiquidityEvidenceAdapter(),
        RegimeCrossAssetAdapter(),
        WESAssetEvidenceAdapter(),
        V3CandidateEvidenceAdapter(),
    )
    observations = []
    evidence = []
    counts: Dict[str, Dict[str, int]] = {}
    for adapter in adapters:
        result = adapter.run(snapshot)
        observations.extend(result.observations)
        evidence.extend(result.evidence)
        counts[result.adapter] = {"observations": len(result.observations), "evidence": len(result.evidence)}
    return {
        "observations": observations,
        "evidence": evidence,
        "adapter_counts": counts,
        "regime": RegimeCrossAssetAdapter.classify(snapshot),
        "wes_asset_coverage": wes_asset_coverage_report(),
    }


def build_market_evidence(snapshot: MarketSnapshot):
    """Backward-compatible helper: evidence now comes from the adapter pipeline."""
    return list(build_adapter_payload(snapshot)["evidence"])


def classify_regime(snapshot: MarketSnapshot) -> str:
    return RegimeCrossAssetAdapter.classify(snapshot)


def outcome_spec(belief_id: str, snapshot: MarketSnapshot) -> Dict[str, Any]:
    if belief_id in READY_CANDIDATE_IDS:
        return candidate_outcome_spec(belief_id, snapshot)
    if belief_id == "spx.trend.bullish":
        return {"kind": "price_above", "symbol": "SPY", "reference": snapshot.latest("SPY")}
    if belief_id == "spx.breadth.healthy":
        return {"kind": "ratio_above", "numerator": "RSP", "denominator": "SPY", "reference": snapshot.ratio("RSP", "SPY")}
    if belief_id == "spx.volatility.benign":
        reference = snapshot.latest("^VIX")
        return {"kind": "value_below", "symbol": "^VIX", "reference": reference, "threshold": max(20.0, reference * 1.10)}
    if belief_id == "spx.liquidity.supportive":
        return {"kind": "ratio_above", "numerator": "HYG", "denominator": "LQD", "reference": snapshot.ratio("HYG", "LQD")}
    if belief_id == "spx.financial_conditions.supportive":
        return {"kind": "majority_supportive", "reference": {
            "TLT": snapshot.latest("TLT"), "HYG": snapshot.latest("HYG"), "UUP": snapshot.latest("UUP")}}
    if belief_id in {"eurusd.macro_surprise.supportive", "eurusd.policy_differential.supportive"}:
        symbol = belief_market_symbol(belief_id)
        return {"kind": "price_above", "symbol": symbol, "reference": snapshot.latest(symbol)}
    if belief_id.startswith("eurusd.") or belief_id.startswith("btc."):
        return wes_asset_outcome_spec(belief_id, snapshot)
    raise KeyError(belief_id)


def evaluate_spec(spec: Mapping[str, Any], values: Mapping[str, float]) -> bool:
    kind = spec["kind"]
    if kind == "price_above":
        return float(values[str(spec["symbol"])]) > float(spec["reference"])
    if kind == "ratio_above":
        ratio = float(values[str(spec["numerator"])]) / float(values[str(spec["denominator"])])
        return ratio > float(spec["reference"])
    if kind == "value_below":
        return float(values[str(spec["symbol"])]) <= float(spec["threshold"])
    if kind == "majority_supportive":
        reference = spec["reference"]
        votes = [
            float(values["TLT"]) >= float(reference["TLT"]),
            float(values["HYG"]) >= float(reference["HYG"]),
            float(values["UUP"]) <= float(reference["UUP"]),
        ]
        return sum(bool(value) for value in votes) >= 2
    if kind in {"value_above", "absolute_return_below", "credit_duration_supportive"}:
        return evaluate_wes_asset_spec(spec, values)
    raise ValueError(f"unknown outcome spec: {kind}")


def required_symbols(spec: Mapping[str, Any]) -> List[str]:
    kind = spec["kind"]
    if kind in {"price_above", "value_below"}:
        return [str(spec["symbol"])]
    if kind == "ratio_above":
        return [str(spec["numerator"]), str(spec["denominator"])]
    if kind == "majority_supportive":
        return ["TLT", "HYG", "UUP"]
    if kind in {"value_above", "absolute_return_below", "credit_duration_supportive"}:
        return required_wes_asset_symbols(spec)
    raise ValueError(str(kind))


def target_values(client: YahooChartClient, spec: Mapping[str, Any], target_at: datetime, now: datetime,
                  live_snapshot: Optional[MarketSnapshot],
                  bars_cache: Optional[Dict[Tuple[str, str, str], Optional[List[Bar]]]] = None) -> Optional[Dict[str, float]]:
    # Settlement must use the first actual bar at/after the frozen Target.
    # Never substitute the latest live snapshot: a delayed workflow run would
    # otherwise score a later market state and violate the frozen contract.
    del live_snapshot  # kept in the signature for compatibility with callers/tests
    symbols = required_symbols(spec)
    values: Dict[str, float] = {}
    cache = bars_cache if bars_cache is not None else {}
    # Resolve the outcome at the declared target, not merely on the same calendar
    # date. Intraday research horizons therefore use intraday bars; long horizons
    # may use hourly bars. The first bar at/after target is the deterministic mark.
    age_hours = max(0.0, (now - target_at).total_seconds() / 3600.0)
    period, interval = ("5d", "5m") if age_hours <= 24 * 5 else ("3mo", "1h")
    tolerance = timedelta(minutes=20) if interval == "5m" else timedelta(hours=2)
    for symbol in symbols:
        cache_key = (symbol, period, interval)
        if cache_key not in cache:
            try:
                cache[cache_key] = list(client.bars(symbol, period, interval))
            except Exception:
                # Fail closed for the whole dependent batch in this cycle.
                # The next scheduler run retries instead of creating a partial,
                # request-order-dependent settlement.
                cache[cache_key] = None
        rows = cache[cache_key]
        if rows is None:
            return None
        candidates = [bar for bar in rows if bar.timestamp >= target_at]
        if not candidates:
            return None
        chosen = min(candidates, key=lambda bar: bar.timestamp)
        symbol_tolerance = tolerance
        # FX/ETF markets can be closed at an absolute research target (weekend/
        # holiday). In that case the contract is the first observable tradable
        # bar after target; BTC remains strict because it trades continuously.
        if symbol != "BTC-USD":
            symbol_tolerance = max(symbol_tolerance, timedelta(hours=72))
        if chosen.timestamp - target_at > symbol_tolerance:
            return None
        values[symbol] = chosen.close
    return values


def load_scheduler(state_dir: Path) -> Dict[str, Any]:
    path = state_dir / "scheduler.json"
    if not path.exists():
        return {"schema_version": 2, "completed_slots": {}, "gaps": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_scheduler(state_dir: Path, payload: Mapping[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "scheduler.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_observations(state_dir: Path, observations) -> int:
    """Append only unseen Observation IDs to private runtime telemetry.

    Stable Observation IDs make workflow retries idempotent rather than silently
    multiplying the empirical sample in `observations.jsonl`.
    """
    path = state_dir / "observations.jsonl"
    existing = set()
    if path.exists():
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            oid = payload.get("observation_id")
            if not oid:
                raise ValueError(f"observations.jsonl line {line_no} has no observation_id")
            existing.add(str(oid))
    written = 0
    with path.open("a", encoding="utf-8") as handle:
        for row in observations:
            if row.observation_id in existing:
                continue
            payload = {
                "observation_id": row.observation_id,
                "adapter": row.adapter,
                "metric": row.metric,
                "entity": row.entity,
                "observed_at": row.observed_at,
                "value": row.value,
                "unit": row.unit,
                "source": row.source,
                "source_type": row.source_type,
                "source_ref": row.source_ref,
                "reliability": row.reliability,
                "independence_cluster": row.independence_cluster,
                "status": row.status,
                "tags": list(row.tags),
                "metadata": dict(row.metadata),
            }
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
            existing.add(row.observation_id)
            written += 1
    return written


def append_world_state(state_dir: Path, core: BeliefCore, when: datetime, regime: str) -> None:
    path = state_dir / "world_state_history.jsonl"
    row = {
        "timestamp": iso_z(when), "regime": regime, "mode": MODE,
        "beliefs": {key: {"probability": value.probability, "confidence": value.confidence,
                          "audit_status": value.audit_status, "domain": value.domain}
                    for key, value in sorted(core.beliefs.items())},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def _belief_ids_for_consumer(core: BeliefCore, consumer: str) -> List[str]:
    if consumer == "WES":
        return sorted(belief_id for belief_id, definition in core.definitions.items() if "WES" in definition.tags)
    if consumer == "WES-ASSET-SHADOW":
        return sorted(
            belief_id for belief_id, definition in core.definitions.items()
            if "WES" in definition.tags and (belief_id.startswith("eurusd.") or belief_id.startswith("btc."))
        )
    if consumer == "BRACE+BRACE-SPX":
        return sorted(
            belief_id for belief_id, definition in core.definitions.items()
            if "BRACE" in definition.tags or "BRACE-SPX" in definition.tags
        )
    if consumer == "BELIEF-V3-CANDIDATE":
        return sorted(
            belief_id for belief_id, definition in core.definitions.items()
            if "v3_candidate" in definition.tags and belief_id in READY_CANDIDATE_IDS
        )
    return []


FORECAST_CONTRACT_VERSION = "decision-lab-forecast-contract-v2"
MODEL_FREEZE_VERSION = "belief-core-v2-shadow-2026-09-27"


def forecast_contract_metadata(snapshot: MarketSnapshot, market_symbol: str, spec: Mapping[str, Any],
                               when: datetime, target: datetime, horizon_hours: float) -> Dict[str, Any]:
    """Immutable T0/target settlement contract for prospective LAB research."""
    symbols = required_symbols(spec)
    t0_values = {symbol: snapshot.latest(symbol) for symbol in symbols}
    continuous = bool(symbols) and all(symbol == "BTC-USD" for symbol in symbols)
    fx_only = bool(symbols) and all(symbol == "EURUSD=X" for symbol in symbols)
    market_calendar = "24/7" if continuous else ("fx_24x5" if fx_only else "tradable_session_first_available")
    return {
        "forecast_contract_version": FORECAST_CONTRACT_VERSION,
        "model_freeze_version": MODEL_FREEZE_VERSION,
        "t0_at": iso_z(when),
        "t0_values": t0_values,
        "nominal_target_at": iso_z(target),
        "horizon_hours": horizon_hours,
        "settlement_rule": "first_bar_at_or_after_nominal_target",
        "settlement_max_delay_hours": 0.333333 if continuous else 72,
        "market_calendar": market_calendar,
        "t1_values_recorded_on_verification": True,
        "shadow_only": True,
        "production_write_authority": False,
        "automatic_promotion": False,
    }


# Same-day Yahoo bars can still be stale by several hours. Do not treat them
# as a confirmed source snapshot or freeze a prospective forecast from them.
SOURCE_BAR_MAX_AGE_MINUTES = 60
SOURCE_BAR_FUTURE_TOLERANCE_MINUTES = 3


def source_bar_is_fresh(snapshot: MarketSnapshot, symbol: str, now: datetime) -> bool:
    try:
        age = (now - snapshot.observed_at(symbol)).total_seconds() / 60.0
        return (-SOURCE_BAR_FUTURE_TOLERANCE_MINUTES <= age <=
                SOURCE_BAR_MAX_AGE_MINUTES)
    except (KeyError, ValueError, TypeError, OverflowError):
        return False


def freeze_set(core: BeliefCore, snapshot: MarketSnapshot, when: datetime, target: datetime,
               consumer: str, slot_key: str, regime: str) -> int:
    count = 0
    for belief_id in _belief_ids_for_consumer(core, consumer):
        market_symbol = belief_market_symbol(belief_id)
        if market_symbol not in snapshot.bars or not source_bar_is_fresh(snapshot, market_symbol, when):
            continue
        forecast_id = stable_id("forecast", consumer, slot_key, belief_id)
        if forecast_id in core.forecasts:
            continue
        try:
            spec = outcome_spec(belief_id, snapshot)
        except (KeyError, ValueError, ZeroDivisionError):
            continue
        if not all(symbol in snapshot.bars for symbol in required_symbols(spec)):
            continue
        raw_probability = float(core.beliefs[belief_id].probability)
        production_p, overlay = production_probability(belief_id, raw_probability)
        metadata = {
            "consumer": consumer,
            "slot_key": slot_key,
            "market_symbol": market_symbol,
            "market_observed_at": iso_z(snapshot.observed_at(market_symbol)),
            "outcome_spec": spec,
            "adapter_contract": "Observation->Evidence/v1",
            "shadow_only": True,
            "trade_execution_enabled": False,
            "policy_output_enabled": False,
            **forecast_contract_metadata(snapshot, market_symbol, spec, when, target, (target - when).total_seconds() / 3600.0),
        }
        if overlay:
            metadata["production_overlay_version"] = overlay.get("version")
            metadata["production_overlay_scope"] = "probability_calibration_only"
        core.capture_forecast(
            belief_id, as_of=when, target_at=target, regime=regime,
            forecast_id=forecast_id, metadata=metadata,
            predicted_probability_override=production_p if overlay else None,
        )
        count += 1
    return count


def freeze_multihorizon_set(core: BeliefCore, snapshot: MarketSnapshot, when: datetime,
                            consumer: str, slot_key: str, regime: str) -> int:
    """Freeze one P/evidence snapshot into clock- or session-aware research horizons."""
    count = 0
    for belief_id in _belief_ids_for_consumer(core, consumer):
        market_symbol = belief_market_symbol(belief_id)
        if market_symbol not in snapshot.bars or not source_bar_is_fresh(snapshot, market_symbol, when):
            continue
        try:
            spec = outcome_spec(belief_id, snapshot)
        except (KeyError, ValueError, ZeroDivisionError):
            continue
        if not all(symbol in snapshot.bars for symbol in required_symbols(spec)):
            continue
        for hours in RESEARCH_HORIZONS_HOURS:
            plan = horizon_target_plan(spec, when, hours)
            target = plan["target"]
            forecast_id = stable_id("forecast", consumer, slot_key, belief_id, f"{hours}h")
            if forecast_id in core.forecasts:
                continue
            raw_probability = float(core.beliefs[belief_id].probability)
            production_p, overlay = production_probability(belief_id, raw_probability)
            metadata = {
                "consumer": consumer, "slot_key": slot_key, "market_symbol": market_symbol,
                "market_observed_at": iso_z(snapshot.observed_at(market_symbol)),
                "outcome_spec": spec, "adapter_contract": "Observation->Evidence/v1",
                "shadow_only": True, "trade_execution_enabled": False, "policy_output_enabled": False,
                "research_horizon_hours": hours, "research_horizon_label": plan["label"],
                "research_horizon_basis": plan["basis"],
                "calibration_horizon_bucket": plan["calibration_bucket"],
                "elapsed_nominal_target_at": plan["elapsed_nominal_target_at"],
                "session_equivalent_count": plan["session_equivalent_count"],
                "primary_research_horizon": hours == PRIMARY_RESEARCH_HORIZON_HOURS,
                "multihorizon_contract": "decision-lab-multihorizon-v2-session-aware",
                **forecast_contract_metadata(snapshot, market_symbol, spec, when, target, hours),
            }
            if overlay:
                metadata["production_overlay_version"] = overlay.get("version")
                metadata["production_overlay_scope"] = "probability_calibration_only"
            core.capture_forecast(
                belief_id, as_of=when, target_at=target, regime=regime,
                forecast_id=forecast_id, metadata=metadata,
                predicted_probability_override=production_p if overlay else None,
            )
            count += 1
    return count


def freeze_v3_candidate_set(core: BeliefCore, snapshot: MarketSnapshot, when: datetime,
                            slot_key: str, regime: str) -> int:
    """Freeze READY v3 candidates as isolated 24H prospective SHADOW forecasts."""
    consumer = "BELIEF-V3-CANDIDATE"
    count = 0
    for belief_id in _belief_ids_for_consumer(core, consumer):
        market_symbol = candidate_market_symbol(belief_id)
        if market_symbol not in snapshot.bars:
            continue
        try:
            spec = candidate_outcome_spec(belief_id, snapshot)
        except (KeyError, ValueError, ZeroDivisionError):
            continue
        if not all(symbol in snapshot.bars for symbol in required_symbols(spec)):
            continue
        plan = horizon_target_plan(spec, when, 24)
        target = plan["target"]
        forecast_id = stable_id("forecast", consumer, slot_key, belief_id, "24h")
        if forecast_id in core.forecasts:
            continue
        metadata = {
            "consumer": consumer,
            "slot_key": slot_key,
            "market_symbol": market_symbol,
            "market_observed_at": iso_z(snapshot.observed_at(market_symbol)),
            "outcome_spec": spec,
            "adapter_contract": "Observation->Evidence/v1",
            "candidate_library_version": "belief-core-v3-candidate-library-v1",
            "candidate_stage": "SHADOW",
            "candidate_isolated_from_control": True,
            "research_horizon_hours": 24,
            "research_horizon_label": plan["label"],
            "research_horizon_basis": plan["basis"],
            "calibration_horizon_bucket": plan["calibration_bucket"],
            "elapsed_nominal_target_at": plan["elapsed_nominal_target_at"],
            "session_equivalent_count": plan["session_equivalent_count"],
            "primary_research_horizon": True,
            "shadow_only": True,
            "trade_execution_enabled": False,
            "policy_output_enabled": False,
            **forecast_contract_metadata(snapshot, market_symbol, spec, when, target, 24),
        }
        metadata["model_freeze_version"] = "belief-core-v3-candidate-library-v1"
        core.capture_forecast(
            belief_id,
            as_of=when,
            target_at=target,
            regime=regime,
            forecast_id=forecast_id,
            metadata=metadata,
        )
        count += 1
    return count


def verify_due(core: BeliefCore, client: YahooChartClient, now: datetime,
               live_snapshot: Optional[MarketSnapshot]) -> int:
    verified_ids = {value.forecast_id for value in core.verifications.values() if value.forecast_id}
    count = 0
    # One market-data fetch per symbol/period/interval per cycle. This prevents
    # duplicate Yahoo requests from causing partial settlement of one forecast batch.
    bars_cache: Dict[Tuple[str, str, str], Optional[List[Bar]]] = {}
    for forecast in sorted(core.forecasts.values(), key=lambda item: item.target_at):
        if forecast.forecast_id in verified_ids or parse_time(forecast.target_at) > now:
            continue
        spec = dict(forecast.metadata.get("outcome_spec") or {})
        if not spec:
            continue
        values = target_values(client, spec, parse_time(forecast.target_at), now, live_snapshot, bars_cache)
        if values is None:
            continue
        outcome = evaluate_spec(spec, values)
        outcome_ref = "yahoo:" + ",".join(required_symbols(spec)) + ":target=" + forecast.target_at
        # Preserve the exact T1 marks used by deterministic settlement. This is
        # research audit metadata only; it cannot alter P or production systems.
        forecast.metadata["t1_values"] = dict(values)
        forecast.metadata["settled_at"] = iso_z(now)
        forecast.metadata["settlement_status"] = "RESOLVED"
        core.verify_forecast(forecast.forecast_id, outcome, verified_at=now,
                             outcome_source="Yahoo Finance chart", outcome_ref=outcome_ref,
                             note="Automatic deterministic shadow verification; forecast contract remains immutable")
        count += 1
    return count


def run_cycle(state_dir: Path, now: datetime, client: YahooChartClient) -> Dict[str, Any]:
    if TRADE_EXECUTION_ENABLED or POLICY_OUTPUT_ENABLED or AUTOMATIC_TUNING_ENABLED:
        raise RuntimeError("Belief Core live adapter safety invariant violated")
    state_dir.mkdir(parents=True, exist_ok=True)
    core = BeliefCore(state_dir)
    core.register_beliefs(BELIEFS)
    scheduler = load_scheduler(state_dir)
    completed = scheduler.setdefault("completed_slots", {})
    local = now.astimezone(NY)
    snapshot: Optional[MarketSnapshot] = None
    evidence_count = observation_count = world_count = forecast_count = wes_count = wes_asset_count = v3_candidate_count = 0
    adapter_counts: Dict[str, Dict[str, int]] = {}
    eurusd_liveness = {
        "attempted": False,
        "status": "not_due",
        "observations": 0,
        "evidence": 0,
    }
    eurusd_calendar_coverage: Dict[str, Any] = {
        "status": "not_due",
        "complete": None,
    }

    # Daily EUR/USD is a 24x5 consumer. Outside the US cash-session collection
    # window, refresh its dedicated market/cross-asset adapter with EUR/USD plus
    # ICE USDX and CBOT Treasury futures. UUP/TLT remain cash-market references
    # and retain their own timestamps; they are never falsely refreshed.
    if in_fx_window(local) and not in_market_window(local):
        eurusd_liveness["attempted"] = True

        calendar_key = f"eurusd-calendar:{local.date().isoformat()}:{local.hour:02d}"
        if calendar_key not in completed:
            calendar = MacroEventCalendarAdapter()
            calendar_result = calendar.run(now)
            observation_count += append_observations(state_dir, calendar_result.observations)
            coverage = calendar.source_status()
            eurusd_calendar_coverage = {
                "status": "ok" if coverage.get("complete") else "coverage_failed",
                **coverage,
            }
            completed[calendar_key] = iso_z(now)
        else:
            eurusd_calendar_coverage = {
                "status": "already_checked_this_hour",
                "complete": None,
            }

        asset_bars: Dict[str, List[Bar]] = {}
        for symbol in ("EURUSD=X", "DX-Y.NYB", "ZT=F", "ZN=F", "ZQ=F", "UUP", "TLT"):
            try:
                rows = list(client.bars(symbol, "10d", "30m"))
            except Exception:
                rows = []
            if rows:
                asset_bars[symbol] = rows
        if asset_bars.get("EURUSD=X"):
            asset_snapshot = MarketSnapshot(asset_bars)
            asset_result = WESAssetEvidenceAdapter().run(asset_snapshot)
            observation_count += append_observations(state_dir, asset_result.observations)
            if asset_result.evidence:
                core.ingest(asset_result.evidence)
                now = bounded_live_recompute_time(now, asset_result.evidence)
                core.recompute(now)
                core.save()
            evidence_count += len(asset_result.evidence)
            adapter_counts["wes_asset_evidence_fx_liveness"] = {
                "observations": len(asset_result.observations),
                "evidence": len(asset_result.evidence),
            }
            eurusd_liveness.update({
                "status": "ok",
                "observations": len(asset_result.observations),
                "evidence": len(asset_result.evidence),
                "symbols": sorted(asset_bars),
            })
        else:
            eurusd_liveness["status"] = "eurusd_market_data_unavailable"

    if in_market_window(local):
        snapshot = fetch_snapshot(client)
        # The date-only condition is insufficient for US-market freshness.
        if snapshot.is_current_session(now) and source_bar_is_fresh(snapshot, "SPY", now):
            payload = build_adapter_payload(snapshot)
            observations = payload["observations"]
            evidence = payload["evidence"]
            observation_count = append_observations(state_dir, observations)
            core.ingest(evidence)
            # Fresh observations are not usable by Epistemic consumers until the
            # BeliefState projection is recomputed. Previously this happened only
            # on selected world/forecast slots, allowing Daily EURUSD to consume a
            # freshly packaged but stale belief projection and fall to artificial
            # 50/0 NO_TRADE. Recompute on every successful market ingest.
            # Bound sub-five-second provider/runtime clock skew without weakening
            # the auditor's rejection of genuinely future-dated evidence.
            now = bounded_live_recompute_time(now, evidence)
            core.recompute(now)
            evidence_count = len(evidence)
            adapter_counts = payload["adapter_counts"]
            regime = payload["regime"]

            half_slot = floor_half_hour(local)
            hour_key = f"world:{local.date().isoformat()}:{half_slot.hour:02d}"
            if half_slot.minute == 0 and hour_key not in completed:
                core.recompute(now)
                append_world_state(state_dir, core, now, regime)
                completed[hour_key] = iso_z(now)
                world_count = 1

            calendar = session_for(local.date())
            for planned in FORECAST_SLOTS:
                if calendar["early_close"] and planned >= calendar["close_time"]:
                    continue  # no synthetic post-close slot on a 13:00 NYSE close
                target = datetime.combine(local.date(), time(16, 0), tzinfo=NY) if planned.hour < 16 else next_weekday_close(local)
                key = f"shared:{local.date().isoformat()}:{planned.hour:02d}{planned.minute:02d}"
                if due_planned_slot(local, planned, key in completed):
                    core.recompute(now)
                    forecast_count += freeze_set(core, snapshot, now, target, "BRACE+BRACE-SPX", key, regime)
                    completed[key] = iso_z(now)

                asset_key = f"wes-assets:{local.date().isoformat()}:{planned.hour:02d}{planned.minute:02d}"
                if due_planned_slot(local, planned, asset_key in completed):
                    core.recompute(now)
                    wes_asset_count += freeze_multihorizon_set(core, snapshot, now, "WES-ASSET-SHADOW", asset_key, regime)
                    completed[asset_key] = iso_z(now)

                candidate_key = f"v3-candidates:{local.date().isoformat()}:{planned.hour:02d}{planned.minute:02d}"
                if due_planned_slot(local, planned, candidate_key in completed):
                    core.recompute(now)
                    v3_candidate_count += freeze_v3_candidate_set(core, snapshot, now, candidate_key, regime)
                    completed[candidate_key] = iso_z(now)

            wes_key = f"wes:{local.date().isoformat()}:1600"
            if local.weekday() == 4 and due_planned_slot(local, time(16, 0), wes_key in completed):
                core.recompute(now)
                wes_count += freeze_set(core, snapshot, now, weekly_target(local), "WES", wes_key, regime)
                completed[wes_key] = iso_z(now)
        else:
            scheduler.setdefault("gaps", []).append({
                "timestamp": iso_z(now), "reason": "no_fresh_current_us_session_bar",
                "symbol": "SPY",
            })

    verified = verify_due(core, client, now, snapshot)
    scheduler["schema_version"] = 2
    scheduler["last_run_at"] = iso_z(now)
    scheduler["last_status"] = {
        "observations_collected": observation_count,
        "evidence_ingested": evidence_count,
        "adapter_counts": adapter_counts,
        "world_state_snapshots": world_count,
        "shared_forecasts_frozen": forecast_count,
        "wes_asset_forecasts_frozen": wes_asset_count,
        "v3_candidate_forecasts_frozen": v3_candidate_count,
        "v3_ready_candidate_count": len(READY_CANDIDATE_IDS),
        "wes_forecasts_frozen": wes_count,
        "forecasts_verified": verified,
        "wes_asset_coverage": wes_asset_coverage_report(),
        "eurusd_24x5_liveness": eurusd_liveness,
        "eurusd_macro_calendar_coverage": eurusd_calendar_coverage,
        "mode": MODE,
    }
    scheduler["gaps"] = scheduler.get("gaps", [])[-100:]
    save_scheduler(state_dir, scheduler)
    core.save(); core.write_dashboard(now)
    return scheduler["last_status"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run BriefRooms Belief Core live shadow collection cycle")
    parser.add_argument("--state-dir", default=os.environ.get("BELIEF_CORE_STATE_DIR", ".belief_runtime/core"))
    parser.add_argument("--now", help="ISO timestamp override for deterministic testing/manual replay")
    args = parser.parse_args()
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    status = run_cycle(Path(args.state_dir), now, YahooChartClient())
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
