"""Experimental read-only Harness planner; no dispatch or production writes."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

# Logical dependencies, not permission to execute or promote.
DEPENDENCIES = {
    "belief": (),
    "epistemic": ("belief",),
    "daily_eurusd": ("epistemic",),
    "wes": (),
    "shadow_observatory": (),
    "evolution": (),
}
VALID = {"HEALTHY", "STALE", "FAILED", "RUNNING", "UNKNOWN"}


def plan(observations: dict, graph: dict = DEPENDENCIES) -> dict:
    """Return diagnostic intents only; unknown evidence never becomes healthy."""
    if not isinstance(observations, dict):
        raise ValueError("observations must be an object")
    order, visiting, done = [], set(), set()

    def visit(node):
        if node not in graph:
            raise ValueError("unknown dependency: " + node)
        if node in visiting:
            raise ValueError("dependency cycle")
        if node in done:
            return
        visiting.add(node)
        for dep in graph[node]:
            visit(dep)
        visiting.remove(node)
        done.add(node)
        order.append(node)

    for node in graph:
        visit(node)
    rows = {}
    for node in order:
        status = observations.get(node, "UNKNOWN")
        if not isinstance(status, str) or status not in VALID:
            status = "UNKNOWN"
        blocked = [dep for dep in graph[node] if rows[dep]["effective"] != "HEALTHY"]
        effective = "BLOCKED" if blocked else status
        intent = ("WAIT_UPSTREAM" if blocked else
                  "INVESTIGATE" if status in {"STALE", "FAILED"} else
                  "COLLECT_EVIDENCE" if status == "UNKNOWN" else
                  "OBSERVE")
        rows[node] = dict(observed=status, effective=effective,
                          blocked_by=blocked, intent=intent)
    return {"schema": "brs-harness-plan-v0", "authority": "OBSERVE_ONLY",
            "nodes": rows, "dispatch_enabled": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", help="JSON object of normalized health observations")
    args = parser.parse_args()
    with open(args.observations, encoding="utf-8") as handle:
        result = plan(json.load(handle))
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
