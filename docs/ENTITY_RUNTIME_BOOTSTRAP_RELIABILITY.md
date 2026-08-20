# PR #15.1 — Entity Runtime Bootstrap Reliability

This patch closes the production bootstrap gap discovered during the PR13 → PR14 → PR15 runtime audit.

## Problem

PR13 already bootstraps on a merge to `main` because its production workflow has a path-filtered `push` trigger. PR14 and PR15 previously depended only on `workflow_run`, `schedule`, or manual dispatch. A newly merged downstream layer could therefore miss its same-day scheduled slot and wait until a later upstream event or schedule before creating its first private state artifact.

## Fix

PR14 and PR15 production workflows now also trigger on a path-filtered push to `main` for their own production workflow, script, test, and documentation files.

The existing triggers remain unchanged:

- `workflow_dispatch`,
- upstream `workflow_run`,
- weekday schedule.

The existing concurrency groups remain unchanged with `cancel-in-progress: false`, preserving cumulative state serialization.

## Expected bootstrap behavior

```text
merge PR14-layer change
→ PR14 production run immediately eligible
→ PR14 state artifact

merge PR15-layer change
→ PR15 production run immediately eligible
→ restore latest PR14 state
→ PR15 state artifact
```

Normal upstream chaining remains active, so a successful PR14 run can still trigger PR15.

## Governance validation

`tests/test_entity_runtime_bootstrap_workflows.py` asserts that both production workflows retain:

- a `push` trigger on `main`,
- the frozen path filters,
- manual dispatch,
- upstream workflow chaining,
- scheduled execution,
- serialized cumulative-state protection.
