# Stock Trading v2 — shadow architecture

Status: **shadow discovery/learning with bounded automatic production promotion**. Production remains unchanged until an exact executable Challenger passes its pre-registered fresh prospective holdout; only then may an allowlisted deterministic config patch be materialized automatically.

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
   - A wider relationship pool is retained for relationship/read-through research without granting portfolio authority.
4. **Market Relationship / Trigger attention layer (US shadow)**
   - Central object: EVENT × ENTITY × PEERS × MARKET REACTION × TIME.
   - Detects direct event reaction, peer read-through, cluster momentum, lead-lag watch and price/volume anomaly hypotheses.
   - Scarce attention budget: maximum 6 names, normally up to 4 trigger-selected + 2 exploration controls.
   - Prospective trigger observations are frozen append-only and settled at 1/3/5/20 sessions.
   - Trigger quality must beat the exploration control arm before it can become eligible for a separate weight Challenger.
   - Intraday trigger outcomes use the price frozen at observation time; the later-known session close cannot replace it.
5. **Trigger-directed lazy deep research (US shadow)**
   - The trigger may nominate at most 2 names for actual authority-weighted deep research.
   - V1 uses Stock Trading v2 Deep Evidence as a research backend and explicitly labels itself a Deep BELIEF proxy, not full Belief Core.
   - Results are immutable per trigger snapshot and measure information yield per research unit.
   - v2 research records carry the exact deterministic `trigger_observation_id`, so later outcome learning joins 1:1 and never by ticker/time heuristics.
   - The broad top-10 Deep Evidence arm remains Champion, so theoretical slot reduction is not reported as realized production compute savings.
   - No production decision influence, trade execution, automatic policy writeback or promotion authority.
6. **Deep Evidence**
   - US primary evidence: SEC EDGAR filings; secondary evidence: news discovery.
   - GPW primary evidence: ESPI/EBI/PAP; secondary evidence: independent news.
   - Primary evidence is not an automatic trade signal. Directional overlays are bounded and provider degradation is explicit.
7. **Research risk plan**
   - Deterministic SL/TP geometry from completed-session price/ATR information.
   - A research plan is never execution-ready. A future production path must revalidate price, freshness and intraday risk before entry.
8. **Portfolio Opportunity Engine**
   - Compares new candidates, current positions and cash.
   - `BUY`: candidate clears the configured cash edge and a slot is free.
   - `REPLACE`: book is full and candidate clears both the cash edge and replacement hysteresis over the weakest current position.
   - Otherwise `HOLD` or `CASH`.
   - Maximum one research action per market cycle.
9. **Immutable learning loop**
   - Selected and rejected candidates are frozen prospectively into the same Experience Store.
   - Continuous intraday observations are deduplicated by market/symbol/day/selection state to avoid overweighting repeated scans.
   - Canonical Champion admission is frozen before outcomes are known.
   - Counterfactual replay settles 1/2/5/10/20/60/120-session outcomes.
   - Opportunity Regret attributes false positives and false negatives to decision gates.
   - HOLD/EXIT decisions have a separate immutable position-experience ledger.
   - Trigger-directed deep research is joined to 1/3/5/20-session Trigger outcomes by exact observation ID; the report measures evidence yield, score update and directional-update/outcome agreement without claiming causality.
10. **Policy learning and promotion**
   - Learning creates future-only Challenger policies and exact Factory deployment artifacts.
   - Promotion requires Replay Tournament robustness plus a separate fixed-N fresh prospective holdout.
   - An exact formal PASS may be promoted automatically only through `stock_trading_v2_auto_promote.py`.
   - Auto-promotion is limited to allowlisted deterministic `config_patch` operations, must match the current Champion manifest revision/component version and the exact replay baseline, and writes an immutable production promotion record.
   - Arbitrary source-code generation or self-modifying production code is not permitted by this path.

## Operational workflows

- `.github/workflows/stock-trading-v2-universe-refresh.yml` — audited dynamic universe refresh.
- `.github/workflows/stock-trading-v2-continuous-discovery.yml` — continuous session-time discovery → relationship/trigger attention → max-two targeted deep-research shadow arm → broad Champion evidence → portfolio comparison → experience/admission freeze.
- `.github/workflows/stock-trading-v2-learning-loop.yml` — post-session outcome settlement, exact Trigger deep-research/outcome joining, regret, Challenger learning/evaluation, evidence commit, then bounded automatic production promotion for exact formal-PASS Challengers.
- `.github/workflows/stock-trading-v2-validation.yml` — compile/config/unit/integrity/live-smoke validation.

## Production boundary

The v2 branch reads the current canonical portfolio and policy from `main` without merging `main`. Discovery, Trigger routing and research remain non-production until their own deployment adapter exists. For components with an exact deployment adapter, a Challenger can cross the production boundary only after a formal fresh-holdout PASS. The promotion engine re-reads the latest `main`, requires the Challenger's base manifest revision/component version and replay baseline to still match, applies only allowlisted JSON replacements, increments the Champion manifest revision, records Challenger/evaluation/evidence hashes, validates cross-file invariants, and then pushes the deterministic production change to `main`. Any mismatch fails closed.
