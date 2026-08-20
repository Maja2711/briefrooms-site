# PR #19 — Epistemic Contract & Causal Belief Graph v1

## Purpose

PR19 adds a research-shadow epistemic layer around the existing Belief Core. It does **not** change Belief probability, confidence, evidence weight, forecast semantics or any BRACE/WES decision.

The purpose is to stop treating a Belief as only `claim + probability`. Every covered Belief now has an explicit contract:

```text
claim
  ↓
causal assumptions
  ↓
hypothesised transmission path
  ↓
falsifiers
  ↓
alternative explanations
  ↓
unknowns
  ↓
regime dependencies
```

The graph is a **hypothesis graph**, not a graph of established causal facts.

## Philosophical design

The implementation borrows operational ideas from several traditions without turning philosophy into decoration:

- **Popper** — every Belief must expose at least one prospective, machine-testable operational falsifier. An unfalsifiable narrative is not enough.
- **Bayes** — probability update remains exclusively in the existing Belief Core. PR19 never rewrites posterior probability or confidence.
- **Lakatos** — core claim and auxiliary assumptions are stored separately. A broken auxiliary transmission assumption is not silently converted into “the whole Belief was false”.
- **Peirce** — alternative explanations remain explicit abductive hypotheses. PR19 does not automatically pick the most convenient explanation after observing the outcome.
- **Kuhn** — regime dependency is explicit. A relationship can become inapplicable in a changed regime without post-hoc relabelling of old forecasts.

## Canonical claim parity

PR19 does not invent replacement claims. The contract claim and operational outcome rule must match the canonical definitions from:

- PR10 Broad Market Beliefs,
- PR11 Sector / Factor Beliefs,
- PR15 Entity Beliefs.

A mismatch fails closed.

## Measurement gap

PR19 explicitly distinguishes a claim from the operational rule used to verify it.

Example:

```text
Claim:
US rates pressure remains supportive for risk assets.

Operational rule:
TLT is not below the frozen reference.
```

The TLT rule is useful and testable, but it is a **partial proxy**. A correct TLT outcome is not automatically proof that the complete causal risk-asset claim was correct.

This distinction is written into `measurement_relation` and `measurement_limitations`.

Entity beliefs are different: the PR15 forecast target is directly the next comparable PR14 interpretation. Even there, correct fundamentals are explicitly **not** treated as proof of stock-return or BRACE economic value. That still requires PR17/PR18/PR18.1 prospective evidence.

## Causal graph v1

The graph contains two node classes:

```text
Belief nodes
Mechanism nodes
```

Example hypotheses:

```text
market.rates.supportive
        ↓
mechanism.discount_rate_relief
        ↓
factor.growth.leadership
```

```text
market.liquidity.supportive
        ↓
mechanism.financing_and_risk_capacity
        ↓
factor.small_cap.leadership
```

```text
entity.amzn.revenue_durability
        ↓
mechanism.entity.amzn.demand_and_monetization_persistence
```

Every graph edge carries:

```text
causal_status = UNVERIFIED_HYPOTHESIS
causal_proof = false
pnl_tuned = false
decision_influence = false
```

PR19 has no automatic edge discovery. Correlation cannot create a causal edge.

## Prospective anti-hindsight binding

The graph is stored in append-only snapshots.

On the first PR19 run, every already-existing PR15 forecast is cursor-only and receives no epistemic binding.

For a later forecast:

```text
graph_snapshot.created_at <= forecast_at
AND
epistemic contract for belief exists in that snapshot
```

Only then can PR19 create:

```text
forecast_id
  ↓
epistemic_contract_id
  ↓
graph_snapshot_id
```

If a new Entity Belief appears and its graph contract is created only after the forecast was already frozen, that forecast becomes terminally unbound:

```text
no_preexisting_epistemic_contract_snapshot
```

It is never retroactively attached to the newer causal story.

## What PR19 does not do

PR19 does not:

- change Belief probability or confidence,
- change evidence weights,
- rewrite forecasts,
- modify BRACE/WES scores, ranking, sizing or exposure,
- veto or force exits,
- execute trades,
- produce an alpha score,
- produce a MIV score,
- infer causal edges from correlations,
- validate causal claims automatically,
- select a preferred alternative explanation,
- create engine-specific trust,
- define a promotion threshold,
- promote anything automatically.

## Relationship to PR18.1

PR18.1 asks:

> Did the Belief add marginal economic information beyond what BRACE already knew?

PR19 adds a second research question:

> If the Belief added value or failed, through which hypothesised mechanism did we expect the information to travel, which assumptions were required, and what competing explanations remain plausible?

The two layers remain separate. Economic uplift does not prove causality, and causal narrative does not prove economic value.

## Future research, not enabled in PR19

Possible later work includes:

- prospective testing of individual transmission edges,
- edge-specific calibration,
- causal-path attribution joined to MIV,
- structured alternative-hypothesis competition,
- regime-conditioned edge validity,
- engine-specific trust learned from prospective evidence.

Those require separate reviewed contracts and cannot be inferred retrospectively from the v1 graph.
