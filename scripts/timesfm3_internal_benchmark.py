#!/usr/bin/env python3
"""Private TimesFM 3 directional benchmark for BriefRooms EUR/USD engines.

TimesFM 3 is an external research benchmark only. It has no production,
trading, PnL, sizing, risk-policy or writeback authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Sequence

try:
    from belief_market_data_adapter import Bar, YahooChartClient
    from learning_ledger import append_event, read_events, verify_chain
except ModuleNotFoundError:
    from scripts.belief_market_data_adapter import Bar, YahooChartClient
    from scripts.learning_ledger import append_event, read_events, verify_chain

SCHEMA_VERSION = "briefrooms-timesfm3-internal-benchmark-v1"
MODEL_ID = "google/timesfm-3.0-pytorch"
PACKAGE_VERSION = "3.0.0"
SYMBOL = "EURUSD=X"
INSTRUMENT = "EUR/USD"
INTERVAL = "30m"
RANGE = "60d"
BAR_MINUTES = 30
CONTEXT_LIMIT = 1024
MIN_CONTEXT = 256
DAILY_HORIZON_STEPS = 48
MAX_HORIZON_STEPS = 256
MAX_DECISION_ORIGIN_AGE_MINUTES = 90

ACTIVATION_FILENAME = "timesfm3_internal_activation.json"
LEDGER_FILENAME = "timesfm3_internal_ledger.jsonl"
PENDING_FILENAME = "timesfm3_internal_pending.json"
STATUS_FILENAME = "timesfm3_internal_status.json"

DAILY_SPOT_DEFAULT = Path("data/investments/eurusd_daily_spot.json")
DAILY_HISTORY_DEFAULT = Path("data/investments/eurusd_daily_history.json")
WEEKLY_DIR_DEFAULT = Path("data/investments/weekly")


class ForecastRuntime(Protocol):
    def forecast(self, context: Sequence[float], horizon_steps: int) -> list[float]: ...


@dataclass(frozen=True)
class BenchmarkRequest:
    request_id: str
    source_engine: str
    source_version: str
    source_decision_id: str
    decision_at: str
    engine_direction: str
    target_mode: str
    target_at: Optional[str]
    horizon_steps: int
    source_ref: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "source_engine": self.source_engine,
            "source_version": self.source_version,
            "source_decision_id": self.source_decision_id,
            "decision_at": self.decision_at,
            "engine_direction": self.engine_direction,
            "target_mode": self.target_mode,
            "target_at": self.target_at,
            "horizon_steps": self.horizon_steps,
            "source_ref": self.source_ref,
        }


def parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("timestamp is required")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("naive timestamp is not allowed")
    return dt.astimezone(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def safety_controls() -> dict[str, bool]:
    return {
        "production_decision_influence": False,
        "decision_influence": False,
        "trade_execution": False,
        "production_policy_writeback": False,
        "production_ranking_writeback": False,
        "production_sizing_writeback": False,
        "belief_writeback": False,
        "evidence_weight_writeback": False,
        "automatic_tuning": False,
        "automatic_promotion": False,
        "historical_backfill": False,
        "public_projection": False,
    }


def _assert_zero_authority(authority: Mapping[str, Any]) -> None:
    if set(authority) != set(safety_controls()):
        raise RuntimeError("TimesFM3 benchmark authority contract incomplete")
    bad = [key for key, value in authority.items() if value is not False]
    if bad:
        raise RuntimeError("TimesFM3 benchmark zero-authority invariant violated: " + ",".join(bad))


def ensure_activation(state_dir: Path, *, now: datetime, bootstrap: bool = False) -> dict[str, Any]:
    path = state_dir / ACTIVATION_FILENAME
    existing = _load_json(path)
    if isinstance(existing, dict):
        if existing.get("schema_version") != SCHEMA_VERSION:
            raise RuntimeError("invalid TimesFM3 benchmark activation schema")
        if existing.get("model_id") != MODEL_ID or existing.get("symbol") != SYMBOL or existing.get("interval") != INTERVAL:
            raise RuntimeError("TimesFM3 benchmark contract changed; create a new experiment version")
        parse_time(str(existing.get("activated_at") or ""))
        authority = existing.get("authority") if isinstance(existing.get("authority"), Mapping) else {}
        _assert_zero_authority(authority)
        return existing
    if not bootstrap:
        raise RuntimeError("TimesFM3 benchmark activation missing; refusing silent reset")

    _assert_zero_authority(safety_controls())
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / LEDGER_FILENAME).touch(exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "activated_at": iso_z(now),
        "model_id": MODEL_ID,
        "package_version": PACKAGE_VERSION,
        "symbol": SYMBOL,
        "instrument": INSTRUMENT,
        "interval": INTERVAL,
        "data_provider": "Yahoo Finance chart",
        "purpose": "private external directional benchmark for BriefRooms Daily EURUSD and WES EURUSD",
        "comparison_policy": {
            "daily": "paired_same_decision_origin_24h_48_completed_30m_bars",
            "wes": "paired_same_decision_origin_to_frozen_wes_exit_target",
            "pnl_benchmark": False,
            "directional_skill_only": True,
        },
        "license_gate": {
            "required_repository_variable": "TIMESFM3_RESEARCH_LICENSE_OK",
            "required_value": "true",
            "reason": "TimesFM 3 pretrained weights are restricted to non-commercial/non-production use",
        },
        "anti_hindsight": {
            "historical_backfill": False,
            "decision_must_be_after_activation": True,
            "forecast_must_be_frozen_before_target_outcome": True,
            "model_context_capped_at_decision_time": True,
        },
        "authority": safety_controls(),
    }
    _atomic_json(path, payload)
    _atomic_json(state_dir / PENDING_FILENAME, {"schema_version": SCHEMA_VERSION, "requests": []})
    return payload


def _bar_completion(bar: Bar) -> datetime:
    return parse_time(bar.timestamp) + timedelta(minutes=BAR_MINUTES)


def completed_at_or_before(bars: Sequence[Bar], cutoff: datetime) -> list[Bar]:
    rows = [bar for bar in bars if _bar_completion(bar) <= cutoff]
    return sorted(rows, key=lambda row: parse_time(row.timestamp))


def future_after(bars: Sequence[Bar], cutoff: datetime) -> list[Bar]:
    rows = [bar for bar in bars if _bar_completion(bar) > cutoff]
    return sorted(rows, key=lambda row: parse_time(row.timestamp))


def _direction(value: Any) -> Optional[str]:
    text = str(value or "").strip().upper()
    if text in {"LONG", "BUY", "UP"}:
        return "LONG"
    if text in {"SHORT", "SELL", "DOWN"}:
        return "SHORT"
    return None


def _sign_direction(value: float, eps: float = 1e-12) -> str:
    if value > eps:
        return "LONG"
    if value < -eps:
        return "SHORT"
    return "FLAT"


def _subject_id(request: BenchmarkRequest) -> str:
    return f"timesfm3-benchmark:{request.source_engine}:{request.source_decision_id}"


def _subjects(ledger: Path, event_type: str, prefix: str) -> set[str]:
    return {
        str(row.get("subject_id") or "")
        for row in read_events(ledger)
        if row.get("event_type") == event_type and str(row.get("source_ref") or "").startswith(prefix)
    }


def _forecast_subjects(ledger: Path) -> set[str]:
    return _subjects(ledger, "forecast", "timesfm3-benchmark://forecast/")


def _outcome_subjects(ledger: Path) -> set[str]:
    return _subjects(ledger, "outcome", "timesfm3-benchmark://outcome/")


def _skip_subjects(ledger: Path) -> set[str]:
    return _subjects(ledger, "learning_observation", "timesfm3-benchmark://skip/")


def _pending_payload(state_dir: Path) -> dict[str, Any]:
    payload = _load_json(state_dir / PENDING_FILENAME)
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return {"schema_version": SCHEMA_VERSION, "requests": []}
    requests = payload.get("requests")
    return {"schema_version": SCHEMA_VERSION, "requests": requests if isinstance(requests, list) else []}


def _write_pending(state_dir: Path, requests: Sequence[Mapping[str, Any]]) -> None:
    ordered = sorted((dict(row) for row in requests), key=lambda row: (str(row.get("decision_at")), str(row.get("request_id"))))
    _atomic_json(state_dir / PENDING_FILENAME, {"schema_version": SCHEMA_VERSION, "requests": ordered})


def _daily_request_from_trade(trade: Mapping[str, Any], source: Path) -> Optional[BenchmarkRequest]:
    direction = _direction(trade.get("direction"))
    decision_id = str(trade.get("trade_id") or "").strip()
    opened_at = str(trade.get("opened_at") or "").strip()
    if not direction or not decision_id or not opened_at:
        return None
    try:
        decision_at = parse_time(opened_at)
    except ValueError:
        return None
    return BenchmarkRequest(
        request_id=f"daily:{decision_id}",
        source_engine="daily_eurusd",
        source_version=str(trade.get("engine_version") or "unknown"),
        source_decision_id=decision_id,
        decision_at=iso_z(decision_at),
        engine_direction=direction,
        target_mode="completed_30m_bars",
        target_at=None,
        horizon_steps=DAILY_HORIZON_STEPS,
        source_ref=str(source),
    )


def _daily_requests(*, activation_at: datetime, daily_spot: Path, daily_history: Path) -> list[BenchmarkRequest]:
    candidates: dict[str, BenchmarkRequest] = {}
    spot = _load_json(daily_spot, {})
    if isinstance(spot, Mapping):
        metadata = spot.get("metadata") if isinstance(spot.get("metadata"), Mapping) else {}
        position = metadata.get("position") if isinstance(metadata.get("position"), Mapping) else {}
        request = _daily_request_from_trade(position, daily_spot)
        if request and parse_time(request.decision_at) > activation_at:
            candidates[request.source_decision_id] = request

    history = _load_json(daily_history, {})
    trades = history.get("trades") if isinstance(history, Mapping) else []
    if isinstance(trades, list):
        for trade in trades:
            if not isinstance(trade, Mapping):
                continue
            request = _daily_request_from_trade(trade, daily_history)
            if request and parse_time(request.decision_at) > activation_at:
                candidates.setdefault(request.source_decision_id, request)
    return sorted(candidates.values(), key=lambda row: row.decision_at)


def _latest_weekly_file(weekly_dir: Path) -> Optional[Path]:
    files = sorted(weekly_dir.glob("20??-W??.json"))
    return files[-1] if files else None


def _wes_requests(*, activation_at: datetime, weekly_dir: Path) -> list[BenchmarkRequest]:
    path = _latest_weekly_file(weekly_dir)
    if path is None:
        return []
    payload = _load_json(path, {})
    if not isinstance(payload, Mapping):
        return []

    week_id = str(payload.get("week_id") or path.stem).strip()
    locked = str(payload.get("forecast_locked_at") or payload.get("forecast_created_at") or "").strip()
    if not week_id or not locked:
        return []
    try:
        decision_at = parse_time(locked)
    except ValueError:
        return []
    if decision_at <= activation_at:
        return []

    window = payload.get("market_window") if isinstance(payload.get("market_window"), Mapping) else {}
    target_text = str(window.get("exit_target_local") or "").strip()
    try:
        target_at = parse_time(target_text)
    except ValueError:
        return []
    if target_at <= decision_at:
        return []

    instruments = payload.get("instruments") if isinstance(payload.get("instruments"), list) else []
    eurusd = next(
        (
            row for row in instruments
            if isinstance(row, Mapping)
            and (str(row.get("instrument_id") or "").lower() == "eurusd" or str(row.get("symbol") or "") == SYMBOL)
        ),
        None,
    )
    if not isinstance(eurusd, Mapping):
        return []
    direction = _direction(eurusd.get("direction"))
    if not direction or str(eurusd.get("trade_status") or "").lower() in {"no_trade", "neutral", "flat"}:
        return []

    return [BenchmarkRequest(
        request_id=f"wes:{week_id}:eurusd",
        source_engine="wes_eurusd",
        source_version=str(payload.get("method_version") or payload.get("version") or "unknown"),
        source_decision_id=f"{week_id}:eurusd",
        decision_at=iso_z(decision_at),
        engine_direction=direction,
        target_mode="absolute_target_time",
        target_at=iso_z(target_at),
        horizon_steps=0,
        source_ref=str(path),
    )]


def _resolve_request_horizon(request: BenchmarkRequest, decision_bars: Sequence[Bar]) -> BenchmarkRequest:
    if not decision_bars:
        raise RuntimeError("decision has no completed EUR/USD bar")
    if request.target_mode == "completed_30m_bars":
        return request
    if request.target_mode != "absolute_target_time" or not request.target_at:
        raise RuntimeError("unsupported target mode")
    seconds = (parse_time(request.target_at) - _bar_completion(decision_bars[-1])).total_seconds()
    steps = math.ceil(seconds / (BAR_MINUTES * 60))
    if seconds <= 0 or steps <= 0 or steps > MAX_HORIZON_STEPS:
        raise RuntimeError(f"TimesFM3 WES horizon {steps} outside supported benchmark range")
    return BenchmarkRequest(**{**request.as_dict(), "horizon_steps": steps})


def _target_observable(request: BenchmarkRequest, bars: Sequence[Bar]) -> bool:
    decision_at = parse_time(request.decision_at)
    future = future_after(bars, decision_at)
    if request.target_mode == "completed_30m_bars":
        return len(future) >= request.horizon_steps
    if request.target_mode == "absolute_target_time" and request.target_at and bars:
        return max(_bar_completion(bar) for bar in bars) >= parse_time(request.target_at)
    return False


def _append_skip(ledger: Path, request: BenchmarkRequest, reason: str, **extra: Any) -> None:
    append_event(
        ledger,
        event_type="learning_observation",
        occurred_at=iso_z(datetime.now(timezone.utc)),
        subject_id=_subject_id(request),
        source_ref=f"timesfm3-benchmark://skip/{request.request_id}",
        payload={
            "producer_schema_version": SCHEMA_VERSION,
            "request": request.as_dict(),
            "reason": reason,
            **extra,
            "authority": safety_controls(),
        },
    )


def discover_requests(
    state_dir: Path,
    *,
    bars: Sequence[Bar],
    daily_spot: Path,
    daily_history: Path,
    weekly_dir: Path,
) -> tuple[int, int]:
    activation = ensure_activation(state_dir, now=datetime.now(timezone.utc), bootstrap=False)
    activation_at = parse_time(str(activation["activated_at"]))
    ledger = state_dir / LEDGER_FILENAME
    known = _forecast_subjects(ledger) | _outcome_subjects(ledger) | _skip_subjects(ledger)
    pending = _pending_payload(state_dir)
    existing = {str(row.get("request_id") or ""): dict(row) for row in pending["requests"] if isinstance(row, Mapping)}

    candidates = _daily_requests(
        activation_at=activation_at,
        daily_spot=daily_spot,
        daily_history=daily_history,
    ) + _wes_requests(activation_at=activation_at, weekly_dir=weekly_dir)

    added = 0
    skipped = 0
    for request in candidates:
        if request.request_id in existing or _subject_id(request) in known:
            continue
        decision_bars = completed_at_or_before(bars, parse_time(request.decision_at))
        if len(decision_bars) < MIN_CONTEXT:
            continue
        try:
            request = _resolve_request_horizon(request, decision_bars)
        except RuntimeError:
            continue
        if _target_observable(request, bars):
            _append_skip(ledger, request, "target_outcome_already_observable_no_historical_backfill")
            known.add(_subject_id(request))
            skipped += 1
        else:
            existing[request.request_id] = request.as_dict()
            added += 1

    _write_pending(state_dir, existing.values())
    return added, skipped


def settle_outcomes(state_dir: Path, bars: Sequence[Bar]) -> int:
    ledger = state_dir / LEDGER_FILENAME
    settled = _outcome_subjects(ledger)
    appended = 0
    for event in read_events(ledger):
        if event.get("event_type") != "forecast" or not str(event.get("source_ref") or "").startswith("timesfm3-benchmark://forecast/"):
            continue
        subject_id = str(event.get("subject_id") or "")
        if not subject_id or subject_id in settled:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        decision_at = parse_time(str(payload.get("decision_at") or ""))
        future = future_after(bars, decision_at)
        target_mode = str(payload.get("target_mode") or "")
        target_bar: Optional[Bar] = None

        if target_mode == "completed_30m_bars":
            steps = int(payload.get("horizon_steps") or 0)
            if steps <= 0 or len(future) < steps:
                continue
            target_bar = future[steps - 1]
        elif target_mode == "absolute_target_time":
            target_text = str(payload.get("target_at") or "")
            if not target_text or not bars:
                continue
            target_at = parse_time(target_text)
            if max(_bar_completion(bar) for bar in bars) < target_at:
                continue
            eligible = [bar for bar in future if _bar_completion(bar) <= target_at]
            if eligible:
                target_bar = eligible[-1]
        if target_bar is None:
            continue

        origin_price = float(payload["origin_price"])
        actual_price = float(target_bar.close)
        actual_return = actual_price / origin_price - 1.0
        actual_direction = _sign_direction(actual_return)
        engine_direction = str(payload.get("engine_direction") or "")
        benchmark_direction = str(payload.get("timesfm3_direction") or "")
        target_completion = _bar_completion(target_bar)
        append_event(
            ledger,
            event_type="outcome",
            occurred_at=iso_z(target_completion),
            subject_id=subject_id,
            source_ref=f"timesfm3-benchmark://outcome/{subject_id}",
            payload={
                "producer_schema_version": SCHEMA_VERSION,
                "source_engine": payload.get("source_engine"),
                "source_version": payload.get("source_version"),
                "source_decision_id": payload.get("source_decision_id"),
                "decision_at": payload.get("decision_at"),
                "engine_direction": engine_direction,
                "timesfm3_direction": benchmark_direction,
                "origin_price": origin_price,
                "timesfm3_forecast_price": payload.get("timesfm3_forecast_price"),
                "target_mode": target_mode,
                "target_at": payload.get("target_at"),
                "horizon_steps": payload.get("horizon_steps"),
                "target_bar_at": iso_z(parse_time(target_bar.timestamp)),
                "target_observed_at": iso_z(target_completion),
                "actual_price": actual_price,
                "actual_return": actual_return,
                "actual_direction": actual_direction,
                "engine_direction_correct": None if actual_direction == "FLAT" else engine_direction == actual_direction,
                "timesfm3_direction_correct": None if actual_direction == "FLAT" else benchmark_direction == actual_direction,
                "directions_agreed": engine_direction == benchmark_direction,
                "paired_comparison": True,
                "authority": safety_controls(),
            },
        )
        settled.add(subject_id)
        appended += 1
    return appended


class TimesFM3Runtime:
    """Lazy runtime so unit tests never import/download TimesFM3."""

    def __init__(self) -> None:
        import numpy as np  # type: ignore
        from timesfm3 import TimesFM3Forecaster  # type: ignore

        self._np = np
        self._model = TimesFM3Forecaster.from_pretrained(MODEL_ID)

    def forecast(self, context: Sequence[float], horizon_steps: int) -> list[float]:
        if horizon_steps <= 0 or horizon_steps > MAX_HORIZON_STEPS:
            raise ValueError("invalid TimesFM3 benchmark horizon")
        values = self._np.asarray(list(context), dtype=float)
        output = self._model.predict(context=values, horizon=horizon_steps)
        forecast = self._np.asarray(output.forecast, dtype=float).reshape(-1)
        if forecast.size < horizon_steps:
            raise RuntimeError("TimesFM3 returned a shorter horizon than requested")
        return [float(value) for value in forecast[:horizon_steps].tolist()]


def _timesfm_package_version() -> str:
    for name in ("timesfm", "timesfm3"):
        try:
            return importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            continue
    return "not-installed"


def _request_from_mapping(raw: Mapping[str, Any]) -> BenchmarkRequest:
    return BenchmarkRequest(
        request_id=str(raw.get("request_id") or ""),
        source_engine=str(raw.get("source_engine") or ""),
        source_version=str(raw.get("source_version") or ""),
        source_decision_id=str(raw.get("source_decision_id") or ""),
        decision_at=str(raw.get("decision_at") or ""),
        engine_direction=str(raw.get("engine_direction") or ""),
        target_mode=str(raw.get("target_mode") or ""),
        target_at=str(raw.get("target_at")) if raw.get("target_at") is not None else None,
        horizon_steps=int(raw.get("horizon_steps") or 0),
        source_ref=str(raw.get("source_ref") or ""),
    )


def run_inference(state_dir: Path, *, bars: Sequence[Bar], runtime: ForecastRuntime, now: datetime) -> tuple[int, int]:
    ledger = state_dir / LEDGER_FILENAME
    pending = _pending_payload(state_dir)
    known = _forecast_subjects(ledger) | _skip_subjects(ledger)
    remaining: list[dict[str, Any]] = []
    appended = 0
    expired = 0

    for raw in pending["requests"]:
        if not isinstance(raw, Mapping):
            continue
        request = _request_from_mapping(raw)
        subject_id = _subject_id(request)
        if subject_id in known:
            continue

        decision_at = parse_time(request.decision_at)
        decision_bars = completed_at_or_before(bars, decision_at)
        if len(decision_bars) < MIN_CONTEXT:
            remaining.append(request.as_dict())
            continue
        origin = decision_bars[-1]
        origin_completion = _bar_completion(origin)
        age_at_decision = (decision_at - origin_completion).total_seconds() / 60.0
        if age_at_decision < -1.0 or age_at_decision > MAX_DECISION_ORIGIN_AGE_MINUTES:
            _append_skip(
                ledger,
                request,
                "decision_origin_stale_or_future",
                origin_bar_completed_at=iso_z(origin_completion),
                age_minutes_at_decision=age_at_decision,
            )
            expired += 1
            continue
        if _target_observable(request, bars):
            _append_skip(ledger, request, "target_outcome_already_observable_before_inference")
            expired += 1
            continue

        context_rows = decision_bars[-CONTEXT_LIMIT:]
        points = runtime.forecast([float(bar.close) for bar in context_rows], request.horizon_steps)
        if len(points) < request.horizon_steps:
            raise RuntimeError("TimesFM3 returned insufficient forecast points")
        origin_price = float(origin.close)
        forecast_price = float(points[request.horizon_steps - 1])
        predicted_return = forecast_price / origin_price - 1.0
        append_event(
            ledger,
            event_type="forecast",
            occurred_at=iso_z(now),
            subject_id=subject_id,
            source_ref=f"timesfm3-benchmark://forecast/{request.request_id}",
            payload={
                "producer_schema_version": SCHEMA_VERSION,
                "model_id": MODEL_ID,
                "timesfm_package_version": _timesfm_package_version(),
                "instrument": INSTRUMENT,
                "symbol": SYMBOL,
                "data_provider": "Yahoo Finance chart",
                "interval": INTERVAL,
                "source_engine": request.source_engine,
                "source_version": request.source_version,
                "source_decision_id": request.source_decision_id,
                "source_ref": request.source_ref,
                "decision_at": request.decision_at,
                "engine_direction": request.engine_direction,
                "context_points": len(context_rows),
                "context_start_at": iso_z(parse_time(context_rows[0].timestamp)),
                "origin_bar_at": iso_z(parse_time(origin.timestamp)),
                "origin_bar_completed_at": iso_z(origin_completion),
                "origin_price": origin_price,
                "target_mode": request.target_mode,
                "target_at": request.target_at,
                "horizon_steps": request.horizon_steps,
                "timesfm3_forecast_price": forecast_price,
                "timesfm3_predicted_return": predicted_return,
                "timesfm3_direction": _sign_direction(predicted_return),
                "paired_same_engine_decision": True,
                "frozen_before_target_outcome": True,
                "private_internal_research": True,
                "authority": safety_controls(),
            },
        )
        known.add(subject_id)
        appended += 1

    _write_pending(state_dir, remaining)
    return appended, expired


def _mean_bool(values: Sequence[Any]) -> Optional[float]:
    rows = [value for value in values if isinstance(value, bool)]
    if not rows:
        return None
    return sum(1 for value in rows if value) / len(rows)


def _source_metrics(outcomes: Sequence[Mapping[str, Any]], source_engine: str) -> dict[str, Any]:
    payloads = [
        row["payload"] for row in outcomes
        if isinstance(row.get("payload"), Mapping) and str(row["payload"].get("source_engine") or "") == source_engine
    ]
    paired = [
        row for row in payloads
        if isinstance(row.get("engine_direction_correct"), bool) and isinstance(row.get("timesfm3_direction_correct"), bool)
    ]
    disagree = [row for row in paired if row.get("directions_agreed") is False]
    engine_rate = _mean_bool([row.get("engine_direction_correct") for row in paired])
    timesfm_rate = _mean_bool([row.get("timesfm3_direction_correct") for row in paired])
    return {
        "paired_resolved": len(paired),
        "engine_direction_hit_rate": engine_rate,
        "timesfm3_direction_hit_rate": timesfm_rate,
        "hit_rate_delta_engine_minus_timesfm3": (
            engine_rate - timesfm_rate if engine_rate is not None and timesfm_rate is not None else None
        ),
        "direction_agreement_rate": _mean_bool([row.get("directions_agreed") for row in paired]),
        "disagreement_count": len(disagree),
        "engine_wins_when_disagree": sum(
            1 for row in disagree if row["engine_direction_correct"] is True and row["timesfm3_direction_correct"] is False
        ),
        "timesfm3_wins_when_disagree": sum(
            1 for row in disagree if row["timesfm3_direction_correct"] is True and row["engine_direction_correct"] is False
        ),
        "both_correct": sum(
            1 for row in paired if row["engine_direction_correct"] is True and row["timesfm3_direction_correct"] is True
        ),
        "both_wrong": sum(
            1 for row in paired if row["engine_direction_correct"] is False and row["timesfm3_direction_correct"] is False
        ),
    }


def build_status(state_dir: Path, *, license_gate_enabled: bool) -> dict[str, Any]:
    ledger = state_dir / LEDGER_FILENAME
    chain = verify_chain(ledger)
    if not chain.get("ok"):
        raise RuntimeError("TimesFM3 benchmark ledger invalid: " + str(chain.get("error")))
    events = read_events(ledger)
    forecasts = [row for row in events if row.get("event_type") == "forecast"]
    outcomes = [row for row in events if row.get("event_type") == "outcome"]
    skips = [row for row in events if row.get("event_type") == "learning_observation"]
    pending = _pending_payload(state_dir)["requests"]
    status = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso_z(datetime.now(timezone.utc)),
        "model_id": MODEL_ID,
        "role": "EXTERNAL_DIRECTIONAL_BENCHMARK",
        "private_internal_research": True,
        "license_gate_enabled": bool(license_gate_enabled),
        "public_projection": False,
        "pnl_role": False,
        "authority": safety_controls(),
        "counts": {
            "forecast_pairs": len(forecasts),
            "resolved_pairs": len(outcomes),
            "skipped_no_hindsight": len(skips),
            "pending_inference": len(pending),
        },
        "daily_eurusd": _source_metrics(outcomes, "daily_eurusd"),
        "wes_eurusd": _source_metrics(outcomes, "wes_eurusd"),
        "ledger": {"count": chain.get("count"), "head_hash": chain.get("head_hash")},
    }
    _atomic_json(state_dir / STATUS_FILENAME, status)
    return status


def verify_state(state_dir: Path) -> dict[str, Any]:
    activation = _load_json(state_dir / ACTIVATION_FILENAME)
    if not isinstance(activation, Mapping):
        return {"ok": False, "error": "activation_missing"}
    try:
        if activation.get("schema_version") != SCHEMA_VERSION or activation.get("model_id") != MODEL_ID:
            raise ValueError("contract")
        parse_time(str(activation.get("activated_at") or ""))
        authority = activation.get("authority") if isinstance(activation.get("authority"), Mapping) else {}
        _assert_zero_authority(authority)
    except (ValueError, RuntimeError, TypeError):
        return {"ok": False, "error": "activation_invalid"}

    chain = verify_chain(state_dir / LEDGER_FILENAME)
    if not chain.get("ok"):
        return {"ok": False, "error": "ledger_invalid", "detail": chain.get("error")}

    activation_at = parse_time(str(activation["activated_at"]))
    seen: set[str] = set()
    pending = _pending_payload(state_dir)["requests"]
    for raw in pending:
        if not isinstance(raw, Mapping):
            return {"ok": False, "error": "pending_invalid"}
        request_id = str(raw.get("request_id") or "")
        if not request_id or request_id in seen:
            return {"ok": False, "error": "pending_duplicate"}
        seen.add(request_id)
        if _direction(raw.get("engine_direction")) is None:
            return {"ok": False, "error": "pending_direction_invalid"}
        if parse_time(str(raw.get("decision_at") or "")) <= activation_at:
            return {"ok": False, "error": "pending_pre_activation"}

    return {
        "ok": True,
        "ledger_count": chain.get("count"),
        "ledger_head_hash": chain.get("head_hash"),
        "pending": len(pending),
    }


def fetch_bars(client: Optional[YahooChartClient] = None) -> list[Bar]:
    return list((client or YahooChartClient(timeout=20)).bars(SYMBOL, RANGE, INTERVAL))


def prepare_cycle(
    state_dir: Path,
    *,
    now: datetime,
    daily_spot: Path,
    daily_history: Path,
    weekly_dir: Path,
    bootstrap: bool,
    bars: Optional[Sequence[Bar]] = None,
    license_gate_enabled: bool = False,
) -> dict[str, Any]:
    before = _sha({
        "activation": _load_json(state_dir / ACTIVATION_FILENAME),
        "pending": _load_json(state_dir / PENDING_FILENAME),
        "ledger": (state_dir / LEDGER_FILENAME).read_text(encoding="utf-8") if (state_dir / LEDGER_FILENAME).exists() else "",
    })
    ensure_activation(state_dir, now=now, bootstrap=bootstrap)
    rows = list(bars) if bars is not None else fetch_bars()
    settled = settle_outcomes(state_dir, rows)
    added, skipped = discover_requests(
        state_dir,
        bars=rows,
        daily_spot=daily_spot,
        daily_history=daily_history,
        weekly_dir=weekly_dir,
    )
    status = build_status(state_dir, license_gate_enabled=license_gate_enabled)
    after = _sha({
        "activation": _load_json(state_dir / ACTIVATION_FILENAME),
        "pending": _load_json(state_dir / PENDING_FILENAME),
        "ledger": (state_dir / LEDGER_FILENAME).read_text(encoding="utf-8"),
    })
    return {
        "changed": before != after,
        "settled": settled,
        "pending_added": added,
        "skipped_matured": skipped,
        "pending_inference": status["counts"]["pending_inference"],
        "needs_inference": status["counts"]["pending_inference"] > 0,
    }


def infer_cycle(
    state_dir: Path,
    *,
    now: datetime,
    runtime: Optional[ForecastRuntime] = None,
    bars: Optional[Sequence[Bar]] = None,
    license_gate_enabled: bool,
) -> dict[str, Any]:
    if not license_gate_enabled:
        raise RuntimeError("TimesFM3 research license gate is not enabled")
    ensure_activation(state_dir, now=now, bootstrap=False)
    rows = list(bars) if bars is not None else fetch_bars()
    appended, expired = run_inference(
        state_dir,
        bars=rows,
        runtime=runtime or TimesFM3Runtime(),
        now=now,
    )
    settled = settle_outcomes(state_dir, rows)
    status = build_status(state_dir, license_gate_enabled=True)
    return {
        "forecasts_appended": appended,
        "expired_before_inference": expired,
        "outcomes_appended": settled,
        "pending_inference": status["counts"]["pending_inference"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Private TimesFM3 benchmark for BriefRooms EUR/USD Daily and WES")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--daily-spot", type=Path, default=DAILY_SPOT_DEFAULT)
    parser.add_argument("--daily-history", type=Path, default=DAILY_HISTORY_DEFAULT)
    parser.add_argument("--weekly-dir", type=Path, default=WEEKLY_DIR_DEFAULT)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--infer", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--license-gate-enabled", action="store_true")
    args = parser.parse_args()

    if sum(bool(x) for x in (args.prepare, args.infer, args.verify)) != 1:
        parser.error("choose exactly one of --prepare, --infer, --verify")

    now = datetime.now(timezone.utc)
    if args.prepare:
        result = prepare_cycle(
            args.state_dir,
            now=now,
            daily_spot=args.daily_spot,
            daily_history=args.daily_history,
            weekly_dir=args.weekly_dir,
            bootstrap=args.bootstrap,
            license_gate_enabled=args.license_gate_enabled,
        )
    elif args.infer:
        result = infer_cycle(args.state_dir, now=now, license_gate_enabled=args.license_gate_enabled)
    else:
        result = verify_state(args.state_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
