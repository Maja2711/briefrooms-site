# Stock Trading v2 — production architecture

Status: **PRODUCTION CHAMPION — CANARY** for the GPW and US stock product.

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
4. **Deep Evidence**
   - US: SEC/primary corporate evidence plus secondary news.
   - GPW: ESPI/EBI/PAP plus secondary news.
5. **Research risk plan**
   - Deterministic SL/TP geometry from available completed-session information.
   - Research output is non-executable until the production bridge revalidates it.
6. **Fixed notional sizing**
   - Every new GPW position uses PLN 5,000 target notional.
   - Every new US position uses USD 5,000 target notional.
   - Paper-trading quantity may be fractional: `quantity = 5000 / entry_price`.
   - This standardizes exposure; SL/TP and risk gates remain independent.
7. **Portfolio Opportunity Engine**
   - Compares new candidates, current positions and cash.
   - Candidate ranking is not a single hard admission cutoff.
   - Production search continues through ranked candidates until capacity is filled or the eligible frontier is exhausted.
8. **Production revalidation**
   - `scripts/stock_trading_v2_production_bridge.py` revalidates opportunity age, live session state, quote freshness and risk geometry.
   - Only a fresh prospective opportunity may become a canonical portfolio admission.
9. **Immutable learning loop**
   - Selected and rejected candidates are frozen prospectively.
   - Counterfactual replay settles later outcomes.
   - Opportunity Regret attributes false positives and false negatives to decision gates.
   - HOLD/EXIT experiences are tracked separately where applicable.

## Production phase

Current configured phase starts at `CANARY`.

The production config currently defines:

- canary maximum: 1 open position per market;
- full maximum: 3 open positions per market;
- automatic canary progression is enabled under the configured healthy-session requirements;
- maximum opportunity age and execution-quote freshness are enforced;
- minimum reward/risk and maximum risk percentage are enforced;
- new entries require the regular session.

Runtime truth lives in:

- `data/investments/stock_trading_policy.json` — includes `FIXED_NOTIONAL_V1` sizing authority
- `data/investments/stock_trading_v2_production_config.json`
- `data/investments/stock_trading_v2_production_state.json`

## Operational workflows

- `.github/workflows/stock-trading-v2-universe-refresh.yml`
- `.github/workflows/stock-trading-v2-continuous-discovery.yml`
- `.github/workflows/stock-trading-v2-learning-loop.yml`
- `.github/workflows/stock-trading-v2-validation.yml`
- `.github/workflows/stock-trading-v2-production.yml` — production Champion admission and canonical portfolio persistence.
- `.github/workflows/stock-trading-fixed-notional-validation.yml` — PR/main validation of the mandatory 5K sizing contract, portfolio tests and public UI syntax.

Legacy workflows such as `gpw-daily-pick-pl.yml` and US Daily workflows may still run for historical settlement, research compatibility or migration support. They are not separate active products and cannot override the v2 production authority.

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
