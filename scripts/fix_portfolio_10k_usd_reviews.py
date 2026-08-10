#!/usr/bin/env python3
"""Rebuild English decision-journal summaries from native USD values."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "data/investments/portfolio_10k_usd.json"


def pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def main() -> int:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    portfolio_return = float(data.get("total_return_percent") or 0.0)
    benchmark_return = float(data.get("benchmark_return_percent") or (data.get("benchmark") or {}).get("return_percent") or 0.0)
    flagged = [
        f"{row.get('broker_symbol')} ({row.get('review_flag')})"
        for row in data.get("positions") or []
        if row.get("review_flag") not in {None, "HOLD"}
    ]
    flagged_text = ", ".join(flagged) if flagged else "none"
    reviews = []
    for source in data.get("weekly_reviews") or []:
        row = dict(source)
        row["summary_en"] = (
            f"The native USD model portfolio has returned {pct(portfolio_return)} since launch "
            f"versus {pct(benchmark_return)} for the USD-rebased benchmark. "
            f"The following require deeper review: {flagged_text}. A flag is not an automatic order."
        )
        row["summary_pl"] = (
            f"Portfel modelowy liczony natywnie w USD ma wynik {pct(portfolio_return)} od startu "
            f"wobec {pct(benchmark_return)} benchmarku przeliczonego do USD. "
            f"Pozycje do pogłębionego przeglądu: {flagged_text}. Flaga nie jest automatycznym zleceniem."
        )
        row["reporting_currency"] = "USD"
        row["portfolio_return_percent"] = round(portfolio_return, 8)
        row["benchmark_return_percent"] = round(benchmark_return, 8)
        reviews.append(row)
    data["weekly_reviews"] = reviews
    # Do not downgrade the schema written by the USD builder.  v1.2 adds the
    # independent Fed cash-yield ledger and executed-quantity mirroring.
    if not str(data.get("schema_version") or "").startswith("1.2"):
        data["schema_version"] = "1.2.0-usd"
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Rebuilt {len(reviews)} USD-native decision-journal entrie(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
