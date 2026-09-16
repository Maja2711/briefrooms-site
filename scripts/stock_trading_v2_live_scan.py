#!/usr/bin/env python3
"""Live stage-zero US discovery adapter for Stock Trading v2.

Keeps network/provider concerns out of the deterministic fast-scan feature
builder.  The adapter remains shadow-only and cannot admit a portfolio trade.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from scripts import stock_trading_v2_fast_scan as fast_scan
    from scripts import stock_trading_v2_us_screener_provider as provider
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_fast_scan as fast_scan
    import stock_trading_v2_us_screener_provider as provider


def run_live(
    *,
    universe_path: Path = fast_scan.US_UNIVERSE_PATH,
    config_path: Path = fast_scan.CONFIG_PATH,
) -> dict[str, Any]:
    universe_snapshot = fast_scan._read_json(universe_path)
    config = fast_scan.load_config(config_path)
    rows, meta = provider.fetch_rows()
    payload = fast_scan.build_shortlist(universe_snapshot, rows, config, source_meta=meta)
    if payload.get("governance", {}).get("production_decision_influence") is not False:
        raise RuntimeError("live stage-zero adapter escaped shadow governance")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", type=Path, default=fast_scan.US_UNIVERSE_PATH)
    parser.add_argument("--config", type=Path, default=fast_scan.CONFIG_PATH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = run_live(universe_path=args.universe, config_path=args.config)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "market": payload["market"],
                "universe_size": payload["universe_size"],
                "eligible_after_join": payload["eligible_after_join"],
                "shortlist_size": payload["shortlist_size"],
                "lane_sizes": payload["lane_sizes"],
                "source_mode": payload.get("source", {}).get("mode"),
                "production_decision_influence": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
