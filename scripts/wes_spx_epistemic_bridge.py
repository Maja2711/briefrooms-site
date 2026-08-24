#!/usr/bin/env python3
"""WES-SPX bridge wired through Epistemic Consumer Interface.

Keeps the existing prospective WES bridge semantics while replacing direct
Belief Core selection with the bounded authoritative EpistemicState projection.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import brace_spx_belief_bridge as belief_bridge
import investments_wes_belief_bridge as legacy
from epistemic_consumer_interface import EpistemicConsumerInterface


def run_bridge(bridge_dir: Path, wes_source_path: Path, epistemic_dir: Path, now: datetime):
    interface = EpistemicConsumerInterface.from_state_dir(epistemic_dir)

    def select_epistemic(_raw, as_of):
        return interface.point_in_time_projection("WES_SPX", as_of, max_age_hours=belief_bridge.MAX_BELIEF_AGE_HOURS)

    original = belief_bridge.select_frozen_belief_state
    belief_bridge.select_frozen_belief_state = select_epistemic
    try:
        return legacy.run_bridge(bridge_dir, wes_source_path, epistemic_dir / "epistemic_state.json", now)
    finally:
        belief_bridge.select_frozen_belief_state = original


def main() -> int:
    parser = argparse.ArgumentParser(description="Run WES-SPX through authoritative EpistemicState consumer interface")
    parser.add_argument("--bridge-dir", required=True)
    parser.add_argument("--wes-source", required=True)
    parser.add_argument("--epistemic-dir", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()
    now = legacy._dt(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise ValueError("invalid --now")
    result = run_bridge(Path(args.bridge_dir), Path(args.wes_source), Path(args.epistemic_dir), now)
    import json
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
