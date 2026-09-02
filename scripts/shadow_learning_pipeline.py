#!/usr/bin/env python3
"""One-command P0 shadow learning pipeline.

Order is intentionally strict:
1. prospectively sync existing engine decisions/outcomes into Learning Ledger,
2. verify and materialize Experience Store,
3. evaluate current evidence of raw edge / formal alpha.

The pipeline remains research-shadow only and has no execution or writeback
authority over any decision engine.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

try:
    import learning_outcome_loop
    import experience_store
    import shadow_alpha_evaluator
except ModuleNotFoundError:
    from scripts import learning_outcome_loop, experience_store, shadow_alpha_evaluator


def run(
    *,
    state_dir: Path,
    investments_dir: Path,
    belief_state: Path | None,
    bootstrap: bool,
    now: datetime | None,
    minimum_sample: int,
) -> dict:
    sync = learning_outcome_loop.sync_all(
        state_dir,
        belief_state_path=belief_state,
        investments_dir=investments_dir,
        now=now,
        bootstrap=bootstrap,
    )
    ledger = state_dir / learning_outcome_loop.LEDGER_FILENAME
    store_path = state_dir / "experience_store.jsonl"
    store_status_path = state_dir / "experience_store_status.json"
    alpha_report_path = state_dir / "shadow_alpha_report.json"

    store = experience_store.materialize(ledger, store_path, store_status_path)
    report = shadow_alpha_evaluator.evaluate(
        experience_store.read_experiences(store_path),
        minimum=minimum_sample,
    )
    shadow_alpha_evaluator._atomic_json(alpha_report_path, report)
    return {
        "sync": sync,
        "experience_store": store,
        "shadow_alpha": report["overall"],
        "artifacts": {
            "ledger": str(ledger),
            "experience_store": str(store_path),
            "experience_store_status": str(store_status_path),
            "shadow_alpha_report": str(alpha_report_path),
        },
        "zero_authority": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run prospective shadow capture + Experience Store + alpha evidence evaluation")
    parser.add_argument("--state-dir", type=Path, default=Path("data/research"))
    parser.add_argument("--investments-dir", type=Path, default=Path("data/investments"))
    parser.add_argument("--belief-state", type=Path)
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--now")
    parser.add_argument("--minimum-sample", type=int, default=shadow_alpha_evaluator.MIN_SAMPLE)
    args = parser.parse_args()
    if args.minimum_sample < 2:
        parser.error("--minimum-sample must be >= 2")
    now = learning_outcome_loop.parse_time(args.now) if args.now else None
    result = run(
        state_dir=args.state_dir,
        investments_dir=args.investments_dir,
        belief_state=args.belief_state,
        bootstrap=args.bootstrap,
        now=now,
        minimum_sample=args.minimum_sample,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
