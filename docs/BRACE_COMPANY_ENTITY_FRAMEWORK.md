# PR #12 — BRACE Company / Entity Belief Framework Foundation

## Purpose

PR #12 establishes the governed activation and definition framework for company/entity Beliefs.

The canonical hierarchy is now:

```text
Broad market
    ↓
Sector / factor
    ↓
Company / entity framework   ← PR #12
    ↓
Entity evidence + calibration
    ↓
BRACE ↔ Entity bridge
    ↓
WITH vs WITHOUT BELIEF
    ↓
Promotion Review
```

PR #12 is **research-shadow only**. It does not ingest issuer evidence, freeze company forecasts, change BRACE scores or rankings, alter exposure, size positions, veto candidates, reverse direction, force exits, execute trades or promote Belief.

## Formal activation rule

The governing rule is:

> **Current portfolio = always-on entity research. New companies activate at BRACE candidate/watchlist stage, not at portfolio-entry stage.**

This is encoded directly in `scripts/brace_company_entity_framework.py`.

### Tier A — current Portfolio 10K stocks

Every active `Stock` position in `data/investments/portfolio_10k_usd.json` is always active in the Entity Framework.

ETFs are excluded from the company/entity layer. Broad, sector and factor ETFs belong to the upstream market/sector/factor layers.

### Tier B — BRACE candidate/watchlist stocks

A company activates before portfolio entry when it appears in the canonical:

```text
data/portfolio10k/analysis.json -> candidates
```

That candidate list is already produced by `brace_portfolio_candidates.rank_candidates` after BRACE's availability, confidence, minimum-observation and data-freshness filters.

PR #12 deliberately adds **no second hidden relevance threshold**. If BRACE has already admitted a Stock to the canonical candidate list, the company becomes eligible for prospective Entity Belief research.

### Tier C — remaining universe

A Stock that is neither:

- an active current Portfolio 10K position, nor
- a current canonical BRACE candidate

is not activated.

The framework therefore avoids maintaining thousands of unnecessary company models while still preventing a cold start at portfolio entry.

## Candidate disappears: dormant, not deleted

If a previously activated candidate disappears from the current candidate list and is not in the portfolio, the entity becomes:

```text
current_status = dormant
```

Its activation history is preserved. If BRACE later selects the company again, research resumes on the same lineage rather than creating a new historical identity.

This prevents deletion/recreation from becoming a form of hidden backfill or survivorship editing.

## Anti-hindsight boundary

PR #12 never reconstructs pre-PR12 candidate history.

On the first production run:

- current holdings receive a real PR #12 activation timestamp,
- current candidates receive a real PR #12 activation timestamp,
- no claim is made that either was prospectively active before that timestamp,
- no old candidate list is reconstructed to manufacture a longer history.

For a future company first seen as a candidate, `first_activated_at` is preserved when that company later enters the portfolio. This provides auditable evidence that Entity research started before portfolio entry.

## Canonical belief dimensions

PR #12 defines a common company core:

```text
entity.<ticker>.earnings_momentum
entity.<ticker>.revenue_durability
entity.<ticker>.margin_trajectory
entity.<ticker>.earnings_quality
entity.<ticker>.valuation
entity.<ticker>.balance_sheet_strength
entity.<ticker>.competitive_position
entity.<ticker>.capital_allocation
entity.<ticker>.capex_returns
entity.<ticker>.regulatory_risk
```

This is a framework vocabulary, not ten active forecasts per company.

Every materialized definition has:

```text
evidence_adapter_status = not_enabled_in_pr12
outcome_contract_status = required_before_forecast_capture
forecast_capture_enabled = false
engine_influence_enabled = false
```

PR #12 therefore cannot create meaningless 50/50 forecasts merely because a definition exists.

## Common core + sector modules

The framework supports additional sector-specific dimensions without pretending every business model is identical.

### Financials

```text
net_interest_income_durability
credit_quality
deposit_funding
capital_strength
```

### Health Care

```text
pipeline_durability
product_concentration
```

### Information Technology

```text
cycle_position
capacity_utilization
```

Company-specific extensions are deferred to later reviewed work. The framework does not yet hard-code bespoke beliefs for JPM, AMZN, LLY, TSM or any other company.

## Reporting regime

PR #12 intentionally does **not** infer filing regimes from ticker, exchange or geography.

Every entity definition starts with:

```text
reporting_regime = unresolved_requires_primary_source_adapter
```

A later evidence adapter must resolve the appropriate issuer-reporting contract using authoritative primary sources before forecast capture. This is required because the universe can contain US domestic issuers, foreign private issuers and non-US reporting regimes; assuming every company uses 10-K/10-Q would be wrong.

## Source lineage

The framework records hashes for the exact:

- Portfolio 10K snapshot,
- BRACE analysis snapshot,
- BRACE universe snapshot.

The private runtime state records:

- `first_activated_at`,
- `first_activation_source`,
- whether the entity has ever been a current holding,
- whether it has ever been a BRACE candidate,
- activation events,
- current status and source,
- candidate rank when available.

## WITH vs WITHOUT governance

Company/Entity Belief cannot be promoted merely because its calibration looks good.

A later BRACE ↔ Entity bridge must produce a prospective paired comparison using the same engine state and decision opportunity:

```text
BRACE WITHOUT ENTITY BELIEF
PnL
Max DD
Sharpe
Hit rate
Turnover / costs

BRACE WITH ENTITY BELIEF
PnL
Max DD
Sharpe
Hit rate
Turnover / costs

DELTA
ΔPnL
ΔDD
ΔSharpe
ΔHit rate
ΔTurnover / costs
```

Promotion Review also requires sufficient effective N, stable uplift, multi-regime robustness, concentration checks, no material drawdown or tail-risk deterioration, good Belief calibration, drift checks, healthy data quality/provenance and anti-hindsight compliance.

No output from PR #12 itself can authorize promotion.

## Hard safety contract

All remain false:

```text
active_decision_influence
score_change
candidate_ranking_change
target_exposure_change
sizing_change
veto
direction_reversal
forced_exit
trade_execution
policy_output
automatic_tuning
bounded_influence
historical_backfill
entity_evidence_ingestion
entity_forecast_capture
entity_promotion
```

## Runtime artifact

The production workflow stores private cumulative state as:

```text
brace-company-entity-framework-state
```

The state is not committed to the public repository.

Canonical report inside the artifact:

```text
BRACE_COMPANY_ENTITY_FRAMEWORK_REPORT.json
```

Canonical activation ledger:

```text
ENTITY_ACTIVATION_STATE.json
```

## What PR #12 does not do

PR #12 deliberately stops before issuer evidence.

The next reviewed entity stage should add primary-source issuer adapters and explicit outcome contracts. Only after those exist should entity forecasts and calibration begin.
