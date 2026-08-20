# PR #18 — BRACE Information Set & Disagreement Capture

## Purpose

PR18 is a research-measurement layer between the first prospective BRACE↔Entity Belief experiment (PR17) and future Marginal Information Value research.

PR17 can tell us whether the hypothetical WITH BELIEF arm changed the BRACE outcome. PR18 freezes what BRACE already knew at the decision so future research can distinguish:

- a Belief that is correct but redundant with the engine,
- a Belief that adds genuinely incremental information,
- a Belief whose value appears mainly under specific disagreement patterns or World States.

PR18 does **not** produce an alpha score and does **not** estimate MIV yet.

## Architecture

```text
BRACE analysis + recommendation
        │
        ├── exact source SHA parity
        │
        ▼
BRACE ENGINE INFORMATION SET
        │
        ├── feature scores
        ├── raw feature blocks
        ├── expectations
        ├── data-quality confidence semantics
        ├── decision context
        └── provenance
        │
        ├──────── PR17 Entity Belief information
        │
        └──────── PR16.1 World State
                     │
                     ▼
             DISAGREEMENT TOPOLOGY
                     │
                     ▼
        future PR18.1 MIV diagnostics
```

## Hard anti-hindsight boundary

A PR17 pair is eligible for PR18 capture only if the exact `analysis.json` and `pending_decisions.json` currently available have the same canonical SHA-256 values frozen by PR17 in the pair record.

If either hash differs, PR18 records:

```text
source_snapshot_not_available
terminal_no_reconstruction = true
```

The pair is never filled later from a newer repository state.

First PR18 run is activation-only for any PR17 pair already visible at activation. Existing pairs become cursor-only and are not reconstructed.

## BRACE Engine Information Set v1

For each eligible Entity instrument PR18 freezes:

- engine methodology version,
- analysis/pending timestamps,
- `quality_score`,
- `valuation_score`,
- `momentum_score`,
- `risk_score`,
- `diversification_score`,
- `thesis_score`,
- `data_quality_score`,
- `final_score`,
- `risk_adjusted_score`,
- raw momentum/risk/quality/valuation/liquidity blocks,
- expected return base/bull/bear,
- expected drawdown,
- probability of reaching target,
- current/proposed/target weights,
- positive/negative factors,
- conditions for change,
- material-event context,
- market/FX timestamps and provenance,
- a fingerprint of the static thesis/invalidation text.

`confidence_score` is explicitly recorded as `data_quality_confidence`, not as an outcome probability.

## Entity Belief Information Set v1

PR18 freezes the PR17 forecast rows already admitted prospectively by PR15/PR16.1:

- forecast ID,
- Belief ID,
- dimension,
- predicted probability,
- forecast confidence,
- forecast/target timestamps,
- forecast World State binding,
- aggregate confidence-weighted signed Entity signal,
- primary PR17 modifier,
- WITH/WITHOUT action and whether the decision changed.

PR18 does not alter those values.

## World State context

The exact PR16.1 `decision_world_state_id` frozen by PR17 is reused. PR18 records:

- four Broad-Market supportive Beliefs,
- mapped Sector leadership Belief where an explicit reviewed sector mapping exists,
- factor leadership context,
- source cutoff and context timestamp.

The World State must predate the BRACE decision.

## Disagreement topology v1

PR18 classifies descriptive stances:

```text
ENGINE:  POSITIVE / NEUTRAL / NEGATIVE / UNAVAILABLE
ENTITY:  POSITIVE / NEUTRAL / NEGATIVE / UNAVAILABLE
MARKET:  POSITIVE / NEUTRAL / NEGATIVE / UNAVAILABLE
SECTOR:  POSITIVE / NEUTRAL / NEGATIVE / UNAVAILABLE
FACTOR:  POSITIVE / NEUTRAL / NEGATIVE / UNAVAILABLE
```

A fixed signed-support dead-band of `0.05` is used only to avoid treating probabilities infinitesimally above/below 0.50 as a different qualitative state. It is declared ex ante, not PnL tuned, and is not a promotion threshold.

The capture records relations such as:

```text
ENGINE_ENTITY_CONFLICT
ENTITY_SECTOR_CONFLICT
ENTITY_MARKET_CONFLICT
TOP_DOWN_SUPPORTIVE
TOP_DOWN_ADVERSE
TOP_DOWN_MIXED
```

and a deterministic `pattern_code`.

This is descriptive telemetry, not an alpha model.

## Marginal Information Value boundary

PR18 intentionally reports:

```text
status = MEASUREMENT_INPUTS_ONLY
miv_score = null
redundancy_status = NOT_YET_ESTIMABLE
orthogonality_status = NOT_YET_ESTIMABLE
```

When a PR17 pair matures, PR18 may join the already-frozen capture to the PR17 economic delta for descriptive analysis. It still does not calculate a single MIV score.

A later reviewed PR18.1 may estimate incremental value, redundancy and orthogonality from prospective samples.

## Zero-authority controls

PR18 cannot:

- change BRACE scores,
- change Belief probabilities,
- rerank candidates,
- rerun the optimizer,
- change exposure or sizing,
- veto or force exits,
- execute trades,
- tune policy,
- promote a Belief or bridge,
- produce a live MIV alpha score.

Promotion status remains:

```text
NOT_ELIGIBLE_FOR_PROMOTION_REVIEW
```

No effective-N promotion threshold is defined in PR18.

## State and report

Private cumulative artifact:

```text
brace-information-disagreement-capture-state
```

Files:

```text
BRACE_INFORMATION_DISAGREEMENT_STATE.json
BRACE_INFORMATION_DISAGREEMENT_REPORT.json
```

The capture and terminal-uncaptured ledgers are append-only.
