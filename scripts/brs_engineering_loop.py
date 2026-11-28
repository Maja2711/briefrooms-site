"""Pure engineering task transitions; no execution or release authority."""
import hashlib
import json
import re

MAX_ATTEMPTS = 3

def initial(task_id):
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task ID required")
    return {"task_id": task_id, "stage": "DETECT", "attempts": 0,
            "history": [], "failures": [], "release_authority": False}

def advance(state, event):
    # Copy so rejected transitions cannot mutate the checkpoint.
    s = json.loads(json.dumps(state))
    stage = s["stage"]
    if stage in ("COMPLETE", "ESCALATED"):
        raise ValueError("terminal task")
    if event.get("stage") != stage:
        raise ValueError("unexpected stage")
    sha = event.get("sha")
    evidence = event.get("evidence")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("candidate SHA required")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("evidence required")
    if stage == "DETECT":
        s["stage"] = "DIAGNOSE"
    elif stage == "DIAGNOSE":
        hypothesis = event.get("hypothesis")
        if not isinstance(hypothesis, str) or not hypothesis.strip():
            raise ValueError("hypothesis required")
        fingerprint = hashlib.sha256(hypothesis.strip().lower().encode()).hexdigest()
        if fingerprint in s["failures"] or s["attempts"] >= MAX_ATTEMPTS:
            s["stage"] = "ESCALATED"
        else:
            s["hypothesis"] = fingerprint
            s["stage"] = "REPAIR"
    elif stage == "REPAIR":
        s["attempts"] += 1
        s["candidate_sha"] = sha
        s["stage"] = "VERIFY"
    elif stage == "VERIFY":
        if sha != s["candidate_sha"] or type(event.get("passed")) is not bool:
            raise ValueError("verification must bind to candidate SHA and verdict")
        s["verified"] = event["passed"]
        s["stage"] = "LEARN"
    elif stage == "LEARN":
        if sha != s["candidate_sha"]:
            raise ValueError("learning must bind to verified SHA")
        if s["verified"]:
            s["stage"] = "COMPLETE"
        else:
            s["failures"].append(s["hypothesis"])
            s["stage"] = "ESCALATED" if s["attempts"] >= MAX_ATTEMPTS else "DIAGNOSE"
    else:
        raise ValueError("unknown stage")
    s["history"].append(dict(event))
    return s
