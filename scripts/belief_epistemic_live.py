#!/usr/bin/env python3
"""Build and persist the authoritative read-only Epistemic State projection."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from belief_epistemic_state import build_runtime_snapshot, persist_runtime_snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    snapshot = build_runtime_snapshot(state_dir)
    persist_runtime_snapshot(state_dir, snapshot)

    if args.print_summary:
        print(json.dumps({
            "contract_version": snapshot["contract_version"],
            "states": len(snapshot["states"]),
            "controls": snapshot["controls"],
        }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
