# Stock Trading v2 — shadow architecture

Status: **implementation complete in shadow mode**. Production promotion is intentionally disabled until the separate statistical Champion–Challenger gate has sufficient prospective holdout evidence.

## Objective

Stock Trading v2 optimizes net expectancy with explicit opportunity cost. Empty portfolio slots are not a reason to trade. `CASH` is a first-class alternative, and the system may select `BUY`, `HOLD`, `CASH` or `REPLACE` only after comparing the best currently observed opportunities with the current portfolio.

## Decision path

1. **Dynamic universe**
   - US: official Nasdaq Trader symbol directories; thousands of eligible common/ADR equity instruments rather than a fixed hand-maintained list.
   - GPW: official GPW Main Market company search.
2. **Stage zero / broad discovery**
   - Preferred US path: Nasdaq live screener joined to the audited dynamic universe, preserving liquidity, mover and mid-cap lanes.
   - If the live Nasdaq screener is unavailable, the engine uses an explicitly degraded rotating shard of the audited universe. It makes no live-liquidity claim. Historical discovery downstream must still prove turnover, momentum and volatility.
3. **Opportunity Frontier**
   - Multi-horizon momentum, trend, turnover, volume impulse, volatility quality and risk-adjusted momentum.
   - No global selection cutoff. The frontier is continuously refreshed during each market's configured session window.
4. **Deep Evidence**
   - US primary evidence: SEC EDGAR filings; secondary evidence: news discovery.
   - GPW primary evidence: ESPI/EBI/PAP; secondary evidence: independent news.
   - Primary evidence is not an automatic trade signal. Directional overlays are bounded and provider degradation is explicit.
5. **Research risk plan**
   - Deterministic SL/TP geometry from completed-session price/ATR information.
   - A research plan is never execution-ready. A future production path must revalidate price, freshness and intraday risk before entry.
6. **Portfolio Opportunity Engine**
   - Compares new candidates, current positions and cash.
   - `BUY`: candidate clears the configured cash edge and a slot is free.
   - `REPLACE`: book is full and candidate clears both the cash edge and replacement hysteresis over the weakest current position.
   - Otherwise `HOLD` or `CASH`.
   - Maximum one research action per market cycle.
7. **Immutable learning loop**
   - Selected and rejected candidates are frozen prospectively into the same Experience Store.
   - Continuous intraday observations are deduplicated by market/symbol/day/selection state to avoid overweighting repeated scans.
   - Canonical Champion admission is frozen before outcomes are known.
   - Counterfactual replay settles 1/2/5/10/20/60/120-session outcomes.
   - Opportunity Regret attributes false positives and false negatives to decision gates.
   - HOLD/EXIT decisions have a separate immutable position-experience ledger.
8. **Policy learning and promotion**
   - Learning may create future-only Challenger policies; it cannot rewrite production automatically.
   - Promotion requires the separate fixed-N statistical holdout gate.
   - `production_promotion_enabled=false` remains an intentional safety invariant.

## Operational workflows

- `.github/workflows/stock-trading-v2-universe-refresh.yml` — audited dynamic universe refresh.
- `.github/workflows/stock-trading-v2-continuous-discovery.yml` — continuous session-time discovery → evidence → portfolio comparison → experience/admission freeze.
- `.github/workflows/stock-trading-v2-learning-loop.yml` — post-session outcome settlement, regret, Challenger learning and evaluation.
- `.github/workflows/stock-trading-v2-validation.yml` — compile/config/unit/integrity/live-smoke validation.

## Production boundary

The v2 branch reads the current canonical portfolio and policy from `main` for shadow comparisons, but it does not merge `main`, mutate the production portfolio, or activate a Challenger. The production boundary remains intact until prospective evidence satisfies the explicit promotion gate.
