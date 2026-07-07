#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "investments" / "room_quotes.json"
WARSAW = ZoneInfo("Europe/Warsaw")
BOND_KEYS = ("pl10y", "us10y")

BOND_SOURCES = {
    "pl10y": {
        "url": "https://tradingeconomics.com/poland/government-bond-yield",
        "row": "Poland 10Y",
        "symbol": "PL10Y",
        "source": "Trading Economics: Poland 10Y",
    },
    "us10y": {
        "url": "https://tradingeconomics.com/united-states/government-bond-yield",
        "row": "US 10Y",
        "symbol": "US10Y",
        "source": "Trading Economics: US 10Y",
    },
}

sys.path.insert(0, str(ROOT / "scripts"))
import update_investment_room_quotes as base  # noqa: E402


def to_float(text: Any) -> Optional[float]:
    try:
        s = str(text).replace("%", "").replace("+", "").replace(" ", "").replace(",", ".").strip()
        if not s or s in {"-", "—", "-.---"}:
            return None
        return float(s)
    except Exception:
        return None


def load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def fetch_text(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 BriefRoomsQuotes/2.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=18) as r:
        raw = r.read()
    return raw.decode("utf-8", errors="ignore")


def clean_text(markup: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", markup, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_te_bond(text: str, row_name: str) -> Optional[Tuple[float, Optional[float], str]]:
    # Example after HTML cleanup:
    # Bonds Yield Day Month Year Date Poland 10Y 5.36 0.083% -0.510% -0.005% Jul/07
    row_re = re.compile(
        rf"{re.escape(row_name)}\s+([0-9]+(?:\.[0-9]+)?)\s+([+-]?[0-9]+(?:\.[0-9]+)?)%\s+[+-]?[0-9]+(?:\.[0-9]+)?%\s+[+-]?[0-9]+(?:\.[0-9]+)?%\s+([A-Za-z]{{3}}/\d{{2}})",
        re.I,
    )
    m = row_re.search(text)
    if m:
        close = to_float(m.group(1))
        day_change = to_float(m.group(2))
        if close is not None:
            return close, day_change, m.group(3)

    # Fallback to the narrative sentence if the table format changes.
    value_match = re.search(r"Bond Yield\s+(?:rose|fell|increased|decreased)\s+to\s+([0-9]+(?:\.[0-9]+)?)%", text, re.I)
    change_match = re.search(r"marking a\s+([0-9]+(?:\.[0-9]+)?)\s+percentage points\s+(increase|decrease)", text, re.I)
    date_match = re.search(r"on\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", text)
    if value_match:
        close = to_float(value_match.group(1))
        day_change = None
        if change_match:
            day_change = to_float(change_match.group(1))
            if day_change is not None and change_match.group(2).lower() == "decrease":
                day_change = -day_change
        return close, day_change, date_match.group(1) if date_match else ""
    return None


def update_bond(data: Dict[str, Any], key: str, now: datetime) -> bool:
    cfg = BOND_SOURCES[key]
    text = clean_text(fetch_text(cfg["url"]))
    parsed = parse_te_bond(text, cfg["row"])
    if not parsed:
        return False
    close, day_change, date_label = parsed
    quote = data.setdefault("quotes", {}).setdefault(key, {})
    quote.update({
        "source": cfg["source"],
        "symbol": cfg["symbol"],
        "close": close,
        "change_percent": day_change,
        "change_value": day_change,
        "date": now.date().isoformat(),
        "time": now.strftime("%H:%M"),
        "source_url": cfg["url"],
        "source_date_label": date_label,
        "bond_update_policy": "every 15 minutes with investment room quote workflow",
        "bond_last_attempt_at": now.isoformat(timespec="seconds"),
        "bond_update_status": "updated_this_run",
    })
    quote.pop("fallback_reason", None)
    return True


def preserve_previous_bonds(data: Dict[str, Any], previous: Dict[str, Any], now: datetime, reason: str) -> None:
    quotes = data.setdefault("quotes", {})
    previous_quotes = previous.get("quotes", {}) if isinstance(previous, dict) else {}
    for key in BOND_KEYS:
        old = previous_quotes.get(key)
        if isinstance(old, dict) and old.get("close") not in (None, "", 0, "0"):
            kept = dict(old)
            kept["bond_update_policy"] = "every 15 minutes with investment room quote workflow"
            kept["bond_last_attempt_at"] = now.isoformat(timespec="seconds")
            kept["bond_update_status"] = reason
            quotes[key] = kept


def refresh_live_prices() -> None:
    try:
        import investments_weekly
        investments_weekly.capture_live_prices()
        print("Live prices refreshed")
    except Exception as exc:
        print(f"WARNING live price refresh skipped: {exc}")


def main() -> None:
    now = datetime.now(WARSAW)
    previous = load_json(OUT)

    refresh_live_prices()
    base.main()

    data = load_json(OUT)
    ok = {"pl10y": False, "us10y": False}
    errors: Dict[str, str] = {}

    for key in BOND_KEYS:
        try:
            ok[key] = update_bond(data, key, now)
        except Exception as exc:
            errors[key] = str(exc)
            print(f"WARNING {key} bond update failed: {exc}")

    if not any(ok.values()):
        preserve_previous_bonds(data, previous, now, "kept_previous_bond_fetch_error")
    elif not all(ok.values()):
        preserve_previous_bonds(data, previous, now, "kept_previous_partial_bond_update")

    data["source"] = "Stooq/Yahoo for FX/index/crypto; Trading Economics for PL/US 10Y bond yields"
    data["refresh"] = "FX/index/crypto/bond yields: every 15 minutes."
    data["bond_schedule"] = {
        "timezone": "Europe/Warsaw",
        "frequency": "every 15 minutes",
        "last_attempt_at": now.isoformat(timespec="seconds"),
        "last_attempt_result": "updated" if any(ok.values()) else "kept_previous",
        "pl10y_updated": ok["pl10y"],
        "us10y_updated": ok["us10y"],
    }
    if errors:
        data["bond_schedule"]["last_errors"] = errors
    data["bond_table_import"] = {
        "source": "Trading Economics government-bond-yield pages",
        "policy": "Update bond yields on every 15-minute investment-room quote workflow run, same as FX/index/crypto.",
        "updated_this_run": any(ok.values()),
        "pl10y": ok["pl10y"],
        "us10y": ok["us10y"],
    }

    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Bond update: every_15m PL={ok['pl10y']} US={ok['us10y']}")


if __name__ == "__main__":
    main()
