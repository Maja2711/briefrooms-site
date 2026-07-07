#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "investments" / "room_quotes.json"
BOND_TABLE_URL = "https://stooq.pl/t/?i=536"
WARSAW = ZoneInfo("Europe/Warsaw")
BOND_UPDATE_HOURS_WARSAW = {10, 13, 17}
BOND_KEYS = ("pl10y", "us10y")

sys.path.insert(0, str(ROOT / "scripts"))
import update_investment_room_quotes as base  # noqa: E402


def to_float(text: Any) -> Optional[float]:
    try:
        s = str(text).replace("%", "").replace("+", "").replace(" ", "").replace(",", ".").strip()
        if not s or s in {"-", "—"}:
            return None
        return float(s)
    except Exception:
        return None


def clean(cell: str) -> str:
    cell = re.sub(r"<script[\s\S]*?</script>", " ", cell, flags=re.I)
    cell = re.sub(r"<style[\s\S]*?</style>", " ", cell, flags=re.I)
    cell = re.sub(r"<[^>]+>", " ", cell)
    cell = html.unescape(cell)
    return re.sub(r"\s+", " ", cell).strip()


def load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def fetch_bond_table() -> str:
    req = urllib.request.Request(BOND_TABLE_URL, headers={"User-Agent": "BriefRoomsQuotes/1.4"})
    with urllib.request.urlopen(req, timeout=16) as r:
        raw = r.read()
    for enc in ("utf-8", "cp1250", "iso-8859-2"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", errors="ignore")


def find_bond(symbol: str, key: str, data: Dict[str, Any], table_text: str) -> bool:
    wanted = symbol.upper()
    for row in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", table_text, flags=re.I):
        if wanted.lower() not in row.lower():
            continue
        cells = [clean(x) for x in re.findall(r"<td[^>]*>([\s\S]*?)</td>", row, flags=re.I)]
        if len(cells) < 6:
            continue
        idx = next((i for i, c in enumerate(cells) if wanted in c.upper()), 0)
        close = to_float(cells[idx + 2] if len(cells) > idx + 2 else None)
        change_pct = to_float(cells[idx + 3] if len(cells) > idx + 3 else None)
        change_val = to_float(cells[idx + 4] if len(cells) > idx + 4 else None)
        stamp = cells[idx + 5] if len(cells) > idx + 5 else ""
        if close is None:
            continue
        quote = data.setdefault("quotes", {}).setdefault(key, {})
        quote.update({
            "source": "Stooq.pl bond table",
            "symbol": symbol.lower(),
            "close": close,
            "change_percent": change_pct,
            "change_value": change_val,
            "date": data.get("updated_at", "")[:10],
            "time": stamp,
            "source_url": BOND_TABLE_URL,
            "bond_update_policy": "Europe/Warsaw 10:00, 13:00, 17:00",
        })
        return True
    return False


def current_slot(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H")


def should_update_bonds(previous: Dict[str, Any], now: datetime) -> bool:
    if now.hour not in BOND_UPDATE_HOURS_WARSAW:
        return False
    last_slot = str(previous.get("bond_schedule", {}).get("last_success_slot") or "")
    return last_slot != current_slot(now)


def preserve_previous_bonds(data: Dict[str, Any], previous: Dict[str, Any], now: datetime) -> None:
    quotes = data.setdefault("quotes", {})
    previous_quotes = previous.get("quotes", {}) if isinstance(previous, dict) else {}
    for key in BOND_KEYS:
        old = previous_quotes.get(key)
        if isinstance(old, dict) and old.get("close") not in (None, "", 0, "0"):
            quotes[key] = old
            quotes[key]["bond_update_policy"] = "Europe/Warsaw 10:00, 13:00, 17:00"
            quotes[key]["bond_update_status"] = f"kept_previous_outside_slot_{now.hour:02d}:00_Warsaw"


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
    do_bond_update = should_update_bonds(previous, now)
    ok_pl = False
    ok_us = False

    if do_bond_update:
        table = fetch_bond_table()
        ok_pl = find_bond("10YPLY.B", "pl10y", data, table)
        ok_us = find_bond("10YUSY.B", "us10y", data, table)
        if ok_pl or ok_us:
            data["bond_schedule"] = {
                "timezone": "Europe/Warsaw",
                "hours": [10, 13, 17],
                "last_success_slot": current_slot(now),
                "last_success_at": now.isoformat(timespec="seconds"),
            }
        else:
            data["bond_schedule"] = {
                "timezone": "Europe/Warsaw",
                "hours": [10, 13, 17],
                "last_attempt_slot": current_slot(now),
                "last_attempt_at": now.isoformat(timespec="seconds"),
                "last_attempt_result": "bond_table_not_found",
            }
    else:
        preserve_previous_bonds(data, previous, now)
        data["bond_schedule"] = {
            **(previous.get("bond_schedule", {}) if isinstance(previous, dict) else {}),
            "timezone": "Europe/Warsaw",
            "hours": [10, 13, 17],
            "current_run": now.isoformat(timespec="seconds"),
            "current_run_status": "bond_quotes_kept_previous_outside_update_slot",
        }

    data["bond_table_import"] = {
        "source": BOND_TABLE_URL,
        "policy": "Update bond yields only at 10:00, 13:00 and 17:00 Europe/Warsaw; other instruments keep 15-minute refresh.",
        "updated_this_run": do_bond_update and (ok_pl or ok_us),
        "pl10y": ok_pl,
        "us10y": ok_us,
    }
    data["refresh"] = "FX/index/crypto: every 15 minutes. Bond yields: 10:00, 13:00, 17:00 Europe/Warsaw."
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Bond table import: update_slot={do_bond_update} PL={ok_pl} US={ok_us}")


if __name__ == "__main__":
    main()
