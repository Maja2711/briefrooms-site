# Engineering Executor + Graph Coordinator — pilot

Development tooling only; no new runtime module or change to investment architecture.
AGENTS.md and canonical architecture rules remain authoritative.

- Graph: Job(id, action, deps, priority); validate the whole DAG before invoking
  trusted adapters; deterministic ready ordering, sequential fallback, dependency
  failure isolation. No real multi-agent process or parallel writer yet.
- Executor: allowlisted six Codex profile files only; detect SHA-256 snapshots,
  diagnose sandbox setting drift, generate candidate files in a fresh external
  directory, verify byte equality and TOML semantics, report lessons.
- Local verification does not authenticate reviewers or establish a Git commit
  SHA. The executor never submits release evidence to the checkpoint gate.
  Its report is not a COMPLETE verdict for an engineering task or PR.
- The source checkout is not written. Use trusted local directories, private
  output and a single operator; this is not an adversarial OS sandbox. A failed
  write can leave an incomplete candidate directory; never publish it.
- There is no arbitrary command execution, GitHub write, deployment, trading
  permission, cross-run queue or autonomous scheduled execution here.

Usage from a trusted checkout (Python 3.11+):
    python scripts/brs_engineering_executor.py --source . --output /private/new-candidate
    python -m unittest discover -s tests -p 'test_brs_engineering_*.py' -v

The output directory must not exist and must be outside the source tree.
An authorized builder can independently review and apply the candidate.
Use authenticated, candidate-commit-bound review and CI before marking a task
complete through brs_engineering_checkpoint.py.

Next integration: authenticated role dispatch, persistent orchestration and
idempotent execution leases, commit-bound evidence retrieval, supervised PR
repair adapters. Do not duplicate the production Evolution Controller (LE-11).
Rollback: remove/revert these development tooling files; discard pilot output.
