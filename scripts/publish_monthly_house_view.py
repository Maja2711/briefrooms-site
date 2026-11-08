#!/usr/bin/env python3
"""Publish a source-checked, read-only monthly SPX House View research snapshot.

This is NOT a trading engine or an automatic BRACE/WES policy promoter.
Only observed macro releases and an optional frozen WES market observation enter.
No source data -> no publication; a model label is never represented as a
validated forecast, price target or probability.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "investments"
PUBLIC = DATA / "house_view_monthly_current.json"
ARCHIVE = DATA / "house_view_monthly_archive"
SERIES = {
    "GDPC1": ("BEA real GDP", "quarterly", 200),
    "PCEPI": ("BEA PCE price index", "monthly", 100),
    "PCEPILFE": ("BEA core PCE price index", "monthly", 100),
    "DGS10": ("U.S. Treasury 10Y", "daily", 15),
    "DCOILBRENTEU": ("EIA Brent spot", "daily", 22),
}
SERIES_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
MAX_BYTES = 1500000


class PublicationBlocked(ValueError):
    """Explicit no-publication condition; old verified snapshot survives."""


def fetch_series(series_id: str) -> list[tuple[date, float]]:
    if series_id not in SERIES:
        raise PublicationBlocked("Unknown source identifier")
    url = SERIES_BASE + series_id
    request = urllib.request.Request(url, headers={
        "User-Agent": "BriefRooms HouseView/1.0 (https://briefrooms.com)",
        "Accept": "text/csv",
    })
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise PublicationBlocked(f"{series_id}: response too large")
    reader = csv.reader(io.StringIO(raw.decode("utf-8-sig")))
    header = next(reader, None)
    if not header or len(header) != 2 or header[1].strip() != series_id or header[0].strip().lower() not in ("date", "observation_date"):
        raise PublicationBlocked(f"{series_id}: unexpected CSV schema")
    observations: list[tuple[date, float]] = []
    for row in reader:
        if len(row) != 2 or row[1].strip() in ("", "."):
            continue
        observed = date.fromisoformat(row[0].strip())
        value = float(row[1])
        if not math.isfinite(value) or (observations and observed <= observations[-1][0]):
            raise PublicationBlocked(f"{series_id}: invalid or nonchronological value")
        observations.append((observed, value))
    if not observations:
        raise PublicationBlocked(f"{series_id}: empty series")
    return observations


def checked_latest(code: str, points: list[tuple[date, float]], today: date) -> tuple[date, float]:
    observed, value = points[-1]
    age = (today - observed).days
    if age < 0 or age > SERIES[code][2]:
        raise PublicationBlocked(f"{code}: stale/future observation {observed}")
    if code in ("GDPC1", "PCEPI", "PCEPILFE") and value <= 0:
        raise PublicationBlocked(f"{code}: nonpositive index")
    if code == "DGS10" and not (0.0 <= value <= 25):
        raise PublicationBlocked("DGS10: implausible yield")
    if code == "DCOILBRENTEU" and not (5 <= value <= 350):
        raise PublicationBlocked("DCOILBRENTEU: implausible oil price")
    return observed, value


def yoy(points: list[tuple[date, float]]) -> float:
    latest_date, latest_value = points[-1]
    prev = next(((d, v) for d, v in points if d.year == latest_date.year - 1 and d.month == latest_date.month), None)
    if prev is None or prev[1] <= 0:
        raise PublicationBlocked("PCE: missing same-month year-ago observation")
    change = 100 * (latest_value / prev[1] - 1)
    if not -3 <= change <= 20:
        raise PublicationBlocked("PCE: implausible 12-month change")
    return round(change, 2)


def qoq_saar(points: list[tuple[date, float]]) -> float:
    if len(points) < 2:
        raise PublicationBlocked("GDPC1: missing previous quarter")
    (pdate, previous), (current_date, current) = points[-2:]
    months = (current_date.year - pdate.year) * 12 + current_date.month - pdate.month
    if months != 3 or previous <= 0:
        raise PublicationBlocked("GDPC1: irregular quarterly history")
    result = 100 * ((current / previous) ** 4 - 1)
    if not -25 <= result <= 25:
        raise PublicationBlocked("GDPC1: implausible annualized growth")
    return round(result, 2)


def weekly_spx_market_observation(today: date, data_dir: Path) -> dict | None:
    try:
        report = json.loads((data_dir / "wes_report.json").read_text(encoding="utf-8"))
        week = report["week_id"]
        if not isinstance(week, str) or not __import__("re").fullmatch(r"20\d{2}-W\d{2}", week):
            return None
        weekly = json.loads((data_dir / "weekly" / (week + ".json")).read_text(encoding="utf-8"))
        end = date.fromisoformat(weekly["forecast_for_week_end"])
        if weekly.get("week_id") != week or not 0 <= (today - end).days <= 21:
            return None
        spx = next((x for x in weekly.get("instruments", []) if x.get("instrument_id") == "sp500_futures"), None)
        if not spx or spx.get("data_quality") != "passed":
            return None
        signals = spx.get("signals") or {}
        ret = float(signals["ret20_pct"])
        close = float(signals["last_close"])
        if not (math.isfinite(ret) and -70 < ret < 70 and math.isfinite(close) and close > 0):
            return None
        return {"week_id": week, "week_end": end.isoformat(), "ret20_pct": round(ret, 2),
                "futures_last_close": round(close, 2), "source": f"/data/investments/weekly/{week}.json"}
    except (OSError, KeyError, ValueError, TypeError, StopIteration):
        return None


def category(score: int | None) -> str:
    if score is None:
        return "unassessed"
    if score >= 2:
        return "mild_positive"
    if score <= -2:
        return "defensive"
    return "neutral"


def factor_values(gdp: float, core: float, ten: float, oil: float, momentum: float | None) -> dict[str, int | None]:
    return {
        "growth": 1 if gdp >= 2 else (-2 if gdp < 0 else (-1 if gdp < 1 else 0)),
        "core_inflation": -2 if core >= 3.5 else (-1 if core >= 2.7 else (1 if core <= 2.3 else 0)),
        "ten_year_yield": -1 if ten >= 5 else (1 if ten <= 3.5 else 0),
        "oil": -1 if oil >= 110 else (1 if oil <= 70 else 0),
        "market_momentum": None if momentum is None else (1 if momentum >= 3 else (-1 if momentum <= -3 else 0)),
    }


def build_snapshot(observations: dict[str, list[tuple[date, float]]], today: date, weekly: dict | None,
                   previous: dict | None = None) -> dict:
    latest = {code: checked_latest(code, observations[code], today) for code in SERIES}
    if abs((latest["PCEPI"][0] - latest["PCEPILFE"][0]).days) > 35:
        raise PublicationBlocked("Headline and core PCE vintages diverge")
    gdp = qoq_saar(observations["GDPC1"])
    headline = yoy(observations["PCEPI"])
    core = yoy(observations["PCEPILFE"])
    ten = round(latest["DGS10"][1], 2)
    oil = round(latest["DCOILBRENTEU"][1], 2)
    market = weekly["ret20_pct"] if weekly else None
    f = factor_values(gdp, core, ten, oil, market)
    # These are transparent heuristic balances, never probabilities or price targets.
    scores = {
        "1M": None if market is None else 2 * f["market_momentum"] + f["core_inflation"] + f["ten_year_yield"] + f["oil"],
        "6M": 2 * f["growth"] + 2 * f["core_inflation"] + f["ten_year_yield"] + f["oil"],
        "12M": f["growth"] + f["core_inflation"] + f["ten_year_yield"] + f["oil"],
    }
    views = {
        horizon: {"bias": category(score), "risk_balance_score": score, "conviction": 3 if score is not None else None,
                  "confidence_note": "unvalidated_heuristic_not_probability"}
        for horizon, score in scores.items()
    }
    indicators = [
        {"id": code, "source": SERIES[code][0], "observation_date": latest[code][0].isoformat(),
         "observed_value": round(latest[code][1], 5), "source_url": SERIES_BASE + code}
        for code in SERIES
    ]
    prev_month = previous.get("month") if isinstance(previous, dict) else None
    changes = {
        h: {"previous_bias": previous["views"][h]["bias"], "current_bias": views[h]["bias"]}
        for h in views if isinstance(previous, dict) and isinstance(previous.get("views"), dict)
        and h in previous["views"] and isinstance(previous["views"][h], dict)
    }
    return {
        "schema_version": "1.0", "product": "BRIEFROOMS_SXP_MONTHLY_MACRO_HOUSE_VIEW",
        "month": today.strftime("%Y-%m"), "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "published_research_only", "method": "monthly_macro_risk_balance_v1",
        "as_of": today.isoformat(), "scope": "macro_and_tactical_risk_balance_not_earnings_or_valuation_forecast",
        "inputs": {"gdp_qoq_saar_pct": gdp, "pce_yoy_pct": headline, "core_pce_yoy_pct": core,
                   "us_10y_pct": ten, "brent_usd_bbl": oil, "spx_futures_20d_return_pct": market},
        "indicators": indicators, "weekly_market_observation": weekly, "factors": f, "views": views,
        "change_vs_previous": {"previous_month": prev_month, "bias_changes": changes},
        "governance": {"research_only": True, "trade_authority": False, "auto_promotion": False,
                       "earnings_and_valuation_coverage": False, "forecast_probability": None,
                       "failure_behavior": "do_not_publish; keep last verified month"}
    }


def publish(today: date, data_dir: Path = DATA) -> str:
    current = data_dir / "house_view_monthly_current.json"
    archives = data_dir / "house_view_monthly_archive"
    month = today.strftime("%Y-%m")
    target = archives / (month + ".json")
    if target.exists():
        stored = target.read_text(encoding="utf-8")
        parsed = json.loads(stored)
        if parsed.get("month") != month or parsed.get("schema_version") != "1.0":
            raise PublicationBlocked("Archive collision / invalid frozen snapshot")
        if not current.exists() or json.loads(current.read_text(encoding="utf-8")).get("month") != month:
            current.write_text(stored, encoding="utf-8")
        return f"skipped: {month} archive already frozen"
    previous = None
    if current.exists():
        previous = json.loads(current.read_text(encoding="utf-8"))
        if previous.get("month", "") >= month:
            raise PublicationBlocked("No retroactive/same-month overwrite allowed")
    observations = {code: fetch_series(code) for code in SERIES}
    weekly = weekly_spx_market_observation(today, data_dir)
    snapshot = build_snapshot(observations, today, weekly, previous)
    result = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    archives.mkdir(parents=True, exist_ok=True)
    # Persist frozen archive first; a re-run heals the 'current' pointer after interruption.
    target.write_text(result, encoding="utf-8")
    current.write_text(result, encoding="utf-8")
    return f"published: {month} ({weekly['week_id'] if weekly else 'no fresh WES SPX'})"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(timezone.utc).date())
    args = parser.parse_args()
    try:
        print(publish(args.as_of, args.data_dir))
        return 0
    except (PublicationBlocked, OSError, ValueError, KeyError, TimeoutError) as exc:
        print(f"HOUSE_VIEW_NO_PUBLICATION: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
