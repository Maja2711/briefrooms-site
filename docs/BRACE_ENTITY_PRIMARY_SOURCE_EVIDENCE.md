# PR #13 — Entity Primary-Source Evidence Foundation

## Purpose

PR #13 connects the PR12 Company/Entity Framework to authoritative issuer-source data without yet allowing Entity Beliefs to influence BRACE.

The hierarchy becomes:

```text
Broad market Beliefs
        ↓
Sector / factor Beliefs
        ↓
PR12 Company / Entity Framework
        ↓
PR13 Primary-Source Observations
        ↓
future reviewed Belief interpretation
        ↓
future Entity forecast + calibration
        ↓
future BRACE ↔ Entity bridge
        ↓
prospective WITH vs WITHOUT BELIEF
        ↓
Promotion Review
```

PR13 answers one narrow question:

> What authoritative, prospectively available facts did the issuer disclose after the Entity research window was opened?

It does **not** answer whether those facts are bullish or bearish.

---

## Active entity universe

PR13 follows the same entity universe formalized by PR12:

- active Stock in current Portfolio 10K = always-on research,
- Stock in canonical `data/portfolio10k/analysis.json -> candidates` = pre-entry candidate/watchlist research,
- other universe stocks = inactive,
- ETFs are excluded from the Company/Entity layer.

PR13 imports PR12's canonical `desired_entities` contract rather than inventing another candidate threshold.

---

## Primary provider in PR13

The first production primary-source adapter is **SEC EDGAR**.

PR13 uses:

- SEC company ticker → CIK registry,
- `data.sec.gov/submissions/CIK##########.json`,
- `data.sec.gov/api/xbrl/companyfacts/CIK##########.json`,
- SEC filing archive/index headers when an exact acceptance timestamp must be recovered.

No API key is required for the public EDGAR data APIs. Requests carry an identifying User-Agent and are conservatively paced.

### Supported filing families

```text
Domestic / US periodic and event reporting
10-K
10-Q
8-K

Foreign private issuer reporting
20-F
6-K
40-F
```

Amendments such as `10-K/A` or `20-F/A` normalize to the corresponding base form while preserving the exact original form in provenance.

---

## Reporting-regime resolution

PR12 intentionally left:

```text
reporting_regime = unresolved_requires_primary_source_adapter
```

PR13 resolves this from observed SEC filing history:

```text
10-K / 10-Q history
→ domestic_sec_periodic_reporting

20-F / 6-K / 40-F history
→ foreign_private_issuer_sec

both domestic and foreign periodic families
→ mixed_or_transition_requires_review

only 8-K among supported forms
→ sec_registered_event_reporting_only_unresolved
```

PR13 does not guess reporting regime from geography, exchange or ticker suffix.

---

## Anti-hindsight: no historical evidence backfill

This is the most important runtime rule.

When an entity enters PR13 for the first time:

```text
T0 = first PR13 collection-window timestamp
```

Filings accepted before or at `T0` are cursor/reference history only. They are **not** turned into primary observations.

Only a filing accepted after the open collection boundary can create a PR13 observation.

Therefore the first normal PR13 run for an entity is expected to produce zero historical evidence.

### Example

```text
AMZN 10-Q filed Aug 1
PR13 collection window opens Aug 20

Aug 1 filing
→ known to SEC
→ may help resolve reporting regime/cursor
→ NOT Entity Belief evidence

next AMZN filing after Aug 20
→ prospectively eligible
→ filing observation + supported XBRL facts
```

This prevents PR13 from manufacturing an apparently long calibration history after the architecture was introduced.

---

## Dormancy and reactivation

A candidate can leave the BRACE candidate list without entering the portfolio.

When this happens:

```text
entity status = dormant
collection window = closed
```

PR13 does not collect filings during the dormant interval.

If BRACE later selects the entity again:

```text
same entity lineage is preserved
new collection window opens at reactivation
```

Filings published while the entity was dormant are not backfilled on reactivation.

This avoids survivorship/history editing while still preserving earlier prospective observations.

---

## Exact availability timestamp

A filing is not evidence-eligible without a sufficiently precise SEC acceptance timestamp.

PR13 first uses `acceptanceDateTime` when supplied by SEC submissions data. If it is absent for a potentially new filing, PR13 attempts to recover `<ACCEPTANCE-DATETIME>` from the SEC filing index headers.

If a precise timestamp still cannot be resolved:

```text
status = data quality issue
filing = not evidence eligible
```

PR13 does not replace missing time with midnight of `filingDate`.

### Conservative timezone policy

Some EDGAR acceptance timestamps are timezone-less. To avoid making information available too early, PR13 calculates both:

- `America/New_York`, and
- fixed EST (`UTC-05:00`)

interpretations and uses the **later UTC instant**.

This can delay an observation by up to one hour during daylight-saving time, but it is intentionally conservative for anti-lookahead control.

---

## What gets stored

PR13 stores two primary observation families.

### 1. Filing observation

One observation for each new eligible SEC filing:

```text
metric = entity_primary_filing
entity
form
accession number
filing date
report date
primary document
SEC source URL
accepted_at
CIK
ticker
```

All facts from the same filing share one provenance/independence cluster:

```text
issuer-filing:<entity>:<accession>
```

This prevents a later interpretation layer from pretending that many facts from one quarterly filing are many independent information sources.

### 2. Structured XBRL facts

For the exact filing accession, PR13 reads selected fields from SEC Company Facts.

Initial canonical metrics include:

```text
revenue
net_income
diluted_eps
operating_income
operating_cash_flow
capex
cash
assets
liabilities
share_repurchases
dividends_paid
```

and, where standardized tags are available:

```text
deposits
provision_for_credit_losses
net_interest_income
```

Each observation preserves:

```text
taxonomy
original XBRL tag
unit
value
period start/end
fiscal year / fiscal period
frame
exact accession
source URL
accepted_at
candidate Entity Belief dimensions
```

---

## Mapping to Entity dimensions

PR13 may attach candidate dimensions to a raw fact, for example:

```text
revenue
→ revenue_durability

net_income / diluted_eps
→ earnings_momentum

operating_income
→ margin_trajectory

operating_cash_flow
→ earnings_quality

cash / assets / liabilities
→ balance_sheet_strength

capex
→ capex_returns

repurchases / dividends
→ capital_allocation

net_interest_income
→ net_interest_income_durability

deposits
→ deposit_funding

provision_for_credit_losses
→ credit_quality
```

This is routing metadata only.

It does **not** mean:

```text
revenue up = automatically bullish
capex up = automatically bearish
liabilities up = automatically bearish
```

Those interpretations require a later reviewed contract that understands sector, scale, period comparability and prior expectations.

---

## No historical comparison in PR13

PR13 deliberately does not compare a newly observed filing to pre-PR13 historical filings in order to manufacture a trajectory.

Example:

```text
first prospective revenue fact = 100
```

PR13 stores `100`.

It does not retrieve an old 2025 value and immediately create:

```text
revenue_growth = +12%
```

as Belief evidence.

After multiple prospective filings accumulate, a later reviewed layer can compare observations whose full collection provenance is already in the ledger.

---

## Why no LLM interpretation here

PR13 is deterministic and source-preserving.

It does not use an LLM to decide whether a filing is good or bad. This separates two different questions:

```text
PR13:
What did the issuer officially disclose, and when was it available?

future interpretation layer:
What does that disclosure mean for a specific Belief dimension?
```

This makes errors easier to audit and prevents an interpretation model from changing the source record itself.

---

## Unstructured dimensions

Some Entity dimensions cannot be safely represented by a few standardized XBRL numbers, including much of:

```text
competitive_position
regulatory_risk
pipeline_durability
capacity_utilization
product concentration details
company-specific strategic claims
```

PR13's filing observation preserves the authoritative document provenance for later adapters, but this PR does not pretend these dimensions are solved.

---

## Issuer Investor Relations extension point

SEC EDGAR is the first live adapter because it provides stable issuer identity, filing metadata and structured XBRL facts.

PR13 exposes the architecture for additional authoritative issuer sources, but **live issuer-IR ingestion remains disabled** until there is a reviewed registry of authoritative IR domains/feeds for each entity.

PR13 will not guess an Investor Relations URL from a company name or scrape arbitrary search results and label them primary.

Future IR support can include official earnings releases or presentations only after authoritative source identity and publication-time rules are frozen.

---

## Fail-soft source behavior

If SEC ticker/CIK resolution or API retrieval fails:

- the entity remains part of the PR12 active research universe,
- no synthetic filing/fact is created,
- the source issue is recorded,
- BRACE is unaffected,
- the run can continue for other entities.

Missing primary data never becomes neutral/positive evidence by default.

---

## Hard safety boundary

All of the following remain false in PR13:

```text
active decision influence
score change
candidate ranking change
target exposure change
sizing change
veto
direction reversal
forced exit
trade execution
policy output
automatic tuning
bounded influence
historical backfill
LLM interpretation
Belief polarity assignment
Entity forecast capture
Entity promotion
```

PR13 creates observations only.

---

## WITH vs WITHOUT governance remains mandatory

PR13 does not weaken the program-level promotion rule.

A later BRACE ↔ Entity bridge must still generate a prospective paired report:

```text
BRACE WITHOUT ENTITY BELIEF
PnL
max drawdown
Sharpe
hit rate
turnover / costs
tail risk

BRACE WITH ENTITY BELIEF
same metrics

DELTA
ΔPnL
Δdrawdown
ΔSharpe
Δhit rate
Δturnover / costs
```

Promotion review additionally requires effective N, stable uplift, regime robustness, concentration diagnostics, calibration, drift, data quality/provenance and anti-hindsight controls.

No Entity Belief is auto-promoted.

---

## Source references

Authoritative design references:

- SEC EDGAR Application Programming Interfaces: `https://www.sec.gov/search-filings/edgar-application-programming-interfaces`
- SEC Data APIs: `https://data.sec.gov/`
- SEC explanation of EDGAR timestamps: `https://www.sec.gov/about/webmaster-frequently-asked-questions`

The code records exact SEC source references with each observation for auditability.
