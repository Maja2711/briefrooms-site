#!/usr/bin/env python3
"""Append reviewed exchange-calendar receipts for prospective research only.

No execution/session gate reads this calendar. US reuses the existing official
NYSE adapter. GPW dates are transcribed from https://www.gpw.pl/szczegoly-sesji
(2026/2027), reviewed 2026-10-28. An exceptional closure requires a reviewed
source update; later provider disagreement blocks evaluation, never shifts its
predeclared endpoint. This is a published schedule, not proof a session occurred.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from scripts import nyse_session_calendar as nyse
except ModuleNotFoundError:  # pragma: no cover
    import nyse_session_calendar as nyse

ROOT = Path(__file__).resolve().parents[1]
RECEIPT_ROOT = ROOT / "data/investments/stock_trading_session_calendars"
SCHEMA = "stock-trading-session-calendar-v1"
GPW_YEARS = frozenset({2026, 2027})
GPW_CLOSED = frozenset({
    "2026-01-01", "2026-01-06", "2026-04-03", "2026-04-06", "2026-05-01",
    "2026-06-04", "2026-11-11", "2026-12-24", "2026-12-25", "2026-12-31",
    "2027-01-01", "2027-01-06", "2027-03-26", "2027-03-29", "2027-05-03",
    "2027-05-27", "2027-11-01", "2027-11-11", "2027-12-24", "2027-12-31",
})
MARKETS = {
    "GPW": ("Europe/Warsaw", "XWAR_REVIEWED_2026_2027", "https://www.gpw.pl/szczegoly-sesji"),
    "US": ("America/New_York", "NYSE_OFFICIAL_2026_2028", "https://www.nyse.com/trade/hours-calendars"),
}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _dt(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("calendar receipt requires timezone-aware time")
    return result


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_receipt(market: str, *, now: datetime, days: int = 180) -> dict[str, Any]:
    if market not in MARKETS or now.tzinfo is None or not 1 <= days <= 366:
        raise ValueError("invalid calendar request")
    tz_name, calendar_id, url = MARKETS[market]
    tz = ZoneInfo(tz_name)
    first = now.astimezone(tz).date()
    verified_years = GPW_YEARS if market == "GPW" else nyse.VERIFIED_YEARS
    if first.year not in verified_years:
        raise ValueError("exchange calendar horizon is unverified")
    end = min(first + timedelta(days=days), date(max(verified_years) + 1, 1, 1))
    sessions = []
    day = first
    while day < end:
        if market == "GPW":
            opened = day.year in GPW_YEARS and day.weekday() < 5 and day.isoformat() not in GPW_CLOSED
            opening, closing = time(9), time(17, 5)
        else:
            status = nyse.session_for(day)
            opened = status["calendar_status"] == "VERIFIED" and status["session_open"]
            opening, closing = time(9, 30), status["close_time"]
        if opened:
            sessions.append({"date": day.isoformat(),
                             "regular_start": _iso(datetime.combine(day, opening, tz)),
                             "regular_end": _iso(datetime.combine(day, closing, tz))})
        day += timedelta(days=1)
    body = {
        "schema_version": SCHEMA, "market": market, "timezone": tz_name,
        "source": "REVIEWED_OFFICIAL_EXCHANGE_SCHEDULE", "source_url": url,
        "hours_source_url": ("https://www.gpw.pl/pub/GPW/files/PDF/regulacje/SZOG_GPW_WATS_pl.pdf"
                             if market == "GPW" else url),
        "source_reviewed_on": "2026-10-28", "calendar_id": calendar_id,
        "authority": "NON_EXECUTABLE_PROSPECTIVE_RESEARCH_CALENDAR",
        "observed_at": _iso(now), "received_at": _iso(now),
        "observation_semantics": "LOCAL_RECEIPT_OF_REVIEWED_PUBLISHED_SCHEDULE_NOT_LIVE_EXCHANGE_STATUS",
        "coverage_start": _iso(datetime.combine(first, time(), tz)),
        "coverage_end": _iso(datetime.combine(end, time(), tz)),
        "generator_sha256": _hash({Path(__file__).name: hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                    "nyse_session_calendar.py": hashlib.sha256(Path(nyse.__file__).read_bytes()).hexdigest()}),
        "sessions": sessions,
    }
    return {**body, "calendar_sha256": _hash(body)}


def validate_receipt(receipt: dict[str, Any]) -> None:
    body = dict(receipt)
    digest = body.pop("calendar_sha256", None)
    if (digest != _hash(body) or body.get("schema_version") != SCHEMA
            or body.get("market") not in MARKETS):
        raise ValueError("calendar receipt integrity failed")
    observed, received = _dt(body["observed_at"]), _dt(body["received_at"])
    if observed > received or _dt(body["coverage_start"]) >= _dt(body["coverage_end"]):
        raise ValueError("calendar receipt chronology failed")


def collect(*, now: datetime | None = None, root: Path = RECEIPT_ROOT) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("calendar collection requires timezone-aware time")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    result = {}
    for market in MARKETS:
        path = root / f"{moment.astimezone(timezone.utc).date().isoformat()}-{market}.json"
        if path.exists():
            receipt = json.loads(path.read_text(encoding="utf-8"))
            validate_receipt(receipt)
            if receipt["market"] != market or _dt(receipt["received_at"]) > moment:
                raise ValueError("calendar receipt identity/clock mismatch")
        else:
            try:
                receipt = build_receipt(market, now=moment)
            except ValueError as exc:
                result[market] = {"status": "PENDING", "reason": str(exc)}
                continue
            with path.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        result[market] = {"status": "RECORDED", "calendar_sha256": receipt["calendar_sha256"],
                          "sessions": len(receipt["sessions"]), "path": str(path)}
    return result


def load_receipts(*, now: datetime, root: Path = RECEIPT_ROOT) -> dict[str, Any]:
    if now.tzinfo is None:
        raise ValueError("calendar read requires timezone-aware time")
    selected = {}
    for path in sorted(Path(root).glob("*.json")):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        validate_receipt(receipt)
        market = receipt["market"]
        if (_dt(receipt["received_at"]) <= now
                and _dt(receipt["coverage_start"]) <= now < _dt(receipt["coverage_end"])
                and (market not in selected or _dt(selected[market]["received_at"]) < _dt(receipt["received_at"]))):
            selected[market] = receipt
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=RECEIPT_ROOT)
    args = parser.parse_args()
    print(json.dumps(collect(root=args.root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
