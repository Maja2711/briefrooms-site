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
    req = urllib.request.Request(BOND_TABLE_URL, headers={"User-Agent": "BriefRoomsQuotes/1.5"})
    with urllib.request.urlopen(req, timeout=16) as r:
        raw = r.read()
    for enc in ("utf-8", "cp1250", "iso-8859-2"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", errors="ignore")


def find_bond(symbol: str, key: str, data: Dict[str, Any], table_text: str, now: datetime) -> bool:
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
            "date": data.get("updated_at", now.isoformat(timespec="seconds"))[:10],
            "time": stamp,
            "source_url": BOND_TABLE_URL,
            "bond_update_policy": "every 15 minutes with investment room quote workflow",
            "bond_last_attempt_at": now.isoformat(timespec="seconds"),
            "bond_update_status": "updated_this_run",
        })
        return True
    return False


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
    ok_pl = False
    ok_us = False
    error = ""

    try:
        table = fetch_bond_table()
        ok_pl = find_bond("10YPLY.B", "pl10y", data, table, now)
        ok_us = find_bond("10YUSY.B", "us10y", data, table, now)
        if not (ok_pl or ok_us):
            preserve_previous_bonds(data, previous, now, "kept_previous_bond_table_not_found")
    except Exception as exc:
        error = str(exc)
        preserve_previous_bonds(data, previous, now, "kept_previous_bond_fetch_error")
        print(f"WARNING bond table import failed: {exc}")

    data["bond_schedule"] = {
        "timezone": "Europe/Warsaw",
        "frequency": "every 15 minutes",
        "last_attempt_at": now.isoformat(timespec="seconds"),
        "last_attempt_result": "updated" if (ok_pl or ok_us) else "kept_previous",
        "pl10y_updated": ok_pl,
        "us10y_updated": ok_us,
    }
    if error:
        data["bond_schedule"]["last_error"] = error

    data["bond_table_import"] = {
        "source": BOND_TABLE_URL,
        "policy": "Update bond yields on every 15-minute investment-room quote workflow run, same as FX/index/crypto.",
        "updated_this_run": ok_pl or ok_us,
        "pl10y": ok_pl,
        "us10y": ok_us,
    }
    data["refresh"] = "FX/index/crypto/bond yields: every 15 minutes."
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Bond table import: every_15m PL={ok_pl} US={ok_us}")


if __name__ == "__main__":
    main()
