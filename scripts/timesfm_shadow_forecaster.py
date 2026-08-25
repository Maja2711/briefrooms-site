#!/usr/bin/env python3
"""TimesFM 2.5 shadow forecaster for BriefRooms.

Initial scope: EUR/USD, 30-minute bars, 1h/4h/24h trading-bar horizons.
The forecaster is research-shadow only. It freezes forecasts before outcomes and
writes them to a hash-chained Learning-Ledger-compatible producer ledger. It has
zero authority over Belief Core, BRACE/WES, Daily Trading, ranking, sizing, or
execution.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence

try:
    from belief_market_data_adapter import Bar, YahooChartClient
    from learning_ledger import append_event, read_events, verify_chain
except ModuleNotFoundError:
    from scripts.belief_market_data_adapter import Bar, YahooChartClient
    from scripts.learning_ledger import append_event, read_events, verify_chain

SCHEMA_VERSION = "briefrooms-timesfm-shadow-v1"
MODEL_ID = "google/timesfm-2.5-200m-pytorch"
SYMBOL = "EURUSD=X"
INSTRUMENT = "EUR/USD"
INTERVAL = "30m"
RANGE = "60d"
BAR_MINUTES = 30
CONTEXT_LIMIT = 1024
MIN_CONTEXT = 256
MAX_HORIZON_STEPS = 48
MAX_ORIGIN_AGE_MINUTES = 90
HORIZONS: tuple[tuple[int, str], ...] = ((2, "1h"), (8, "4h"), (48, "24h_trading_bars"))
ACTIVATION_FILENAME = "timesfm_shadow_activation.json"
LEDGER_FILENAME = "timesfm_shadow_ledger.jsonl"
STATUS_FILENAME = "timesfm_shadow_status.json"


class ForecastRuntime(Protocol):
    def forecast(self, context: Sequence[float], horizon_steps: int) -> tuple[list[float], list[dict[str, float]]]: ...


@dataclass(frozen=True)
class CycleResult:
    forecasts_appended: int
    outcomes_appended: int
    skipped_stale_origin: bool
    latest_completed_bar_at: Optional[str]
    ledger_count: int
    ledger_head_hash: Optional[str]


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
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def safety_controls() -> Dict[str, bool]:
    return {
        "decision_influence": False,
        "belief_writeback": False,
        "evidence_writeback": False,
        "evidence_weight_writeback": False,
        "causal_edge_writeback": False,
        "engine_policy_writeback": False,
        "ranking_writeback": False,
        "sizing_writeback": False,
        "trade_execution": False,
        "automatic_tuning": False,
        "automatic_promotion": False,
        "historical_backfill": False,
    }


def _assert_safety() -> None:
    bad = [key for key, value in safety_controls().items() if value is not False]
    if bad:
        raise RuntimeError("TimesFM shadow zero-authority invariant violated: " + ",".join(bad))


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


def ensure_activation(state_dir: Path, *, now: datetime, bootstrap: bool = False) -> Dict[str, Any]:
    path = state_dir / ACTIVATION_FILENAME
    existing = _load_json(path)
    if isinstance(existing, dict):
        if existing.get("schema_version") != SCHEMA_VERSION:
            raise RuntimeError("invalid TimesFM shadow activation schema")
        if existing.get("model_id") != MODEL_ID or existing.get("symbol") != SYMBOL or existing.get("interval") != INTERVAL:
            raise RuntimeError("TimesFM shadow activation contract changed; create an explicit new experiment version")
        parse_time(str(existing.get("activated_at") or ""))
        return existing
    if not bootstrap:
        raise RuntimeError("TimesFM shadow activation missing; refusing silent reset")
    _assert_safety()
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / LEDGER_FILENAME).touch(exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "activated_at": iso_z(now),
        "model_id": MODEL_ID,
        "symbol": SYMBOL,
        "instrument": INSTRUMENT,
        "interval": INTERVAL,
        "horizons": [{"steps": steps, "label": label} for steps, label in HORIZONS],
        "anti_hindsight": {
            "historical_backfill": False,
            "forecast_before_outcome": True,
            "same_cycle_new_forecast_outcome_binding": False,
        },
        "authority": safety_controls(),
    }
    _atomic_json(path, payload)
    return payload


def _completed_bars(bars: Sequence[Bar], now: datetime) -> list[Bar]:
    cutoff = now - timedelta(minutes=BAR_MINUTES)
    rows = [bar for bar in bars if parse_time(bar.timestamp) <= cutoff]
    rows.sort(key=lambda row: parse_time(row.timestamp))
    return rows


def _bar_completion(bar: Bar) -> datetime:
    return parse_time(bar.timestamp) + timedelta(minutes=BAR_MINUTES)


def _subject_id(origin_bar_at: str, horizon_steps: int) -> str:
    safe = origin_bar_at.replace(":", "").replace("-", "")
    return f"timesfm:eurusd:30m:{safe}:h{horizon_steps}"


def _sign(value: float, eps: float = 1e-12) -> int:
    if value > eps:
        return 1
    if value < -eps:
        return -1
    return 0


def _forecast_events(ledger: Path) -> list[dict[str, Any]]:
    return [row for row in read_events(ledger) if row.get("event_type") == "forecast" and str(row.get("source_ref") or "").startswith("timesfm://")]


def _outcome_subjects(ledger: Path) -> set[str]:
    return {
        str(row.get("subject_id"))
        for row in read_events(ledger)
        if row.get("event_type") == "outcome" and str(row.get("source_ref") or "").startswith("timesfm://")
    }


def settle_outcomes(ledger: Path, bars: Sequence[Bar]) -> int:
    outcomes = _outcome_subjects(ledger)
    appended = 0
    for forecast_event in _forecast_events(ledger):
        subject_id = str(forecast_event.get("subject_id") or "")
        if not subject_id or subject_id in outcomes:
            continue
        payload = forecast_event.get("payload") if isinstance(forecast_event.get("payload"), Mapping) else {}
        horizon_steps = int(payload.get("horizon_steps") or 0)
        if horizon_steps <= 0:
            continue
        forecast_at = parse_time(str(forecast_event.get("occurred_at") or ""))
        future = [bar for bar in bars if _bar_completion(bar) > forecast_at]
        if len(future) < horizon_steps:
            continue
        target_bar = future[horizon_steps - 1]
        target_completion = _bar_completion(target_bar)
        if target_completion <= forecast_at:
            continue
        origin_price = float(payload["origin_price"])
        forecast_price = float(payload["forecast_price"])
        actual_price = float(target_bar.close)
        predicted_return = forecast_price / origin_price - 1.0
        actual_return = actual_price / origin_price - 1.0
        prediction_error = forecast_price - actual_price
        pred_sign = _sign(predicted_return)
        actual_sign = _sign(actual_return)
        quantiles = payload.get("quantiles") if isinstance(payload.get("quantiles"), Mapping) else {}
        q10 = quantiles.get("q10")
        q90 = quantiles.get("q90")
        interval_80_contains_actual = None
        if q10 is not None and q90 is not None:
            interval_80_contains_actual = float(q10) <= actual_price <= float(q90)
        append_event(
            ledger,
            event_type="outcome",
            occurred_at=iso_z(target_completion),
            subject_id=subject_id,
            source_ref=f"timesfm://outcome/{subject_id}",
            payload={
                "producer_schema_version": SCHEMA_VERSION,
                "model_id": payload.get("model_id"),
                "instrument": INSTRUMENT,
                "symbol": SYMBOL,
                "interval": INTERVAL,
                "forecast_at": forecast_event.get("occurred_at"),
                "origin_bar_at": payload.get("origin_bar_at"),
                "origin_price": origin_price,
                "horizon_steps": horizon_steps,
                "horizon_label": payload.get("horizon_label"),
                "target_bar_at": iso_z(parse_time(target_bar.timestamp)),
                "target_observed_at": iso_z(target_completion),
                "forecast_price": forecast_price,
                "actual_price": actual_price,
                "predicted_return": predicted_return,
                "actual_return": actual_return,
                "absolute_error": abs(prediction_error),
                "squared_error": prediction_error * prediction_error,
                "direction_correct": None if 0 in {pred_sign, actual_sign} else pred_sign == actual_sign,
                "interval_80_contains_actual": interval_80_contains_actual,
                "shadow": True,
                "decision_influence": False,
            },
        )
        outcomes.add(subject_id)
        appended += 1
    return appended


class TimesFMRuntime:
    """Lazy production runtime so unit tests do not need torch/timesfm installed."""

    def __init__(self) -> None:
        import numpy as np  # type: ignore
        import torch  # type: ignore
        import timesfm  # type: ignore

        self._np = np
        torch.set_float32_matmul_precision("high")
        self._model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(MODEL_ID)
        self._model.compile(
            timesfm.ForecastConfig(
                max_context=CONTEXT_LIMIT,
                max_horizon=MAX_HORIZON_STEPS,
                normalize_inputs=True,
                use_continuous_quantile_head=True,
                force_flip_invariance=True,
                infer_is_positive=True,
                fix_quantile_crossing=True,
            )
        )

    def forecast(self, context: Sequence[float], horizon_steps: int) -> tuple[list[float], list[dict[str, float]]]:
        values = self._np.asarray(list(context), dtype=self._np.float32)
        point, quantile = self._model.forecast(horizon=horizon_steps, inputs=[values])
        points = [float(value) for value in point[0].tolist()]
        qrows: list[dict[str, float]] = []
        labels = ["mean", "q10", "q20", "q30", "q40", "q50", "q60", "q70", "q80", "q90"]
        for row in quantile[0].tolist():
            qrows.append({label: float(value) for label, value in zip(labels, row)})
        return points, qrows


def _timesfm_package_version() -> str:
    try:
        return importlib_metadata.version("timesfm")
    except importlib_metadata.PackageNotFoundError:
        return "not-installed"


def generate_forecasts(
    ledger: Path,
    bars: Sequence[Bar],
    *,
    now: datetime,
    runtime: ForecastRuntime,
) -> int:
    if len(bars) < MIN_CONTEXT:
        raise RuntimeError(f"TimesFM requires at least {MIN_CONTEXT} completed EUR/USD bars")
    origin = bars[-1]
    origin_at = iso_z(parse_time(origin.timestamp))
    origin_completion = _bar_completion(origin)
    age_minutes = (now - origin_completion).total_seconds() / 60.0
    if age_minutes < -1.0:
        raise RuntimeError("latest completed bar is in the future")
    if age_minutes > MAX_ORIGIN_AGE_MINUTES:
        return 0
    existing_subjects = {str(row.get("subject_id") or "") for row in _forecast_events(ledger)}
    if all(_subject_id(origin_at, steps) in existing_subjects for steps, _ in HORIZONS):
        return 0

    context_rows = list(bars[-CONTEXT_LIMIT:])
    context = [float(bar.close) for bar in context_rows]
    points, qrows = runtime.forecast(context, MAX_HORIZON_STEPS)
    if len(points) < MAX_HORIZON_STEPS or len(qrows) < MAX_HORIZON_STEPS:
        raise RuntimeError("TimesFM returned a shorter horizon than requested")
    origin_price = float(origin.close)
    forecast_at = iso_z(now)
    appended = 0
    for horizon_steps, horizon_label in HORIZONS:
        subject_id = _subject_id(origin_at, horizon_steps)
        if subject_id in existing_subjects:
            continue
        forecast_price = float(points[horizon_steps - 1])
        quantiles = dict(qrows[horizon_steps - 1])
        append_event(
            ledger,
            event_type="forecast",
            occurred_at=forecast_at,
            subject_id=subject_id,
            source_ref=f"timesfm://forecast/{subject_id}",
            payload={
                "producer_schema_version": SCHEMA_VERSION,
                "model_id": MODEL_ID,
                "timesfm_package_version": _timesfm_package_version(),
                "instrument": INSTRUMENT,
                "symbol": SYMBOL,
                "data_provider": "Yahoo Finance chart",
                "interval": INTERVAL,
                "context_points": len(context),
                "context_start_at": iso_z(parse_time(context_rows[0].timestamp)),
                "origin_bar_at": origin_at,
                "origin_bar_completed_at": iso_z(origin_completion),
                "origin_price": origin_price,
                "horizon_steps": horizon_steps,
                "horizon_label": horizon_label,
                "forecast_price": forecast_price,
                "predicted_return": forecast_price / origin_price - 1.0,
                "predicted_direction": "UP" if forecast_price > origin_price else "DOWN" if forecast_price < origin_price else "FLAT",
                "quantiles": quantiles,
                "frozen_before_outcome": True,
                "shadow": True,
                "decision_influence": False,
            },
        )
        existing_subjects.add(subject_id)
        appended += 1
    return appended


def fetch_bars(client: Optional[YahooChartClient] = None) -> list[Bar]:
    client = client or YahooChartClient(timeout=20)
    return list(client.bars(SYMBOL, RANGE, INTERVAL))


def verify_state(state_dir: Path) -> Dict[str, Any]:
    activation = _load_json(state_dir / ACTIVATION_FILENAME)
    if not isinstance(activation, dict):
        return {"ok": False, "error": "activation_missing"}
    try:
        if activation.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("schema")
        if activation.get("model_id") != MODEL_ID or activation.get("symbol") != SYMBOL or activation.get("interval") != INTERVAL:
            raise ValueError("contract")
        parse_time(str(activation.get("activated_at") or ""))
        if any(value is not False for value in (activation.get("authority") or {}).values()):
            raise ValueError("authority")
    except (ValueError, TypeError):
        return {"ok": False, "error": "activation_invalid"}
    chain = verify_chain(state_dir / LEDGER_FILENAME)
    if not chain.get("ok"):
        return {"ok": False, "error": f"ledger:{chain.get('error')}", "ledger": chain}
    return {"ok": True, "activation": activation, "ledger": chain}


def run_cycle(
    state_dir: Path,
    *,
    now: Optional[datetime] = None,
    bootstrap: bool = False,
    runtime: Optional[ForecastRuntime] = None,
    client: Optional[YahooChartClient] = None,
) -> CycleResult:
    _assert_safety()
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ensure_activation(state_dir, now=current_time, bootstrap=bootstrap)
    ledger = state_dir / LEDGER_FILENAME
    chain = verify_chain(ledger)
    if not chain.get("ok"):
        raise RuntimeError("invalid TimesFM shadow ledger before cycle: " + str(chain.get("error")))

    completed = _completed_bars(fetch_bars(client), current_time)
    if not completed:
        raise RuntimeError("no completed EUR/USD 30-minute bars available")
    outcomes_appended = settle_outcomes(ledger, completed)

    origin_completion = _bar_completion(completed[-1])
    stale = (current_time - origin_completion).total_seconds() / 60.0 > MAX_ORIGIN_AGE_MINUTES
    forecasts_appended = 0
    if not stale:
        active_runtime = runtime or TimesFMRuntime()
        forecasts_appended = generate_forecasts(ledger, completed, now=current_time, runtime=active_runtime)

    final_chain = verify_chain(ledger)
    if not final_chain.get("ok"):
        raise RuntimeError("invalid TimesFM shadow ledger after cycle: " + str(final_chain.get("error")))
    status = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": iso_z(current_time),
        "model_id": MODEL_ID,
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "latest_completed_bar_at": iso_z(parse_time(completed[-1].timestamp)),
        "latest_completed_bar_observed_at": iso_z(origin_completion),
        "forecasts_appended": forecasts_appended,
        "outcomes_appended": outcomes_appended,
        "skipped_stale_origin": stale,
        "ledger_count": int(final_chain.get("count") or 0),
        "ledger_head_hash": final_chain.get("head_hash"),
        "zero_authority": safety_controls(),
    }
    _atomic_json(state_dir / STATUS_FILENAME, status)
    return CycleResult(
        forecasts_appended=forecasts_appended,
        outcomes_appended=outcomes_appended,
        skipped_stale_origin=stale,
        latest_completed_bar_at=status["latest_completed_bar_at"],
        ledger_count=status["ledger_count"],
        ledger_head_hash=status["ledger_head_hash"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run TimesFM 2.5 as a zero-authority BriefRooms shadow forecaster")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        result = verify_state(args.state_dir)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("ok") else 2
    result = run_cycle(args.state_dir, bootstrap=args.bootstrap)
    print(json.dumps(result.__dict__, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
