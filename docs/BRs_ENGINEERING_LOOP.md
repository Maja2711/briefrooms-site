# Engineering Harness repair loop — development tooling

This is repository engineering tooling, not a production engine, promotion
controller or trading authority. The existing canonical architecture and AGENTS.md
remain authoritative. No runtime module changes or production state writes.

- brs_engineering_loop.py: deterministic DETECT → DIAGNOSE → REPAIR → VERIFY →
  LEARN transitions, at most three attempted repairs, and escalation on a
  repeated normalized textual hypothesis. COMPLETE is a task verdict, not merge
  permission.
- brs_engineering_checkpoint.py: SQLite checkpoint store, per-task monotonic
  revision, transactional compare-and-set. Use a private persistent local path
  outside the repository; do not commit databases, credentials or raw logs.
  read(path, task_id) returns revision and state; apply(path, task_id, revision,
  event) advances atomically or rejects the event. A caller must reload after
  revision conflict. This store does not coordinate across independent DB copies.
- Successful VERIFY through the checkpoint API additionally requires release
  evidence for the exact candidate SHA and four distinct declared identities
  (builder, tester, auditor, architect). This validates structure only, not the
  identity or authenticity of the evidence; a trusted executor must collect it.
- CI: Engineering Harness Validation runs unit tests and TOML syntax/basic role
  checks, read-only. It is not an actual Codex integration test.

Example (from scripts directory in Python):
    from brs_engineering_checkpoint import read, apply
    rev, state = read('/private/tasks.db', 'pr-1128')
    rev, state = apply('/private/tasks.db', 'pr-1128', rev, {
        'stage': 'DETECT', 'sha': '<40-character candidate SHA>',
        'evidence': '<reference to actual observation>'
    })

Outstanding: authenticated evidence collection, agent execution/dispatch,
durable cross-run storage, allowlisted repair adapters, independent verification
and supervised pilot. No schedule is activated by this change. Rollback: revert
the tooling changes; retain the private checkpoint for incident review. Never
use repair authority to force runtime fallback state, bypass CI, or alter trading.
