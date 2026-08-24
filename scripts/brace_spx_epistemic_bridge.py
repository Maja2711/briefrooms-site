#!/usr/bin/env python3
"""BRACE-SPX bridge wired through Epistemic Consumer Interface.

Reuses the existing proven prospective/read-only BRACE bridge machinery, but
replaces direct Belief Core selection with an authoritative EpistemicState
projection. Historical reconstruction is not attempted; point-in-time failures
fail closed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import brace_spx_belief_bridge as legacy
from epistemic_consumer_interface import EpistemicConsumerInterface


def run_bridge(bridge_dir: Path, epistemic_dir: Path, brace_shadow_path: Path, now: datetime):
    interface = EpistemicConsumerInterface.from_state_dir(epistemic_dir)

    def select_epistemic(_raw, as_of):
        return interface.point_in_time_projection("BRACE_SPX", as_of, max_age_hours=legacy.MAX_BELIEF_AGE_HOURS)

    original = legacy.select_frozen_belief_state
    legacy.select_frozen_belief_state = select_epistemic
    try:
        return legacy.run_bridge(bridge_dir, epistemic_dir / "epistemic_state.json", brace_shadow_path, now)
    finally:
        legacy.select_frozen_belief_state = original


def main() -> int:
    parser = argparse.ArgumentParser(description="Run BRACE-SPX through authoritative EpistemicState consumer interface")
    parser.add_argument("--bridge-dir", required=True)
    parser.add_argument("--epistemic-dir", required=True)
    parser.add_argument("--brace-shadow", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()
    now = legacy._dt(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise ValueError("invalid --now")
    result = run_bridge(Path(args.bridge_dir), Path(args.epistemic_dir), Path(args.brace_shadow), now)
    import json
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
