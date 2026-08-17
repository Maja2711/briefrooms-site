#!/usr/bin/env python3
"""Verify frozen Belief Core forecasts from a batch JSON file."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from belief_core import BeliefCore


def main() -> int:
    p = argparse.ArgumentParser(description="Verify frozen Belief Core v2 forecasts.")
    p.add_argument("--input", required=True, help="JSON: {verifications:[{forecast_id,outcome,verified_at?,note?}]}.")
    p.add_argument("--state-dir", default="data/belief_core")
    args = p.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    core = BeliefCore(args.state_dir)
    done = []
    for item in payload.get("verifications", []):
        v = core.verify_forecast(str(item["forecast_id"]), bool(item["outcome"]),
                                 verified_at=item.get("verified_at"), note=str(item.get("note", "")),
                                 verification_id=item.get("verification_id"),
                                 allow_early=bool(item.get("allow_early", False)),
                                 outcome_source=str(item.get("outcome_source", "manual")),
                                 outcome_ref=item.get("outcome_ref"))
        done.append(v.to_dict())
    print(json.dumps({"verified": len(done), "calibration": core.calibration_summary()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
