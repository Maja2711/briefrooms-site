# ARIS Shadow Periodic Report

## Purpose

Aggregate the PR31 ARIS-inspired Belief Core shadow diagnostics over many real cycles so that decisions about future representation simplification are evidence-based rather than based on a single replay.

The report evaluates only the three transferred principles:

1. Model + Residual
2. Competing Representations
3. ROI-based Search / Pruning

It does not import ARIS compression operators and has no authority over Belief Core, EpistemicState, BRACE, WES, ranking, sizing or execution.

## Cadence

The GitHub Actions workflow runs weekly on Monday at 06:00 UTC and can also be run manually.

It restores up to the 60 most recent non-expired `belief-aris-shadow` artifacts and aggregates their `aris_belief_shadow.json` payloads.

## Main metrics

- number of shadow cycles
- total belief evaluations
- winner counts by representation
- non-full representation win share
- selected information retention
- selected complexity reduction versus `full_representatives`
- selected residual mass ratio
- absolute probability gap versus authoritative Belief Core probability
- representation disagreement
- candidate pruning rates
- per-belief winner history

## Recommendation states

`COLLECT_MORE_SHADOW_DATA` means the sample is still too small.

`KEEP_SHADOW` means there is not enough evidence that simpler representations justify deeper validation.

`REVIEW_CANDIDATES_FOR_CALIBRATION_TESTING` means simpler representations have shown enough repeated information/complexity efficiency to justify a separate calibration and decision-quality study. It is not permission to enter the authority path.

## Hard safety boundary

The report always keeps these controls false:

- decision influence
- Belief Core writeback
- automatic promotion
- automatic tuning

Outcome calibration and downstream decision quality are explicit blockers before any future promotion can be considered.
