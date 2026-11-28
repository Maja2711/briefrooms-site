# Engineering Dispatcher + Persistent Orchestrator (supervised pilot)

This extends existing engineering development tooling, not investment runtime architecture.
No trading, deployment, production promotion, or merge authority. LE-11 remains unchanged.

The shared private SQLite database (brs_engineering_checkpoint.connect) now also
owns eng_runs and eng_jobs. The graph validates dependencies before enqueue.
Run IDs are immutable bindings to PR, SHA and DAG. Job claims are transactional,
fenced by random tokens with expiry. Failures block dependent tasks; unrelated
jobs may continue. Expired work escalates rather than blindly replaying an
ambiguous side effect. Restart workers with the same private DB and run ID.
This is not distributed storage across unrelated hosts.

Default DAG: authenticated GitHub observation -> search + grep -> architect ->
deterministic isolated profile repair -> audit + tester. The architect receives
bounded prior findings as untrusted data. Audit/tester are bound to candidate
file hashes. These are distinct role invocations of the installed Codex CLI,
not proof of independent identities or successful review. DONE means an adapter
returned a report, not that an engineering task is verified or ready to merge.
The repair adapter supports six profiles only. It does not execute a model's
proposed arbitrary patch. The release checkpoint gate remains separate; no
synthetic release evidence is created.

## Deployment prerequisites (trusted private runner, Python 3.11+)

- Trusted dedicated service account, installed and authenticated Codex CLI,
  git, private persistent storage and a clean trusted checkout at the exact PR SHA.
- Set BRS_GITHUB_READ_TOKEN using a secret manager; use a narrowly scoped
  GitHub App token or fine-grained token with Pull requests/Checks read access.
  Token never passed in the agent subprocess environment; do not configure
  GitHub credentials, unrelated secrets, untrusted MCP servers or broad
  permissions in the dedicated agent account. Do not run on untrusted forks.
- Secure the private storage directory (0700, umask 077); DB contains agent
  reports. Never commit or upload it publicly. Keep DB and candidate output
  outside checkout. No arbitrary URLs or GitHub write operations.
- API observes the PR and paginates check runs at the bound head; it does
  not establish required branch protection, status contexts or release readiness.

Example (environment already provisioned, replace SHA with actual trusted
checkout SHA and use PR 1128 only while it is open at that SHA):

    umask 077
    python scripts/brs_engineering_dispatcher.py --db /private/jobs.db enqueue pilot-1128 --pr 1128 --sha <40-hex-sha>
    python scripts/brs_engineering_dispatcher.py --db /private/jobs.db drain pilot-1128 --source /trusted/checkout --output-root /private/candidates
    python scripts/brs_engineering_dispatcher.py --db /private/jobs.db status pilot-1128

Drain processes at most seven jobs and returns nonzero if not all jobs finished.
Tick processes one job. Do not run against different checkouts/storage with
the same run ID. Restart after interruption with the same arguments, and
reconcile expired leases or incomplete candidate directories manually before a
new run. No scheduled runner installed by this PR. No auto-merge or GitHub
write; independently review candidate files before applying them to a worktree.
A separate authenticated verifier and policy-controlled repair writer are future
work. Codex subprocess authentication/real network calls require a live pilot.
Unit/integration tests use simulated external services, real SQLite and git
fixtures, and the real deterministic repair adapter.

Rollback: stop workers, retain private DB for review, discard candidate output
and revert tooling commits. No production runtime changes to roll back.
