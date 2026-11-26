"""Single-writer incident reconciliation; no execution authority."""
import argparse
import json
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone


def reconcile(previous, report):
    if previous.get("schema") != "brs-harness-incidents-v1":
        raise ValueError("invalid incident state schema")
    if report.get("authority") != "OBSERVE_ONLY" or report.get("dispatch_enabled") is not False:
        raise ValueError("invalid report authority")
    now = datetime.fromisoformat(report["generated_at"])
    if now.tzinfo is None:
        raise ValueError("missing timezone")
    last = previous.get("last_report_at")
    if last and now <= datetime.fromisoformat(last):
        raise ValueError("non-monotonic report")
    incidents = {k: dict(v) for k, v in previous["incidents"].items()}
    for node, row in report["nodes"].items():
        status = row["effective"]
        old = incidents.get(node)
        if status in {"FAILED", "STALE"}:
            if old and old["state"] == "OPEN":
                old.update(last_seen=now.isoformat(), status=status)
            else:
                incidents[node] = dict(state="OPEN", first_seen=now.isoformat(),
                                       last_seen=now.isoformat(), status=status,
                                       generation=(old or {}).get("generation", 0)+1)
        elif status == "HEALTHY" and old and old["state"] == "OPEN":
            old.update(state="RESOLVED", resolved_at=now.isoformat())
    return dict(schema="brs-harness-incidents-v1", last_report_at=now.isoformat(),
                incidents=incidents, authority="OBSERVE_ONLY")


def atomic_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    if args.state.exists():
        previous = json.loads(args.state.read_text())
    else:
        previous = dict(schema="brs-harness-incidents-v1", incidents={})
    report = json.loads(args.report.read_text())
    atomic_write(args.state, reconcile(previous, report))


if __name__ == "__main__":
    main()
