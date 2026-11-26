"""Pure task state machine for diagnostic Harness work; no dispatch."""
from datetime import datetime, timedelta
import hashlib

MAX_ATTEMPTS = 3
LEASE_SECONDS = 300

def stamp(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("timezone required")
    return dt

def enqueue(state, incidents):
    tasks = {k: dict(v) for k,v in state.items()}
    for node, incident in incidents.items():
        if incident["state"] != "OPEN":
            continue
        key = hashlib.sha256(f"{node}:{incident['generation']}:{incident['first_seen']}".encode()).hexdigest()
        tasks.setdefault(key, dict(node=node, generation=incident["generation"],
                                  status="QUEUED", attempts=0, authority="OBSERVE_ONLY"))
    return tasks

def transition(task, event, at, evidence=None):
    t = dict(task)
    now = stamp(at)
    if t.get("authority") != "OBSERVE_ONLY":
        raise ValueError("invalid authority")
    if t.get("updated_at") and now <= stamp(t["updated_at"]):
        raise ValueError("non-monotonic transition")
    status = t["status"]
    if event == "CLAIM" and status in {"QUEUED", "RETRY_WAIT"}:
        if t.get("retry_at") and now < stamp(t["retry_at"]):
            raise ValueError("retry not due")
        if t["attempts"] >= MAX_ATTEMPTS:
            raise ValueError("attempt budget exhausted")
        t.update(status="RUNNING", attempts=t["attempts"]+1,
                 lease_until=(now+timedelta(seconds=LEASE_SECONDS)).isoformat())
    elif event == "RESULT" and status == "RUNNING":
        if now >= stamp(t["lease_until"]) or not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("expired lease or missing evidence")
        t.update(status="VERIFYING", result=evidence,
                 lease_until=(now+timedelta(seconds=LEASE_SECONDS)).isoformat())
    elif event == "VERIFY" and status == "VERIFYING":
        if now >= stamp(t["lease_until"]) or not isinstance(evidence, str) or not evidence.strip() or evidence == t["result"]:
            raise ValueError("fresh separate verification reference required")
        t.update(status="SUCCEEDED", verification=evidence)
    elif event == "FAIL" and status in {"RUNNING", "VERIFYING"} or event == "EXPIRE" and status in {"RUNNING", "VERIFYING"} and now >= stamp(t["lease_until"]):
        t.update(status="ESCALATED" if t["attempts"] >= MAX_ATTEMPTS else "RETRY_WAIT",
                 retry_at=(now+timedelta(seconds=60*2**(t["attempts"]-1))).isoformat())
    else:
        raise ValueError("invalid transition")
    t["updated_at"] = now.isoformat()
    return t
