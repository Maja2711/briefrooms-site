# Stock Trading – automatic fundamental gate promotion (US only)

## What runs automatically

Research starts **after 2026-11-19T00:00:00Z**. The US Stock Trading v2
discovery flow already collects SEC/XBRL fundamentals without LLM tokens.
This integration freezes **at most one** immutable, point-in-time virtual
free-slot decision per US session, before future prices are available.

The only whitelisted future production behavior is
`US_QUALITY_GATE_55_V1` in `scripts/stock_trading_fundamental_policy.py`:
remove US candidates with missing/non-verifiable fundamental evidence or an
annual fundamental quality score below 55. It cannot add a trade, raise a
score, bypass the existing cash edge, increase position limits, change
SL/TP, remove risk rules, or alter GPW/BTC/S&P/EURUSD.

`stock_trading_v2_fundamental_trial.py` freezes the exact observed
eligible candidate ordering, the original Opportunity/Evidence source hashes,
the SHA-256 of the algorithm and immutable experience-event references. The
freeze is append-only; the same day cannot be retrospectively replaced.
No candidate is scored using a SEC report filed later than the decision.
The replay engine uses the frozen next-session open and conservative
stop-before-target convention with US round-trip cost stress.

The main-owned `stock_trading_fundamental_governor.py` independently reads
**the raw frozen trial + immutable source events + settled outcomes** from
the research branch. It rejects any mismatch of the production and research
policy bytes, the agreed protocol, original opportunity hash, source
fundamental hash, underlying event or outcome hash. Research cannot submit
arbitrary code or post hoc promotion patches.

## Required promotion gate (non-negotiable)

- Exactly the **first 30** prospectively frozen US session episodes;
  no cherry-picking later winners after a missing/bad earliest observation.
- Every episode must have settled, source-verified 1-session **and**
  2-session results. Incomplete evidence waits; unplayable data fails.
- At least 10 decisions differ between Champion and filtered Challenger;
  at least five unique symbols and 25 calendar days in the cohort.
- For each horizon, the difference of stressed net returns must average
  at least **+0.10 percentage points**, at least 55% of changed comparisons
  must improve, and no single positive episode may account for over half
  the positive advantage.
- One-sided bootstrap lower bound at 2.5th percentile must be >0 for
  both horizons, with fixed seed and 4,000 bootstrap replicates.
- A single formal look per trial. Failure does **not** trigger automatic
  threshold weakening or retesting the same trial on a more favorable slice.

Once the exact gate passes, the main-only governor changes
`data/investments/stock_trading_fundamental_rollout.json` to ACTIVE
automatically and writes a durable audit event. No person needs to approve
this preapproved narrow behavior. It **cannot be guaranteed** that the
statistical test will pass.

The independent scheduled workflow is
`.github/workflows/stock-trading-fundamental-autopromotion.yml`, also
triggered by successful v2 Closed Learning Loop runs.

## Immediate rollback / monitoring

Before **each** US production-admission cycle when ACTIVE, the production
bridge recomputes the Opportunity and Evidence integrity hashes and checks
the exact source- and candidate-level fundamental data binding. Any
missing/corrupt/unbound evidence, clock violation or on-switch kill switch:
(1) stops US admissions in that same cycle, (2) persists ROLLED_BACK on main,
(3) appends an audit reason, (4) leaves existing positions and GPW unchanged.
Later cycles run the original approved baseline (fundamental gate inactive).

The automatic promotion workflow also monitors the first ten **new**
prospectively frozen sessions after activation. A mean incremental net return
below -0.25 percentage points on either horizon, or a proof integrity
failure, causes automatic rollback. A rolled-back trial cannot re-promote
itself; another approved variant requires a new prospective test family.

The usual production trading workflow remains responsible for fresh
execution-quote checks, liquidity, risk, cash opportunity edge, max position
count, no-retroactive-execution verification and canonical position state.

## Scope / limitations

This is **a filter-only US research-to-paper-production upgrade**, not a
guarantee of future profitability. Initial backtest semantics use prospective
**virtual free-slot** next-session-open replay, not identical portfolio-level
live execution. There are no guarantees of 30 valid observations within a
fixed number of days. A strict guard is preferable to forcing a deployment
on inconclusive evidence. GPW structured fundamental coverage needs a
separate official Polish ESEF/XBRL ingestion and predeclared promotion family.
