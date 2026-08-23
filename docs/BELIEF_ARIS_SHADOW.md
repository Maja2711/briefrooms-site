# PR31 — ARIS principles inside Belief Core (shadow/read-only)

## Purpose

PR31 transfers three mathematical principles from the ARIS research direction into BriefRooms without moving ARIS code or changing the purpose of either project.

ARIS remains a compression research system:

`ARIS -> minimize bits`

BriefRooms applies only the optimization philosophy:

`BriefRooms -> minimize unnecessary complexity while retaining decision-relevant information`

The transferred principles are:

1. **Model + Residual**
2. **Competing representations**
3. **ROI-based search / pruning**

The implementation is `research_shadow` only.

## Authority boundary

Belief Core remains authoritative.

PR31 may calculate alternative diagnostic representations, but it may not:

- replace `BeliefState.probability`;
- replace `BeliefState.confidence`;
- change evidence reliability;
- write to Belief Core;
- affect BRACE/WES/Daily decisions;
- tune thresholds or policies;
- promote an alternative representation automatically.

The selected representation is a research diagnostic, not a new posterior.

## Model + Residual

Every candidate representation explicitly records both:

- the evidence retained by the model;
- the evidence left outside the model as residual.

This prevents epistemic compression from silently discarding information.

For every representation PR31 records:

- `evidence_ids`;
- `residual_evidence_ids`;
- `retained_effective_mass`;
- `residual_effective_mass`;
- `information_retention`;
- probability implied by that representation.

The residual is not treated as error to be deleted. It is first-class diagnostic information.

## Competing representations

PR31 currently evaluates four deliberately simple representations over the same canonical representative Evidence set:

- `full_representatives` — canonical reference set;
- `fresh_signal` — evidence with material current effective mass;
- `high_reliability` — evidence with reliability >= 0.70;
- `primary_preferred` — primary evidence, or non-derived evidence when no primary source exists.

These are research probes, not claims that any representation is economically optimal.

The framework is extensible: future representations must prove prospective value before promotion.

## ROI search / pruning

Each candidate gets a bounded research utility score based on:

- effective information retained;
- distance from the authoritative Belief Core probability;
- internal contradiction;
- representation complexity.

The current shadow objective is:

`ROI = retention - 0.50*authority_distance - 0.20*contradiction - complexity_penalty*complexity`

This is a transparent engineering heuristic, not a learned economic optimum.

Rules:

- `full_representatives` is never pruned by ROI;
- empty alternative representations are pruned;
- low-ROI alternatives may be pruned diagnostically;
- pruning never removes Evidence from Belief Core;
- pruning never changes the authoritative probability.

## Representation disagreement

PR31 reports the probability spread across viable representations as `representation_disagreement`.

A large disagreement means the compressed interpretation is representation-sensitive and should be treated as a reason for caution / deeper inspection, not as permission for an LLM to choose whichever representation it prefers.

## Relationship to Epistemic State

PR30 remains the authoritative reversible read interface:

`EpistemicState -> Belief -> Evidence -> Observation -> Source`

PR31 is a research diagnostic adjacent to that interface. It can later add fields such as:

- selected shadow representation;
- residual mass;
- representation disagreement;
- ROI diagnostics;

Only after prospective validation should any such field be promoted into the stable consumer contract.

## Runtime output

PR31 writes one private runtime report:

`aris_belief_shadow.json`

The report contains no action API and no writeback path.

## Hard safety invariants

All remain false:

```text
decision_influence = false
belief_core_writeback_enabled = false
automatic_tuning_enabled = false
```

Belief Core probability remains authoritative at all times.
