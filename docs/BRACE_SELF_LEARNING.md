# BRACE Portfolio Engine — governed self-learning

## Purpose

BRACE now has a weekly self-learning loop for its challenger methodology. The loop improves only the shadow version of BRACE. It does not alter the production Portfolio 10K baseline, place real orders, enable leverage, or bypass the existing promotion controller.

## Weekly loop

1. Build the normal BRACE shadow analysis and recommendations.
2. Run point-in-time walk-forward research with costs and FX included.
3. Evaluate mature shadow decisions after 7, 30 and 90 calendar days.
4. Compare each actionable call with the FWIA benchmark over the same period.
5. Store immutable outcome events in `data/portfolio10k/learning_state.json`.
6. Propose a bounded challenger change to decision gates.
7. Require the same proposed change in two consecutive weekly reviews.
8. Apply the change only to future BRACE shadow decisions and only after research safety gates pass.
9. Keep the normal methodology promotion gates in force.

## Learnable parameters

The first governed version may change only:

- `minimum_confidence`, bounded to 0.60–0.75;
- `minimum_score_improvement`, bounded to 6.5–10.5 points;
- `minimum_expected_alpha`, bounded to 1.75%–4.00%.

The maximum change is one small predefined step per weekly review. BRACE cannot learn its way around position limits, drawdown limits, transaction costs, the no-leverage rule, the no-short rule, broker restrictions, or the production fallback.

## Sample gate

No learned parameter is activated before at least 12 effective mature actionable outcomes exist. HOLD and WATCH observations may be recorded for audit, but they do not train the actionable decision gates. Seven-day outcomes have lower weight than 30- and 90-day outcomes.

## Activation and rollback

A candidate is activated only when:

- the minimum sample gate passes;
- the same candidate is confirmed in two consecutive weekly runs;
- no-look-ahead, cost, FX, reproducibility and minimum-observation checks pass;
- all values remain inside the hard-coded whitelist and bounds.

The adaptive policy is read by `brace_portfolio_config.py`. Invalid, unknown, out-of-range or base-config-incompatible overrides fail closed. Real-broker execution remains explicitly prohibited.

## Files

- `scripts/brace_portfolio_self_learning.py` — outcome collection, statistics, candidate generation and versioning;
- `data/portfolio10k/adaptive_policy.json` — active and pending bounded overrides;
- `data/portfolio10k/learning_state.json` — append-only outcome memory created by the first run;
- `data/portfolio10k/methodology_registry.json` — immutable challenger manifests;
- `.github/workflows/portfolio-10k-brace.yml` — weekly orchestration.

## Current state

The loop starts in `WARMUP`. That is intentional: the engine must accumulate mature observations before any learned parameter can affect a future shadow decision.
