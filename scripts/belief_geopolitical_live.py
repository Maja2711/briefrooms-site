#!/usr/bin/env python3
"""Ingest qualified frozen GSE forecasts into Belief Core shadow state.

This step runs before the normal market Belief cycle so any qualified GSE v1
Evidence is available to the next frozen Belief snapshot. It has no engine or
execution connection.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from belief_core import BeliefCore, iso_z, parse_time
from belief_core_live import BELIEFS, append_observations, load_scheduler, save_scheduler
from belief_geopolitical_forecast_adapter import GeopoliticalForecastAdapter

MODE = "shadow"
TRADE_EXECUTION_ENABLED = False
POLICY_OUTPUT_ENABLED = False
DECISION_ENGINE_CONNECTED = False


def run_cycle(state_dir: Path, gse_state_dir: Path, now: datetime) -> dict:
    if TRADE_EXECUTION_ENABLED or POLICY_OUTPUT_ENABLED or DECISION_ENGINE_CONNECTED:
        raise RuntimeError("GSE -> Belief shadow ingest safety invariant violated")
    state_dir.mkdir(parents=True, exist_ok=True)
    core = BeliefCore(state_dir)
    core.register_beliefs(BELIEFS)
    result = GeopoliticalForecastAdapter().run(gse_state_dir, now)
    observations_written = append_observations(state_dir, result.observations)
    core.ingest(result.evidence)
    core.save()

    scheduler = load_scheduler(state_dir)
    scheduler["schema_version"] = 2
    scheduler["last_geopolitical_run_at"] = iso_z(now)
    scheduler["last_geopolitical_status"] = {
        "adapter": result.adapter,
        "observations_seen": len(result.observations),
        "observations_written": observations_written,
        "evidence_ingested": len(result.evidence),
        "mode": MODE,
        "decision_engine_connected": False,
        "trade_execution_enabled": False,
        "policy_output_enabled": False,
    }
    save_scheduler(state_dir, scheduler)
    return scheduler["last_geopolitical_status"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest frozen GSE forecasts into Belief Core shadow state")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--gse-state-dir", required=True)
    parser.add_argument("--now", help="ISO timestamp override")
    args = parser.parse_args()
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    status = run_cycle(Path(args.state_dir), Path(args.gse_state_dir), now)
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
