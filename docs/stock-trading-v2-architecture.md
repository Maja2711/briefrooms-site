# Stock Trading v2 — production architecture

Status: **PRODUCTION CHAMPION — FULL** for the GPW and US stock product. The production rollout started from CANARY, but the current production state has already promoted to FULL.

The production policy is authoritative:

- `champion_engine = v2`
- `challenger_engine = v1`
- `legacy_candidate_admission_enabled = false`

The former Daily GPW and Daily US paths are no longer separate active stock-trading products. Their code may remain temporarily for research, settlement, historical lineage and migration compatibility, but they do not own production stock admission.

## Objective

Stock Trading v2 optimizes net expectancy with explicit opportunity cost. `CASH` is a first-class alternative. Empty portfolio slots do not by themselves authorize a trade.

The active stock product covers both markets:

```text
STOCK TRADING v2
├── GPW
└── US
```

## Decision path

1. **Dynamic universe**
   - US: audited dynamic equity universe.
   - GPW: audited GPW Main Market universe.
2. **Broad discovery**
   - Wide candidate discovery with explicit degraded-mode handling when a preferred live source is unavailable.
3. **Opportunity Frontier**
   - Multi-horizon momentum, trend, turnover, volume impulse, volatility quality and risk-adjusted momentum.
   - The US frontier also retains a wider relationship pool for read-through research without granting portfolio authority.
4. **Market Relationship / Trigger attention (US research branch)**
   - Central object: `EVENT × ENTITY × PEERS × MARKET REACTION × TIME`.
   - Detects direct-event reaction, peer read-through, cluster momentum, lead-lag watch and price/volume anomaly hypotheses.
   - Maximum six attention slots: normally up to four trigger-selected plus two exploration controls.
   - Prospective trigger observations are immutable and later settled at 1/3/5/20 sessions.
   - This layer has zero production decision authority.
5. **Trigger-directed lazy Deep BELIEF proxy (US research branch)**
   - At most two names from the trigger attention arm may receive targeted authority-weighted deep research.
   - The implementation deliberately uses Stock Trading v2 Deep Evidence as a proxy backend, not a full Belief Core invocation.
   - Exact `trigger_observation_id` lineage joins later research to prospective outcomes; ticker/time heuristic joins are forbidden.
   - Exploration controls cannot enter the Deep BELIEF queue.
   - No trade, policy writeback or promotion authority.
6. **Deep Evidence**
   - US: SEC/primary corporate evidence plus secondary news.
   - GPW: ESPI/EBI/PAP plus secondary news.
7. **Research risk plan**
   - Deterministic SL/TP geometry from available completed-session information.
   - Research output is non-executable until the production bridge revalidates it.
8. **Fixed notional sizing**
   - Every new GPW position uses PLN 5,000 target notional.
   - Every new US position uses USD 5,000 target notional.
   - Paper-trading quantity may be fractional: `quantity = 5000 / entry_price`.
   - This standardizes exposure; SL/TP and risk gates remain independent.
9. **Portfolio Opportunity Engine**
   - Compares new candidates, current positions and cash.
   - Candidate ranking is not a single hard admission cutoff.
   - Production search continues through ranked candidates until capacity is filled or the eligible frontier is exhausted.
10. **Production revalidation**
   - `scripts/stock_trading_v2_production_bridge.py` revalidates opportunity age, live session state, quote freshness and risk geometry.
   - Only a fresh prospective opportunity may become a canonical portfolio admission.
11. **Immutable learning loop**
   - Selected and rejected candidates are frozen prospectively.
   - Counterfactual replay settles later outcomes.
   - Opportunity Regret attributes false positives and false negatives to decision gates.
   - HOLD/EXIT experiences are tracked separately where applicable.

## Production phase

The rollout contract starts at `CANARY`, but runtime state is currently `FULL`. `data/investments/stock_trading_v2_production_state.json` is the phase source of truth; `data/investments/stock_trading_v2_production_config.json` still defines the canary-to-full transition rules.

The production config defines:

- canary maximum: 1 open position per market;
- full maximum: 3 open positions per market;
- automatic canary progression is enabled under the configured healthy-session requirements;
- maximum opportunity age and market-specific execution-quote freshness are enforced (US 2 minutes; GPW paper execution 20 minutes);
- minimum reward/risk and maximum risk percentage are enforced;
- new entries require the regular session.

Runtime truth lives in:

- `data/investments/stock_trading_policy.json` — includes `FIXED_NOTIONAL_V1` sizing authority
- `data/investments/stock_trading_v2_production_config.json`
- `data/investments/stock_trading_v2_production_state.json`

## Main / research-branch runtime authority

Stock Trading v2 intentionally uses two branches with different authority:

- `main` is the sole production/governance authority. It owns canonical portfolio state, production policy, Champion manifests, production promotion and execution/paper-control paths.
- `stock-trading-v2` is a research/evidence runtime branch. It owns dynamic discovery research state, Market Relationship / Trigger observations, targeted Deep BELIEF proxy research, prospective outcomes, regret, Challenger generation and holdout evaluation.
- Scheduled workflow definitions live on the default branch `main`, but research jobs explicitly check out `stock-trading-v2`. This makes the default branch the orchestration authority while preserving research-state isolation.
- After a successful active-market Continuous Discovery run, the workflow explicitly dispatches `stock-trading-v2-production.yml` on `main`. The independent liveness watchdog and the production schedule remain recovery paths; only the production bridge can mutate the canonical portfolio. A separate `workflow_run` trigger is intentionally not used, avoiding duplicate production runs after the same discovery cycle.
- Research workflows may read frozen production snapshots from `main`; they may not directly push production mutations to `main`.
- A formal Challenger PASS is evidence only. Production admission is owned by the main-branch `Stock Trading Component Promotion` workflow, which performs production-owned candidate intake, exact binding, current-Champion checks, health checks and rollback.

## Operational workflows

- `.github/workflows/stock-trading-v2-universe-refresh.yml`
- `.github/workflows/stock-trading-v2-continuous-discovery.yml`
- `.github/workflows/stock-trading-v2-learning-loop.yml`
- `.github/workflows/stock-trading-v2-validation.yml`
- `.github/workflows/stock-trading-v2-production.yml` — production Champion admission and canonical portfolio persistence.
- `.github/workflows/stock-trading-fixed-notional-validation.yml` — PR/main validation of the mandatory 5K sizing contract, portfolio tests and public UI syntax.

Legacy workflows such as `gpw-daily-pick-pl.yml` and US Daily workflows may still run for historical settlement, research compatibility or migration support. They are not separate active products and cannot override the v2 production authority.

### Single production admission authority

While `champion_engine=v2` and `legacy_candidate_admission_enabled=false`, there is exactly one code path allowed to create a new canonical Stock Trading position:

```text
stock_trading_v2_production_bridge.py
  -> prospective execution quote under the market-specific paper policy
  -> portfolio.admit_candidate(..., authority="v2_production_bridge")
  -> canonical portfolio
```

`stock_trading_portfolio.py --mode run` is a lifecycle/review worker only in this regime. Its legacy `sync-candidates` mode is fail-closed and performs no state write. Daily GPW/US candidate workflows publish research/candidate state only and never stage `stock_trading_portfolio.json`. This prevents any parallel legacy writer from bypassing fresh-quote revalidation or racing the canonical v2 writer.

## Production boundary and anti-hindsight invariant

The v2 opportunity frontier is research input, not a historical fill.

The production bridge must observe the opportunity prospectively and then obtain a fresh execution-time quote. The NO RETROACTIVE airlock validates persistence before any state is committed.

Therefore:

```text
research/shadow opportunity
    -> fresh production revalidation
    -> current LIVE decision
    -> canonical portfolio persistence
```

A replay, old candidate or historical research artifact can never be converted into a retroactive LIVE entry.

The same invariant applies to exits. A current SL/TP geometry may only inspect market bars whose timestamp is at or after the moment that geometry became effective:

```text
trigger_window_start >= max(opened_at, risk_last_changed_at)
```

This prevents two classes of false execution: a pre-entry candle closing a position that did not yet exist, and an earlier same-day low/high being reused after a later SL/TP ratchet. If no post-effective intraday bar exists yet, the engine must hold with a data error rather than synthesize an SL/TP fill.

For new v2 admissions the production bridge requires a prospectively observed execution quote under a market-specific paper-execution policy. US keeps the strict maximum age of 2 minutes. GPW explicitly allows a current-session Yahoo/eligible-provider observation up to 20 minutes old and records the fill as `DELAYED_PAPER`. The quote must still belong to the REGULAR session and be observed prospectively after the production run starts; quotes older than the market-specific limit, closed-session observations and research reference prices remain non-executable. This policy is for paper trading only and does not represent broker-quality real-time execution.

## Learning / challenger boundary

Stock Trading v2 is now the engine-level production Champion. V1 is the registered Challenger.

Component-level changes inside v2 still follow governed future-only learning:

```text
Experience
 -> Regret
 -> Challenger
 -> Replay / Holdout
 -> Shadow
 -> Statistical/Governance Gate
 -> Promotion or Reject
```

No learning step may rewrite closed history, fabricate prior execution or silently weaken hard safety invariants.

Detailed bilingual sizing contract: `docs/STOCK_TRADING_FIXED_NOTIONAL_EN.md` and `docs/STOCK_TRADING_FIXED_NOTIONAL_PL.md`.
