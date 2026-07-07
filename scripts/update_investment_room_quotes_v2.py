#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "investments" / "room_quotes.json"
BOND_TABLE_URL = "https://stooq.pl/t/?i=536"

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


def fetch_bond_table() -> str:
    req = urllib.request.Request(BOND_TABLE_URL, headers={"User-Agent": "BriefRoomsQuotes/1.3"})
    with urllib.request.urlopen(req, timeout=16) as r:
        raw = r.read()
    for enc in ("utf-8", "cp1250", "iso-8859-2"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", errors="ignore")


def find_bond(symbol: str, key: str, data: Dict[str, Any]) -> bool:
    text = fetch_bond_table()
    wanted = symbol.upper()
    for row in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", text, flags=re.I):
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
        })
        return True
    return False


def refresh_live_prices() -> None:
    try:
        import investments_weekly
        investments_weekly.capture_live_prices()
        print("Live prices refreshed")
    except Exception as exc:
        print(f"WARNING live price refresh skipped: {exc}")


def main() -> None:
    refresh_live_prices()
    base.main()
    data = json.loads(OUT.read_text(encoding="utf-8"))
    ok_pl = find_bond("10YPLY.B", "pl10y", data)
    ok_us = find_bond("10YUSY.B", "us10y", data)
    data["bond_table_import"] = {"source": BOND_TABLE_URL, "pl10y": ok_pl, "us10y": ok_us}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Bond table import: PL={ok_pl}, US={ok_us}")


if __name__ == "__main__":
    main()
