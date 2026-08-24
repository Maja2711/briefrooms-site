# PR37 — Autonomous Policy Observatory / Report v1

## Purpose

PR37 makes the PR35/PR36 self-improvement loop observable without giving the report any decision authority.

It answers, per autonomous engine:

- which policy is active,
- which bounded parameter is active and at what value,
- whether a challenger exists,
- training and validation sample sizes,
- progress to the statistical gate,
- challenger net incremental result after cost stress,
- bootstrap confidence interval,
- statistical status and blocking reasons,
- recent promotion / hold / rejection / rollback events.

## Two views

### Public sanitized view

`data/public/autonomous_policy_observatory.json`

Contains only decision-policy status and sanitized metrics. It excludes private policy IDs, source candidate IDs, registry hashes and raw private evidence.

### Private audit view

`autonomous_policy_observatory_private.json` inside the existing autonomous-policy artifact.

It contains the validated registry, authorizations, PR35 status and PR36 report for audit/reconstruction.

## No-write authority

PR37 cannot:

- create a candidate,
- promote a candidate,
- reject a candidate,
- roll back a policy,
- modify engine configuration,
- execute a trade.

It runs only after PR35 and PR36 have completed their cycle.

## Churn control

The public report has a deterministic `state_digest`. The repository file is rewritten only when meaningful observatory state changes. Re-running the same state does not create a new public commit.

Meaningful changes include, for example:

- challenger creation,
- validation N increase,
- statistical metrics change,
- Promotion Gate state change,
- policy promotion,
- rollback,
- transition block/unblock.

## Example

```json
{
  "engine": "gpw_daily",
  "active": {
    "parameter": "minimum_composite_score",
    "value": 72,
    "revision": 0,
    "statistically_authorized": true
  },
  "challenger": {
    "from_value": 72,
    "to_value": 71,
    "training_n": 34,
    "validation_n": 12,
    "statistical_status": "COLLECTING",
    "progress": {"paired_n": 12, "required_n": 25, "progress_percent": 48},
    "net_incremental_mean_percent": 0.31,
    "confidence_interval_percent": [0.05, 0.55]
  }
}
```

## Flow

```text
PR35 candidate / rollback state
            +
PR36 statistical authorization
            ↓
PR37 read-only observatory
      ┌─────┴─────┐
      ↓           ↓
private audit   sanitized public report
```
