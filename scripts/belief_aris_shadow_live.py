#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from belief_aris_shadow import build_shadow_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ARIS-inspired Belief Core shadow diagnostics")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    report = build_shadow_report(Path(args.state_dir))
    if args.print_summary:
        summary = {
            "contract_version": report["contract_version"],
            "mode": report["mode"],
            "beliefs": len(report["beliefs"]),
            "decision_influence": report["authority"]["decision_influence"],
            "writeback": report["authority"]["belief_core_writeback_enabled"],
        }
        print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
