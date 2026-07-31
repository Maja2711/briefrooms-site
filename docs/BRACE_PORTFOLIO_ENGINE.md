# BRACE Portfolio Engine

## Production state

The first deployment state is deliberately:

`ACTIVE_BASELINE + BRACE_SHADOW`

The existing Portfolio 10K methodology remains the champion, benchmark and
automatic fallback. Its entry prices, closed history, staged entries and prior
decisions are immutable. BRACE has no real-broker credentials or order path.

## Control lifecycle

The deterministic controller owns methodology status changes:

`CANDIDATE -> TESTING -> SHADOW -> PROBATIONARY_CONTROL -> ACTIVE_PAPER_CONTROL`

An LLM cannot promote a methodology. Every transition writes an append-only
record containing the previous and new state, condition results, validation
window, code commit, data manifest hash and reason.

Promotion from SHADOW requires all historical, risk, operational and shadow
gates. It includes at least 60 calendar days, 20 decisions and 8 completed
shadow trades. Quiet markets extend the shadow period; the engine never creates
trades merely to satisfy a counter.

Probation lasts at least 30 calendar days. It permits no more than one rotation
per day, two position changes per week, 15% weekly turnover and a 10% new
position. Confidence must be at least 70%.

## Fallback

During probation or active paper control, the controller automatically returns
control to the baseline after stale or inconsistent data, repeated workflow
failures, history-integrity failure, missing rationale or risk data, excessive
drawdown or turnover, unexplained parameter changes, or material divergence
from validation.

Fallback preserves every BRACE decision and paper transaction. It stops new
BRACE decisions, restores the baseline as champion and leaves BRACE available
for a later full revalidation.

## Runtime separation

- `brace-portfolio-monitor.yml`: hourly market-day safety monitoring and
  eligible paper execution. It does not train or optimize.
- `brace-portfolio-daily.yml`: daily observations, feature updates and
  confidence calibration.
- `portfolio-10k-brace.yml`: weekly decisions and walk-forward research, plus
  monthly deep robustness runs and manual dispatch.

All three use the same `portfolio-10k-automation` concurrency lock. They test
before publishing, avoid empty commits, retry rebased pushes and upload
diagnostics after failure.

## Data map

- `data/investments/portfolio_10k.json`: preserved production baseline.
- `data/portfolio10k/config.json`: immutable risk and autonomy policy.
- `data/portfolio10k/universe.json`: approved cash-stock and UCITS ETF universe.
- `data/portfolio10k/methodology_registry.json`: champion, challenger, baseline
  and status registry.
- `data/portfolio10k/analysis.json`: current holdings and candidate analysis.
- `data/portfolio10k/pending_decisions.json`: governed recommendations.
- `data/portfolio10k/shadow_log.json`: append-only baseline versus BRACE record.
- `data/portfolio10k/research_validation.json`: walk-forward, regime, bootstrap
  and sensitivity results.
- `data/portfolio10k/promotion_history.json`: append-only production status
  changes.
- `data/portfolio10k/paper_portfolio.json`: created only for model control.
- `data/portfolio10k/public/brace_engine_public.json`: sanitized website state.

The large raw market cache stays outside version control. Reproducibility is
provided by the research manifest, parameter version, data hash and code hash.

## Analysis and execution

Holdings and candidates use the same score: quality, valuation, momentum, risk,
diversification, thesis evidence and data quality. Missing fundamentals reduce
confidence and cannot silently become a bullish signal. Candidate filtering
requires approved XTB availability, sufficient history, current data and
non-duplicated exposure.

The constrained optimizer prohibits short sales, leverage and CFDs, respects
instrument, sector, currency and region limits, and penalizes turnover.
Rotations require minimum holding time, cooldown, score improvement, expected
alpha, confidence, transaction-cost buffer and a portfolio-risk check.
`NO_ACTION` is a valid decision.

After a valid promotion, paper orders use a fresh completed five-minute candle
observed after the signal. Signal and execution timestamps are separate.
Transaction costs, FX and slippage are recorded. Closed markets cause waiting,
stale signals expire, and anti-oscillation blocks rapid sell-and-buy reversals.

## Ten-percent target

`target_annual_return = 0.10` is an analytical long-term objective, not a
guarantee or trade quota. Risk limits always take precedence. The public result
is one of:

- `TARGET_CURRENTLY_JUSTIFIED_WITHIN_MODEL`
- `TARGET_NOT_CURRENTLY_JUSTIFIED`
- `TARGET_REQUIRES_EXCESSIVE_RISK`

## Verification

Run:

```text
python -m pytest -q tests/test_brace_portfolio_engine.py
python scripts/brace_portfolio_engine.py --mode monitor
python scripts/brace_portfolio_simulate_governance.py
node --check scripts/portfolio-10k-control-public.js
```

The governance simulation is in-memory and never changes the production
registry or baseline portfolio.
