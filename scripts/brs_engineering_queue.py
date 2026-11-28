"""Persistent engineering job queue; private single SQLite DB, no release authority."""
import json
import re
import time
import uuid
from contextlib import contextmanager, closing
from brs_engineering_checkpoint import connect
from brs_engineering_graph import Job, execute

ACTIONS = ("github", "search", "grep", "architect", "repair_profiles", "audit", "tester")
PLAN = (
    Job("observe", "github"),
    Job("search", "search", ("observe",)),
    Job("grep", "grep", ("observe",)),
    Job("architect", "architect", ("search", "grep")),
    Job("candidate", "repair_profiles", ("architect",)),
    Job("audit", "audit", ("candidate",)),
    Job("tester", "tester", ("candidate",)),
)
ID = re.compile(r"[a-zA-Z0-9_-]{1,80}")
SHA = re.compile(r"[0-9a-f]{40}")

@contextmanager
def transaction(path):
    with closing(connect(path)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS eng_runs (id TEXT PRIMARY KEY, spec TEXT NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS eng_jobs
            (run TEXT NOT NULL, id TEXT NOT NULL, action TEXT NOT NULL,
             deps TEXT NOT NULL, priority INTEGER NOT NULL, state TEXT NOT NULL,
             token TEXT, expires REAL, attempts INTEGER NOT NULL DEFAULT 0,
             result TEXT, PRIMARY KEY(run,id))""")
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise

def enqueue(path, run, pr, sha, jobs=PLAN):
    if not isinstance(run, str) or not ID.fullmatch(run) or type(pr) is not int or pr < 1 or not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise ValueError("invalid run, PR or SHA")
    jobs = tuple(jobs)
    if not jobs:
        raise ValueError("empty plan")
    # Validate DAG before any DB changes; no real adapters invoked.
    execute(jobs, {a: lambda _: {"status": "PASS"} for a in ACTIONS})
    if any(not ID.fullmatch(j.id) or type(j.priority) is not int for j in jobs):
        raise ValueError("invalid job ID or priority")
    spec = json.dumps({"pr": pr, "sha": sha, "jobs": [dict(id=j.id, action=j.action, deps=j.deps, priority=j.priority) for j in jobs]}, sort_keys=True)
    with transaction(path) as db:
        existing = db.execute("SELECT spec FROM eng_runs WHERE id=?", (run,)).fetchone()
        if existing:
            if existing[0] != spec:
                raise ValueError("run ID already bound to another plan")
            return False
        db.execute("INSERT INTO eng_runs VALUES (?,?)", (run, spec))
        db.executemany("INSERT INTO eng_jobs(run,id,action,deps,priority,state) VALUES (?,?,?,?,?,'QUEUED')",
                       [(run,j.id,j.action,json.dumps(j.deps),j.priority) for j in jobs])
        return True

def claim(path, run, lease_seconds=300):
    if not 1 <= lease_seconds <= 3600:
        raise ValueError("invalid lease")
    with transaction(path) as db:
        now = time.time()
        spec = db.execute("SELECT spec FROM eng_runs WHERE id=?", (run,)).fetchone()
        if not spec:
            raise ValueError("unknown run")
        # An expired execution may have produced external effects. Never blindly replay.
        db.execute("UPDATE eng_jobs SET state='ESCALATED',token=NULL,expires=NULL,result=? WHERE run=? AND state='RUNNING' AND expires<=?",
                   (json.dumps({"status":"FAIL","reason":"expired lease; reconcile before retry"}),run,now))
        rows = db.execute("SELECT id,action,deps,state,result FROM eng_jobs WHERE run=? ORDER BY priority DESC,id",(run,)).fetchall()
        states = {r[0]:r[3] for r in rows}
        changed = True
        while changed:
            changed = False
            for key,_,deps,_,_ in rows:
                if states[key]=="QUEUED" and any(states[d] in ("FAILED","BLOCKED","ESCALATED") for d in json.loads(deps)):
                    db.execute("UPDATE eng_jobs SET state='BLOCKED' WHERE run=? AND id=?",(run,key))
                    states[key]="BLOCKED"
                    changed=True
        for key,action,deps,_,_ in rows:
            if states[key]=="QUEUED" and all(states[d]=="DONE" for d in json.loads(deps)):
                token=uuid.uuid4().hex
                db.execute("UPDATE eng_jobs SET state='RUNNING',token=?,expires=?,attempts=attempts+1 WHERE run=? AND id=?",(token,now+lease_seconds,run,key))
                return {"run":run,"id":key,"action":action,"token":token,"spec":json.loads(spec[0])}
        return None

def finish(path, job, result):
    if not isinstance(result,dict) or result.get("status") not in ("PASS","FAIL"):
        raise ValueError("invalid result")
    data=json.dumps(result,sort_keys=True)
    if len(data.encode())>131072:
        raise ValueError("result too large")
    with transaction(path) as db:
        count=db.execute("""UPDATE eng_jobs SET state=?,result=?,token=NULL,expires=NULL
            WHERE run=? AND id=? AND state='RUNNING' AND token=? AND expires>?""",
            ("DONE" if result["status"]=="PASS" else "FAILED",data,job["run"],job["id"],job["token"],time.time())).rowcount
        if count != 1:
            raise ValueError("expired or stale lease")

def status(path,run):
    with transaction(path) as db:
        return [{"id":key,"state":state,"attempts":attempts,"result":json.loads(result) if result else None}
                for key,state,attempts,result in db.execute("SELECT id,state,attempts,result FROM eng_jobs WHERE run=? ORDER BY id",(run,))]
