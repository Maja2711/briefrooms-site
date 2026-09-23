# ARIS-PATTERN-1 — prospective market-pattern discovery

## Purpose

ARIS-PATTERN-1 extends the existing Belief ARIS research branch with temporal
pattern discovery over prospectively frozen Belief Core forecasts.

It does not move the standalone ARIS compression code into BriefRooms. It
transfers the ARIS research principle that a useful representation must reduce
total description length after the cost of the representation itself is paid.

The research question is:

> Which small combinations of point-in-time Evidence repeatedly make later
> verified outcomes easier to describe than the baseline outcome stream?

## Input contract

Only prospective, calibration-eligible Belief Core verification rows are used.
Every eligible row must carry the Evidence snapshot frozen before outcome.

Legacy rows, rows without a forecast_id, rows marked calibration-ineligible and
rows without a frozen Evidence snapshot are excluded.

The time order is preserved.

## Pattern atoms

ARIS-PATTERN-1 v1 uses deliberately small atoms:

- Evidence type plus direction;
- current regime when it is known.

Weak Evidence with effective mass below the configured floor is not promoted to
an atom.

Patterns contain 2-4 atoms. The candidate vocabulary is bounded per Belief group
to avoid unconstrained combinatorial search.

## Discovery and holdout

Each Belief/horizon group is sorted by forecast time and split chronologically:

- earlier observations: discovery;
- later observations: holdout/OOS.

Candidate selection sees discovery only. The later holdout slice is never used
to decide which atom combinations should be published.

This is an anti-hindsight boundary, not merely a UI convention.

## MDL objective

A candidate is retained only when it has positive discovery MDL gain.

The baseline encodes the outcome sequence with a Jeffreys Bernoulli universal
code. A pattern candidate pays for:

1. the outcome sequence when the pattern is present;
2. the residual outcome sequence when the pattern is absent;
3. the description cost of selecting the pattern atoms from the candidate
   vocabulary.

Conceptually:

    MDL gain =
      L(outcomes | baseline)
      - [L(pattern) + L(outcomes | pattern) + L(residual outcomes)]

A pattern with zero or negative gain is not published even when its raw hit rate
looks attractive.

## Residual and modifier diagnostics

Exceptions remain first-class data.

For every published pattern the report keeps the number of residual exceptions.
ARIS may also identify a possible modifier: an atom that is substantially more
common in the exceptions than in successful pattern occurrences.

A modifier is only a diagnostic lead. It is not automatically appended to a
production rule.

## OOS statuses

Current statuses are:

- DISCOVERY — positive discovery MDL, insufficient holdout support;
- OOS PASS — later holdout preserves a positive lift;
- REPLICATED — stronger minimum discovery/holdout support and positive holdout
  lift;
- UNSTABLE — later holdout reverses or removes the discovery advantage.

These are research labels, not execution permissions.

## Causal boundary

Every pattern carries:

    causal_status = ASSOCIATION_ONLY

ARIS-PATTERN-1 does not write causal edges and does not change the Epistemic
Causal Graph setting that forbids automatic correlation-to-causation inference.

A repeated predictive association may become a candidate for a separate causal
research process, but it is never a causal result by itself.

## Authority boundary

ARIS-PATTERN-1 is research_shadow only.

The following paths are explicitly disabled:

    decision_influence = false
    production_decision_influence = false
    belief_core_writeback_enabled = false
    consumer_contract_export_enabled = false
    trade_execution_enabled = false
    automatic_promotion_enabled = false
    automatic_tuning_enabled = false
    causal_edge_writeback_enabled = false

The module cannot rank production trades, size positions, modify Belief
probabilities, alter Evidence weights or execute a transaction.

## Decision LAB

The sanitized public projection exposes an ARIS Patterns tab with:

- Pattern;
- discovery N;
- P(outcome);
- baseline;
- lift;
- MDL gain;
- OOS success rate;
- research status.

Drill-down exposes:

- the atom combination;
- expected outcome;
- discovery and holdout counts;
- regime slices;
- residual exceptions;
- possible modifier;
- explicit ASSOCIATION ONLY status.

No demonstrative/fake patterns are inserted. If the prospective sample is too
small or no candidate pays its MDL cost, the UI shows an empty research state.

## Files

- scripts/belief_aris_pattern.py
- scripts/decision_lab_public_projection.py
- pl/inwestycje/decision-lab.html
- scripts/decision-lab.js
- assets/decision-lab.css
- tests/test_belief_aris_pattern.py
- tests/test_decision_lab_public_projection.py
