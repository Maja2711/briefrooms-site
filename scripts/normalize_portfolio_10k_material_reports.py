#!/usr/bin/env python3
"""Normalize generated material reports to the strict public schema."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "data/investments/portfolio_10k_material_reports.json"


def main() -> int:
    payload = json.loads(PATH.read_text(encoding="utf-8"))
    changed = 0
    for report in payload.get("reports") or []:
        quote = report.get("quote")
        if not isinstance(quote, dict):
            continue
        if quote.get("kind") == "LATEST_COMPLETED":
            quote["kind"] = "CLOSE"
            changed += 1
        if not quote.get("source"):
            quote["source"] = "Portfolio 10K stored market quote"
            changed += 1
    PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Normalized {changed} material-report field(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
