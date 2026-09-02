#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from belief_aris_shadow import build_shadow_report, validate_shadow_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ARIS-inspired Belief Core shadow diagnostics")
    parser.add_argument("--state-dir", required=True, help="Authoritative Belief Core input directory (read-only)")
    parser.add_argument("--output-dir", required=True, help="Separate directory for ARIS shadow diagnostics")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    report = build_shadow_report(Path(args.state_dir), Path(args.output_dir))
    validate_shadow_report(report)
    if args.print_summary:
        summary = {
            "contract_version": report["contract_version"],
            "mode": report["mode"],
            "representation_namespace": report["representation_namespace"],
            "beliefs": len(report["beliefs"]),
            "decision_influence": report["authority"]["decision_influence"],
            "production_decision_influence": report["authority"]["production_decision_influence"],
            "writeback": report["authority"]["belief_core_writeback_enabled"],
            "consumer_export": report["authority"]["consumer_contract_export_enabled"],
            "trade_execution": report["authority"]["trade_execution_enabled"],
        }
        print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
