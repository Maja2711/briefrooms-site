#!/usr/bin/env python3
"""Lightweight 15-minute FSE intraday projection.

This is a read-only / SHADOW_ONLY presentation refresh for IN-09.
It does NOT create HSE2 evidence, does NOT mutate durable FSE state, and does
NOT change production probabilities or trading decisions.

Fresh path:
Yahoo 1m bars (7d) -> resample 5m/15m/1h -> Phase descriptors ->
merge with the latest full FSE-PHASE 4h/1d/1w context -> Cross-Scale Alignment.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from scripts import fractal_structure_engine as v1
from scripts import fractal_structure_engine_v2 as v2

SCHEMA = "briefrooms-fse-intraday-fast-v1"
FAST_SCALES = ("1m", "5m", "15m", "1h")
CADENCE_MINUTES = 15
STALE_AFTER_MINUTES = 25
SOURCE_RANGE = "7d"
SOURCE_INTERVAL = "1m"


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def merged_phase_map(base_row: Mapping[str, Any], minute_bars: list[v1.Bar]) -> dict[str, Any]:
    base = dict(base_row.get("phase_map") or {})
    scales = {
        "1m": minute_bars,
        "5m": v2.resample_minutes(minute_bars, 5),
        "15m": v2.resample_minutes(minute_bars, 15),
        "1h": v2.resample_minutes(minute_bars, 60),
    }
    out: dict[str, Any] = {}
    for tf in v2.SCALE_ORDER:
        prior = dict(base.get(tf) or {})
        if tf in FAST_SCALES:
            bars = scales[tf]
            desc = v2.phase_descriptor(bars)
            prior.update(desc)
            prior["bars"] = len(bars)
            prior["observed_at"] = bars[-1].timestamp.isoformat().replace("+00:00", "Z") if bars else None
            prior["fast_refreshed"] = True
        else:
            prior["fast_refreshed"] = False
        out[tf] = prior
    return out


def build_instrument(
    instrument: str,
    symbol: str,
    base_row: Mapping[str, Any],
    minute_bars: list[v1.Bar],
) -> dict[str, Any]:
    phase_map = merged_phase_map(base_row, minute_bars)
    alignment = v2.cross_scale_alignment(phase_map)
    observed = {
        tf: (phase_map.get(tf) or {}).get("observed_at")
        for tf in FAST_SCALES
    }
    return {
        "instrument": instrument,
        "symbol": symbol,
        "source": f"Yahoo Chart {SOURCE_RANGE}/{SOURCE_INTERVAL}",
        "phase_map": phase_map,
        "cross_scale_alignment": alignment,
        "fast_observed_at": observed,
        "latest_fast_observed_at": max([x for x in observed.values() if x] or [None]),
    }


def run(
    root: Path,
    public: Path,
    client: v1.YahooChartClient | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    client = client or v1.YahooChartClient()
    base_path = root / "data/investments/fse_v2_public.json"
    base = json.loads(base_path.read_text(encoding="utf-8")) if base_path.exists() else {}
    base_rows = {
        str(x.get("instrument")): x
        for x in (base.get("instruments") or [])
        if isinstance(x, Mapping) and x.get("instrument")
    }
    instruments = dict(v1.DEFAULT_INSTRUMENTS)
    rows, errors = [], {}
    for instrument, symbol in instruments.items():
        try:
            minute_bars = client.bars(symbol, SOURCE_RANGE, SOURCE_INTERVAL)
            if len(minute_bars) < 90:
                raise RuntimeError(f"insufficient 1m bars: {len(minute_bars)}")
            rows.append(build_instrument(instrument, symbol, base_rows.get(instrument, {}), minute_bars))
        except Exception as exc:
            errors[instrument] = f"{type(exc).__name__}: {exc}"
    if not rows:
        raise RuntimeError("No FSE intraday instruments available: " + json.dumps(errors, sort_keys=True))

    generated_at = at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    out = {
        "schema_version": SCHEMA,
        "engine": "FSE Intraday Fast Projection",
        "module_id": "IN-09",
        "component_id": "FSE-INTRADAY-FAST",
        "mode": "SHADOW_ONLY",
        "production_impact": False,
        "authority": dict(v2.ZERO_AUTHORITY),
        "generated_at": generated_at,
        "cadence_minutes": CADENCE_MINUTES,
        "stale_after_minutes": STALE_AFTER_MINUTES,
        "source_policy": {
            "source": "Yahoo Chart secondary research feed",
            "range": SOURCE_RANGE,
            "interval": SOURCE_INTERVAL,
            "derived_scales": ["5m", "15m", "1h"],
            "slow_context": "4h/1d/1w retained from latest full FSE-PHASE snapshot",
        },
        "instruments": rows,
        "errors": errors,
        "notes": [
            "Presentation freshness path only; no HSE2 evidence is created here.",
            "No durable FSE snapshot/resolution is written by this fast path.",
            "Frontend must mark this projection stale after the declared threshold.",
        ],
    }
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--public", default="data/investments/fse_intraday_public.json")
    args = ap.parse_args()
    out = run(Path(args.root), Path(args.public))
    print(json.dumps({
        "generated_at": out["generated_at"],
        "instruments": len(out["instruments"]),
        "errors": out["errors"],
        "production_impact": out["production_impact"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
