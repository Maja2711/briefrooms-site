# BriefRooms Belief Core v1 / Belief Lab

## Status

**SHADOW MODE ONLY.** Belief Core v1 does not call BRACE, WES, BRACE-SPX, GPW Daily, position sizing, execution, or any BUY/HOLD/SELL policy. Its output is a read-only epistemic state consumed by the internal Belief Lab dashboard.

## Architecture

```text
OBSERVATIONS
    -> EVIDENCE STORE (source / time / provenance / reliability)
    -> INDEPENDENCE-CLUSTER DEDUPLICATION
    -> BELIEF ENGINE
         probability
         evidence confidence
         alternative hypotheses
         support / opposition
         freshness / decay
    -> BELIEF AUDITOR
    -> WORLD STATE SNAPSHOT
    -> BELIEF LAB (read only)

VERIFICATION -> calibration records -> BELIEF CORE
```

There is deliberately no Policy/Action component in v1.

## Probability is not confidence

**Belief probability** describes how likely a claim is under the current prior and active evidence.

**Evidence confidence** describes the quality of the evidence basis. v1 derives it transparently from source reliability, freshness, independent evidence clusters, source diversity and a contradiction penalty.

A belief can therefore have high probability and low confidence.

## Provenance and de-duplication

Every `Evidence` item has an `independence_cluster`. A filing and ten stories derived from that filing remain visible in provenance, but if they share one cluster they contribute only one statistical evidence unit. The representative is selected deterministically, preferring a primary source. `derived_from` and `source_ref` preserve lineage.

## Freshness

```text
freshness = 0.5 ** (age_hours / half_life_hours)
```

Half-life belongs to the belief definition so event, technical, macro and fundamental beliefs can decay at different speeds.

## Probability update

The engine recomputes from a fixed prior plus the currently stored evidence every run. It does not accumulate the previous posterior again, preventing repeated shadow jobs from double-counting old evidence. The v1 update is transparent log-odds aggregation bounded to [0, 1].

## Alternative hypotheses

Beliefs sharing `alternative_group` are treated as a mutually exclusive, collectively exhaustive set and normalized to sum to 1. Use such groups only when that assumption is justified.

## Belief Auditor

Checks include duplicate provenance clusters, thin evidence, stale evidence, material contradiction, missing primary source, weak provenance, and high probability paired with low confidence.

## Verification and calibration

`verify()` records a later binary outcome and its Brier-score component. v1 records calibration but does not automatically tune policy or source reliability from a small sample.

## Persistent outputs

Default directory: `data/belief_core/`

- `state.json` — definitions, evidence, beliefs, history, verifications.
- `ledger.jsonl` — append-only belief transitions and verification events.
- `dashboard.json` — read-only snapshot consumed by Belief Lab.

JSON state writes are atomic. Runtime `state.json` and `ledger.jsonl` are ignored by git; the safe empty dashboard contract is tracked.

## Runner

```bash
python scripts/belief_core_shadow.py --input path/to/evidence-batch.json --state-dir data/belief_core
```

Input contains `beliefs`, `evidence`, and optional `as_of`. Evidence requires `evidence_id`, `belief_id`, `source`, `observed_at`, `direction` (-1/+1), `strength`, `reliability`, `independence_cluster`; optional fields include `source_type`, `source_ref`, `derived_from`, `note`, `metadata`.

## Belief Lab

Internal route: `/pl/belief-lab.html`.

The route is `noindex,nofollow` and is not added to public navigation. It displays World State, probability vs confidence, support/opposition, provenance, freshness, audit findings, history, contradictions and calibration.

The browser panel rejects a snapshot if `mode != shadow`, trade execution is enabled, or policy output is enabled.

## Tests

```bash
python -m unittest discover -s tests -v
```

The initial suite covers bounds, probability/confidence separation, cluster de-duplication, decay, contradiction penalty, alternative normalization, provenance preservation, persistence, immutable/idempotent ingestion, verification, and hard shadow safety controls.

## Integration gate

Do not connect Belief Core to BRACE/WES/BRACE-SPX decisions merely because this code exists. First run it on real evidence in shadow mode and inspect provenance quality, de-duplication, confidence distribution, contradiction behavior, belief stability, calibration and pipeline reliability. Only then add read-only adapters, one engine at a time.
