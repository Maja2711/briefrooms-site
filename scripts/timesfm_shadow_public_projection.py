#!/usr/bin/env python3
"""Build a sanitized public projection from private TimesFM shadow state."""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from learning_ledger import read_events
    from timesfm_shadow_forecaster import (
        ACTIVATION_FILENAME,
        LEDGER_FILENAME,
        MODEL_ID,
        STATUS_FILENAME,
        verify_state,
    )
except ModuleNotFoundError:
    from scripts.learning_ledger import read_events
    from scripts.timesfm_shadow_forecaster import (
        ACTIVATION_FILENAME,
        LEDGER_FILENAME,
        MODEL_ID,
        STATUS_FILENAME,
        verify_state,
    )

PUBLIC_SCHEMA = "briefrooms-timesfm-shadow-public-pl-v1"
HORIZON_ORDER = ("1h", "4h", "24h_trading_bars")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _rmse(values: list[float]) -> float | None:
    return math.sqrt(sum(v * v for v in values) / len(values)) if values else None


def _safe_quantiles(payload: Mapping[str, Any]) -> dict[str, float | None]:
    q = payload.get("quantiles") if isinstance(payload.get("quantiles"), Mapping) else {}
    out: dict[str, float | None] = {}
    for key in ("q10", "q50", "q90"):
        value = q.get(key)
        out[key] = float(value) if value is not None else None
    return out


def build_projection(state_dir: Path) -> dict[str, Any]:
    verified = verify_state(state_dir)
    if not verified.get("ok"):
        raise RuntimeError(f"invalid TimesFM shadow state: {verified.get('error')}")

    activation = _load(state_dir / ACTIVATION_FILENAME)
    status = _load(state_dir / STATUS_FILENAME) if (state_dir / STATUS_FILENAME).exists() else {}
    events = read_events(state_dir / LEDGER_FILENAME)
    forecasts = [
        row for row in events
        if row.get("event_type") == "forecast" and str(row.get("source_ref") or "").startswith("timesfm://")
    ]
    outcomes = {
        str(row.get("subject_id") or ""): row
        for row in events
        if row.get("event_type") == "outcome" and str(row.get("source_ref") or "").startswith("timesfm://")
    }

    forecasts.sort(key=lambda row: str(row.get("occurred_at") or ""))
    latest_at = str(forecasts[-1].get("occurred_at") or "") if forecasts else None
    latest_rows = [row for row in forecasts if str(row.get("occurred_at") or "") == latest_at] if latest_at else []

    latest_horizons: dict[str, Any] = {}
    for row in latest_rows:
        p = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        label = str(p.get("horizon_label") or "")
        if not label:
            continue
        outcome = outcomes.get(str(row.get("subject_id") or ""))
        op = outcome.get("payload") if outcome and isinstance(outcome.get("payload"), Mapping) else {}
        latest_horizons[label] = {
            "forecast_price": float(p["forecast_price"]),
            "predicted_return": float(p["predicted_return"]),
            "predicted_direction": p.get("predicted_direction"),
            "quantiles": _safe_quantiles(p),
            "status": "RESOLVED" if outcome else "PENDING",
            "actual_price": float(op["actual_price"]) if op.get("actual_price") is not None else None,
            "actual_return": float(op["actual_return"]) if op.get("actual_return") is not None else None,
            "direction_correct": op.get("direction_correct") if outcome else None,
            "interval_80_contains_actual": op.get("interval_80_contains_actual") if outcome else None,
        }

    performance: dict[str, Any] = {}
    by_horizon: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in outcomes.values():
        p = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        label = str(p.get("horizon_label") or "")
        if label:
            by_horizon[label].append(p)

    for label in HORIZON_ORDER:
        rows = by_horizon.get(label, [])
        abs_errors_pips = [float(r["absolute_error"]) * 10000.0 for r in rows if r.get("absolute_error") is not None]
        signed_errors_pips = [
            (float(r["forecast_price"]) - float(r["actual_price"])) * 10000.0
            for r in rows if r.get("forecast_price") is not None and r.get("actual_price") is not None
        ]
        directions = [bool(r["direction_correct"]) for r in rows if r.get("direction_correct") is not None]
        coverage = [bool(r["interval_80_contains_actual"]) for r in rows if r.get("interval_80_contains_actual") is not None]
        performance[label] = {
            "resolved": len(rows),
            "direction_hit_rate": _mean([1.0 if x else 0.0 for x in directions]),
            "mae_pips": _mean(abs_errors_pips),
            "rmse_pips": _rmse(signed_errors_pips),
            "interval_80_coverage": _mean([1.0 if x else 0.0 for x in coverage]),
        }

    grouped: dict[str, dict[str, Any]] = {}
    for row in forecasts:
        p = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        key = str(row.get("occurred_at") or "")
        item = grouped.setdefault(key, {
            "forecast_at": key,
            "origin_bar_at": p.get("origin_bar_at"),
            "origin_price": p.get("origin_price"),
            "horizons": {},
        })
        label = str(p.get("horizon_label") or "")
        if not label:
            continue
        outcome = outcomes.get(str(row.get("subject_id") or ""))
        op = outcome.get("payload") if outcome and isinstance(outcome.get("payload"), Mapping) else {}
        item["horizons"][label] = {
            "forecast_price": p.get("forecast_price"),
            "predicted_direction": p.get("predicted_direction"),
            "status": "RESOLVED" if outcome else "PENDING",
            "actual_price": op.get("actual_price") if outcome else None,
            "direction_correct": op.get("direction_correct") if outcome else None,
        }

    history = [grouped[key] for key in sorted(grouped.keys(), reverse=True)[:12]]
    latest_payload = latest_rows[0].get("payload") if latest_rows and isinstance(latest_rows[0].get("payload"), Mapping) else {}

    return {
        "schema_version": PUBLIC_SCHEMA,
        "generated_at": status.get("updated_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "experiment": {
            "model_id": activation.get("model_id") or MODEL_ID,
            "instrument": activation.get("instrument") or "EUR/USD",
            "interval": activation.get("interval") or "30m",
            "activated_at": activation.get("activated_at"),
            "research_only": True,
            "decision_influence": False,
        },
        "latest": {
            "available": bool(latest_rows),
            "forecast_at": latest_at,
            "origin_bar_at": latest_payload.get("origin_bar_at"),
            "origin_price": latest_payload.get("origin_price"),
            "context_points": latest_payload.get("context_points"),
            "horizons": latest_horizons,
        },
        "performance": performance,
        "history": history,
        "ledger": {
            "events": status.get("ledger_count"),
            "forecasts": len(forecasts),
            "resolved_outcomes": len(outcomes),
        },
    }


def validate_projection(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != PUBLIC_SCHEMA:
        raise ValueError("invalid public schema")
    experiment = payload.get("experiment")
    if not isinstance(experiment, Mapping) or experiment.get("research_only") is not True or experiment.get("decision_influence") is not False:
        raise ValueError("TimesFM public projection must remain research-only and zero-authority")
    latest = payload.get("latest")
    if not isinstance(latest, Mapping):
        raise ValueError("latest missing")
    performance = payload.get("performance")
    if not isinstance(performance, Mapping):
        raise ValueError("performance missing")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    if args.validate:
        payload = json.loads(args.output.read_text(encoding="utf-8"))
        validate_projection(payload)
        print("TIMESFM_PUBLIC_PROJECTION_VALID")
        return 0
    if not args.state_dir:
        parser.error("--state-dir is required when building a projection")
    payload = build_projection(args.state_dir)
    validate_projection(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "forecasts": payload["ledger"]["forecasts"], "outcomes": payload["ledger"]["resolved_outcomes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
