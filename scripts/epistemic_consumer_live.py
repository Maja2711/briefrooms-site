#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from epistemic_consumer_interface import build_consumer_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Build BRACE/WES EpistemicState consumer bundle")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    payload = build_consumer_bundle(Path(args.state_dir))
    if args.print_summary:
        print(json.dumps({
            "contract_version": payload["contract_version"],
            "consumers": {k: {"available": v["available"], "stance": v["stance"], "drilldown_required": v["drilldown_required"]} for k, v in payload["consumers"].items()},
            "decision_writeback_enabled": payload["authority"]["decision_writeback_enabled"],
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
