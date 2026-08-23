# PR28 — Learning Ledger / Outcome Loop Integration v1

## Purpose

PR28 connects the PR27 Learning Ledger to real existing BriefRooms producer state.
It is an observation/integration layer only. It does not learn weights and has no
right to change any engine decision.

## Sources

PR28 prospectively observes four existing sources:

1. **Belief Core v2** — frozen forecasts and later verifications from the private
   Belief Core shadow-state artifact.
2. **GPW Daily Stock** — published daily decisions and later resolved outcomes
   from the canonical GPW daily history.
3. **US Daily Stock** — canonical one-position decisions and later TP/SL/horizon
   outcomes from the PR26 lifecycle history.
4. **Daily EUR/USD Spot** — actual opened positions and later closed-trade
   outcomes from the native EUR/USD lifecycle.

Source engines remain authoritative. PR28 copies a bounded immutable learning
record and never writes back to them.

## Prospective activation boundary

The first successful production run creates `learning_loop_activation.json` and
an empty `learning_ledger.jsonl`. The activation timestamp is permanent.

Anything observed before that timestamp is outside PR28 learning history.
There is no historical backfill.

This intentionally sacrifices old samples to prevent a false claim that PR28
observed a forecast or decision before its outcome.

## Strict anti-hindsight binding

An outcome is accepted only when the upstream event was already in the Learning
Ledger **before the current collector cycle began**.

Therefore:

```text
cycle N
  forecast / decision observed
  -> append upstream event

cycle N+1 or later
  outcome / verification observed
  AND upstream event already existed at cycle start
  -> append outcome
```

If a forecast and its verification, or a trade and its resolved outcome, are
first seen together, PR28 excludes the pair as hindsight-contaminated. It does
not reconstruct a clean-looking pre-outcome state from files read after the
result was known.

A very fast trade that opens and closes between PR28 observations can therefore
be excluded. That is deliberate. Epistemic integrity has priority over sample
count.

## Normalized event records

### Belief Core forecast

The copied payload contains the frozen probability/confidence, target/horizon,
domain/entity/regime, outcome rule and representative Evidence IDs. Full source
Evidence text is not duplicated into the cross-engine ledger.

### Belief Core outcome and verification

The real binary outcome, source/reference, Brier score, log loss and calibration
eligibility are copied only for a previously observed forecast.

### GPW / US decision

The copied decision snapshot contains the decision, policy version, symbol,
score, reference/entry/stop/target/RR and validity fields. It excludes mutable
monitor marks.

No-trade daily decisions are valid decision events and can be retained when the
canonical producer state exposes them prospectively.

### GPW / US outcome

Resolved activation, entry/exit, exit reason, return, R-multiple, cost assumption
and settlement policy are copied only for a decision already present in the
ledger.

### EUR/USD decision

Only an actual OPEN position is treated as the durable decision event in v1.
Frequent FLAT/candidate refreshes are intentionally excluded to avoid turning a
5-minute monitor into noisy pseudo-decisions.

### EUR/USD outcome

Closed trade result, exit reason, return and R-multiple are copied only when the
open trade had already been observed by PR28.

## Private durable state

The BriefRooms repository is public. PR28 raw learning history must not be
committed to Git.

Production state is stored as private GitHub Actions artifacts:

- `learning-outcome-loop-state`
- `learning-outcome-loop-checkpoint`

The workflow restores an existing valid state before collection. Once a state
artifact exists, failure to restore/verify it is fatal; PR28 must not silently
create a new activation boundary.

The JSONL ledger remains SHA-256 hash chained and fail-closed on tampering.
Artifacts are refreshed by the collector schedule, so the two copies form a
renewed private lease. This is still not a claim of permanent archival storage;
a future off-platform archive can be reviewed separately.

## Authority

All of the following remain hard false:

- automatic tuning
- belief writeback
- Evidence-weight writeback
- causal-edge writeback
- source-state writeback
- engine-policy writeback
- ranking writeback
- sizing writeback
- trade execution
- automatic promotion
- historical backfill
- same-cycle outcome binding
- decision-engine influence

## What PR28 enables next

After enough prospectively clean events exist, later work can compute descriptive
learning diagnostics such as:

- forecast calibration and error by source/domain/regime/horizon,
- decision outcome distributions,
- WITH/WITHOUT marginal information value,
- LLM-derived Evidence usefulness,
- candidate policy changes.

Those diagnostics still do not authorize automatic policy changes. Any future
candidate adjustment must be separately defined, shadow tested prospectively and
promoted through an explicit reviewed gate.
