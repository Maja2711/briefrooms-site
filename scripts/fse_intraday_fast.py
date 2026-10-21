#!/usr/bin/env python3
"""Lightweight 5-minute FSE intraday projection.

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
from datetime import date, datetime, time, timedelta, timezone
from calendar import monthrange
import urllib.request
import urllib.parse
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Mapping

from scripts import fractal_structure_engine as v1
from scripts import fractal_structure_engine_v2 as v2

SCHEMA = "briefrooms-fse-intraday-fast-v1"
TRANSPORT_VERSION = "FSE-FAST-2.0"
FAST_SCALES = ("1m", "5m", "15m", "1h")
CADENCE_MINUTES = 5
STALE_AFTER_MINUTES = 12
SOURCE_RANGE = "7d"
SOURCE_INTERVAL = "1m"


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def market_reference(instrument: str, now: datetime) -> tuple[str, datetime]:
    if instrument == "BTCUSD":
        return "OPEN", now
    ny = ZoneInfo("America/New_York")
    local = now.astimezone(ny)
    if instrument == "EURUSD":
        if local.weekday() < 4 or (local.weekday() == 4 and local.hour < 17) or (local.weekday() == 6 and local.hour >= 17):
            return "OPEN", now
        friday = local.date() - timedelta(days=(local.weekday() - 4) % 7)
        return "CLOSED", datetime.combine(friday, time(17), ny).astimezone(timezone.utc)
    if instrument != "SPX":
        raise ValueError("unknown instrument")

    def observed(year: int, month: int, day: int) -> date:
        d = date(year, month, day)
        return d + timedelta(days=-1 if d.weekday() == 5 else 1 if d.weekday() == 6 else 0)

    def nth(year: int, month: int, weekday: int, n: int) -> date:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))

    def holidays(year: int) -> set[date]:
        a, b, c = year % 19, year // 100, year % 100
        d, e, f = b // 4, b % 4, (b + 8) // 25
        g = (b - f + 1) // 3
        h, i, k = (19 * a + b - d - g + 15) % 30, c // 4, c % 4
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        easter = date(year, (h + l - 7 * m + 114) // 31, (h + l - 7 * m + 114) % 31 + 1)
        memorial = date(year, 5, monthrange(year, 5)[1])
        memorial -= timedelta(days=memorial.weekday())
        # NYSE does not observe a Saturday New Year on the preceding Friday.
        new_year = date(year, 1, 1)
        if new_year.weekday() == 6:
            new_year += timedelta(days=1)
        return {new_year, observed(year, 6, 19), observed(year, 7, 4), observed(year, 12, 25),
                nth(year, 1, 0, 3), nth(year, 2, 0, 3), memorial,
                easter - timedelta(days=2), nth(year, 9, 0, 1), nth(year, 11, 3, 4)}

    day = local.date()
    for _ in range(10):
        if day.weekday() < 5 and day not in holidays(day.year):
            opened = datetime.combine(day, time(9, 30), ny)
            early = day == nth(day.year, 11, 3, 4) + timedelta(days=1) or (day.month, day.day) in {(7, 3), (12, 24)}
            closed = datetime.combine(day, time(13 if early else 16), ny)
            if local >= opened:
                return ("OPEN" if local < closed else "CLOSED"), min(local, closed).astimezone(timezone.utc)
        day -= timedelta(days=1)
    raise RuntimeError("market session unavailable")


def validate_minute_source(bars: list[v1.Bar], instrument: str, now: datetime) -> dict[str, Any]:
    if len(bars) < 90:
        raise RuntimeError(f"insufficient 1m bars: {len(bars)}")
    if any(b.timestamp > now + timedelta(minutes=1) or b.close <= 0 for b in bars):
        raise RuntimeError("invalid or future source bars")
    latest = bars[-1].source_observed_at or bars[-1].timestamp
    if latest > now + timedelta(minutes=1):
        raise RuntimeError("future source clock")
    state, reference = market_reference(instrument, now)
    lag = max(0.0, (reference - latest - timedelta(minutes=1)).total_seconds() / 60)
    if lag > STALE_AFTER_MINUTES:
        raise RuntimeError(f"stale 1m source: {lag:.1f} minutes")
    return {"market_state": state, "source_reference_at": reference.isoformat().replace("+00:00", "Z"),
            "source_age_minutes": max(0, (now - latest).total_seconds() / 60), "source_lag_minutes": lag}


class FastYahooChartClient:
    """Bounded provider fallback; stale HTTP 200 is a failed provider."""
    def __init__(self, now: datetime):
        self.now = now

    def bars(self, symbol: str, range_: str, interval: str) -> list[v1.Bar]:
        instrument = next(k for k, value in v1.DEFAULT_INSTRUMENTS.items() if value == symbol)
        errors = []
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            url = f"https://{host}/v8/finance/chart/{urllib.parse.quote(symbol, safe='')}?range={range_}&interval={interval}&includePrePost=false&_={int(self.now.timestamp())}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": v1.USER_AGENT, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=4) as response:
                    rows = v1.parse_yahoo_bars(json.load(response), symbol, interval, self.now)
                validate_minute_source(rows, instrument, self.now)
                return rows
            except Exception as exc:
                errors.append(f"{host}:{type(exc).__name__}:{exc}")
        raise RuntimeError("all providers failed: " + " | ".join(errors))


def merged_phase_map(base_row: Mapping[str, Any], minute_bars: list[v1.Bar], symbol: str = "") -> dict[str, Any]:
    base = dict(base_row.get("phase_map") or {})
    scales = {
        "1m": minute_bars,
        "5m": v1.resample_minutes(minute_bars, 5, symbol),
        "15m": v1.resample_minutes(minute_bars, 15, symbol),
        "1h": v1.resample_minutes(minute_bars, 60, symbol),
    }
    out: dict[str, Any] = {}
    for tf in v2.SCALE_ORDER:
        prior = dict(base.get(tf) or {})
        if tf in FAST_SCALES:
            bars = scales[tf]
            desc = v2.phase_descriptor(bars)
            prior = dict(desc)
            prior["bars"] = len(bars)
            prior["observed_at"] = bars[-1].timestamp.isoformat().replace("+00:00", "Z") if bars else None
            prior["fast_refreshed"] = True
            prior["source_observed_at"] = (bars[-1].source_observed_at or bars[-1].timestamp).isoformat().replace("+00:00", "Z") if bars else None
            prior["provisional"] = True
            prior["phase_memory"] = None
            prior["phase_progress"] = None
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
    phase_map = merged_phase_map(base_row, minute_bars, symbol)
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
    now = v1.parse_time(at) if at else datetime.now(timezone.utc)
    client = client or FastYahooChartClient(now)
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
            minute_bars = sorted(minute_bars, key=lambda b: b.timestamp)
            source_status = validate_minute_source(minute_bars, instrument, now)
            row = build_instrument(instrument, symbol, base_rows.get(instrument, {}), minute_bars)
            row["source_status"] = source_status
            rows.append(row)
        except Exception as exc:
            errors[instrument] = f"{type(exc).__name__}: {exc}"
    if not rows:
        raise RuntimeError("No FSE intraday instruments available: " + json.dumps(errors, sort_keys=True))

    generated_at = at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    out = {
        "schema_version": SCHEMA,
        "transport_version": TRANSPORT_VERSION,
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
