# BriefRooms LAB / L3 P1 — Hypothesis Utility Engine

Status: research Shadow, read-only. This stage is a **measurement and review proposal**
mechanism, **not** autonomous methodology mutation, retirement, challenger promotion
or production execution.

## Inputs and lineage

- Canonical cumulative \`state.json\` (immutable frozen forecasts and real verifications).
- Optional \`L3A_EXPERIENCE_STATE.json\` with registered research attempts and settled
  \`future_metrics.verification_id\`, \`p_before\` and \`brier_gain_vs_pre_research\`.
- Event identity from \`forecast_event_identity.identity\`, including exact frozen
  outcome contract, target time, hypothesis version, and symbol/threshold.
- Read-only result: \`HYPOTHESIS_UTILITY_REPORT.json\` inside the same cumulative
  state artifact, plus an explicitly sanitized \`hypothesis_utility\` subdocument in
  \`data/investments/decision_lab_public.json\`.

## Predictive Utility

1. Resolve verifications via \`forecast_id\`; reject early/nonprospective, invalid
   probability, missing timestamp, invalid outcome, duplicated verification and
   identity mismatch. Quarantine event-level conflicting outcomes.
2. One **first-frozen** revision per immutable event for headline proper scoring.
   Revisions remain available in the full frozen-forecast state/history.
3. Measure mean Brier, ECE, calibration bias, Brier Skill vs 50/50.
4. Compare paired Brier against a **prequential** Jeffreys-smoothed base-rate
   benchmark. Only previously *verified* events known by the frozen forecast time
   can update the baseline. This avoids future outcome leakage and the overfitting
   of an in-sample empirical base rate.
5. Report independently observed event count, distinct target dates and
   per-horizon diagnostic slices. Approximate gain intervals are descriptive;
   temporal dependence is **not** removed by these intervals.

**P1 review policy (modifiable only by code review):**

- \`COLLECTING / COLLECT_MORE\`: fewer than 30 independent resolved events or
  fewer than 10 distinct target dates.
- \`CHALLENGER / CHALLENGER_RECOMMENDED\`: at least 50 independent events,
  at least 15 target dates and prequential Brier gain below -0.03.
- \`REVIEW\`: remaining adequately sampled cases with negative paired prequential
  gain or ECE above 10 percentage points.
- \`ACTIVE / MAINTAIN\`: adequately sampled and no material adverse diagnostic.
- Cross-horizon comparison remains a warning. No score establishes causality.

A recommendation is **not** a promotion or retirement authorization. P2 is
responsible for independent challenger construction, prospective OOS evidence,
governed promotion gate, rollback and lifecycle write authority.

## Research Utility

- Count attempted L3-A research from the bounded experience state.
- Only use real verification-linked settled research records; recalculate
  \`brier_gain_vs_pre_research\` using frozen \`p_before\` and actual verification
  outcome/Brier. Skip missing, fabricated or inconsistent links.
- Collapse multiple settled attempts relating to the same frozen event to one
  aggregate contribution; show attempts separately.
- Report evidence-discovery rate, settled independent event count and average
  observed research gain. This is **associational attribution**, not causal
  proof that research improved forecasts.
- USD costs and gain-per-dollar are available **only** when every included
  record contains validated \`measured_cost_usd\`; otherwise they remain null.
  Never fabricate token counts or provider costs.

## Guardrails and acceptance

Required false: \`production_writeback\`, \`source_evidence_mutation\`,
\`belief_probability_override\`, \`frozen_forecast_mutation\`,
\`automatic_retirement\`, \`automatic_promotion\`, \`trade_execution\`.

Production calibration workflow must run P1 regression tests, compile the
module, generate a fresh report, reconcile its independent-event denominator
with the canonical P0 Belief calibration report, and publish only aggregate
data. Failure blocks publication. Existing frozen forecasts remain untouched.

Tests: \`tests/test_hypothesis_utility_engine.py\`,
\`tests/test_hypothesis_utility_projection.py\`. Central regression:
8 S&P 500 revisions targeting 2026-10-08 20:00 UTC => 8 frozen forecasts,
8 revision identifiers, **1 independently scored event**.

## Deliberate limitations

- No attribution of causal value to a question, source or evidence cluster.
- No cost-effectiveness claim before measured costs are available.
- No automatic retirement or code changes. P1 only produces auditable reviews
  for downstream governed experimental workflow.
- No implied statistical independence among correlated market outcomes.
- Legacy forecasts with insufficient executable outcome specifications remain
  conservatively separate (no speculative merging).
