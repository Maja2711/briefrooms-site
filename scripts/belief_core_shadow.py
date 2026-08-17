#!/usr/bin/env python3
"""Run BriefRooms Belief Core in shadow mode and emit Belief Lab snapshot."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from belief_core import BeliefCore, load_input


def main() -> int:
    parser = argparse.ArgumentParser(description="Update BriefRooms Belief Core (shadow mode only).")
    parser.add_argument("--input", required=True, help="JSON file with belief definitions and evidence.")
    parser.add_argument("--state-dir", default="data/belief_core", help="Persistent state directory (default: data/belief_core).")
    args = parser.parse_args()
    definitions, evidence, as_of = load_input(args.input)
    core = BeliefCore(args.state_dir)
    core.register_beliefs(definitions)
    core.ingest(evidence)
    states = core.recompute(as_of=as_of)
    print(json.dumps({"mode":"shadow","beliefs":len(states),"evidence":len(core.evidence),
        "dashboard":str(Path(args.state_dir)/"dashboard.json"),"policy_output_enabled":False,
        "trade_execution_enabled":False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
