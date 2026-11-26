"""Single-writer shadow runtime. No workflow dispatch or production writes."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from briefrooms_harness_observer import observe
from briefrooms_harness_incidents import reconcile, atomic_write
from briefrooms_harness_tasks import enqueue, transition, stamp

SCHEMA = "brs-harness-runtime-v1"


def cycle(previous, snapshot, now):
    if previous.get("schema") != SCHEMA:
        raise ValueError("invalid runtime checkpoint")
    report = observe(snapshot, now)
    incidents = reconcile(previous["incident_state"], report)
    tasks = enqueue(previous["tasks"], incidents["incidents"])
    for key, task in list(tasks.items()):
        if task["status"] in {"RUNNING", "VERIFYING"} and now >= stamp(task["lease_until"]):
            tasks[key] = transition(task, "EXPIRE", now.isoformat())
        incident = incidents["incidents"].get(task["node"])
        if (incident and (incident["state"] == "RESOLVED" or
                          incident["generation"] != task["generation"])
                and tasks[key]["status"] in {"QUEUED", "RETRY_WAIT"}):
            tasks[key] = dict(tasks[key], status="CANCELLED", updated_at=now.isoformat())
    return dict(schema=SCHEMA, authority="OBSERVE_ONLY", report=report,
                incident_state=incidents, tasks=tasks)


def initial():
    return dict(schema=SCHEMA, incident_state=dict(
        schema="brs-harness-incidents-v1", incidents={}), tasks={})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    args = parser.parse_args()
    if args.snapshot.resolve() == args.checkpoint.resolve():
        raise ValueError("checkpoint must not overwrite source")
    previous = json.loads(args.checkpoint.read_text()) if args.checkpoint.exists() else initial()
    try:
        snapshot = json.loads(args.snapshot.read_text())
    except (OSError, ValueError):
        snapshot = None
    result = cycle(previous, snapshot, datetime.now(timezone.utc))
    atomic_write(args.checkpoint, result)
    print(json.dumps({"authority": result["authority"], "tasks": len(result["tasks"]),
                      "observatory": result["report"]["observatory_check"]}))


if __name__ == "__main__":
    main()
