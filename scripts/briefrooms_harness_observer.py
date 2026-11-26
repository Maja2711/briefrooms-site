"""Read-only adapter for the existing Shadow Observatory."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from briefrooms_harness_core import plan
from shadow_engines_watchdog import evaluate


def observe(snapshot, now):
    check = evaluate(snapshot, now)
    observations = {"shadow_observatory": "STALE" if check["stale"] else "HEALTHY"}
    # Shadow reports are not evidence of production engine health.
    shadow = {}
    if not check["stale"]:
        for row in snapshot["engines"]:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            status = row.get("status")
            shadow[row["id"]] = {
                "reported_status": status,
                "last_run_at": row.get("last_run_at"),
                "intent": "INVESTIGATE" if status in ("ERROR", "STALE") else
                          "COLLECT_EVIDENCE" if status == "IDLE" else "OBSERVE",
            }
    result = plan(observations)
    result.update(generated_at=now.isoformat(), observatory_check=check,
                  shadow_observations=shadow)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        snapshot = None
    print(json.dumps(observe(snapshot, datetime.now(timezone.utc)), indent=2))


if __name__ == "__main__":
    main()
