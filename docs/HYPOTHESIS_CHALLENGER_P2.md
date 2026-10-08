# L3 P2 — Hypothesis Challenger / Shadow OOS / Promotion Gate

## Scope and authority

P2 automatically selects `CHALLENGER` hypotheses from P1, fits bounded probability-methodology alternatives on previously verified independent events, freezes challenger probabilities before the future outcome, settles only against real Belief Core Verifications, and sends prospective gate results to BriefRooms Evolution Controller.

P2 **does not** automatically change semantic hypothesis definitions, frozen Evidence, Belief Core, trading policy or execution. A gate PASS makes a candidate `PROMOTION_ELIGIBLE` for separately authorized owner review; no direct production materialization.

## Lifecycle

1. **Discovery**: Select independent resolved `event_id` (first frozen revision). Group by hypothesis version and horizon. If all horizon slices are too small, permit one cross-horizon probability mapping with protected-horizon OOS gates.
2. **Holdout**: Fit deterministic bounded `logit_affine_v1` transforms only on earlier target dates. Require at least 30 training events and 15 later holdout events; holdout Brier improves ≥2%, log loss improves, ECE may not worsen more than 2 pp. Reject weak discoveries and log them.
3. **Preregistration**: Fix `candidate_id`, transform, activation time and source fingerprint. No historical Shadow predictions.
4. **Frozen prospective Shadow**: Only forecasts created *after candidate activation*, before target and without existing Verification, receive a single immutable challenger prediction. Append `shadow_forecasts[event_id]` without rewriting champion forecasts or Evidence.
5. **Verification**: Real canonical Verification at/after target settles a separate event record. Revalidate source fingerprints and outcomes on later runs; inconsistent or missing evidence forces HOLD.
6. **Gate**: 50 independent prospectively settled events, 20 target dates, ≥5% relative Brier improvement, improved log loss, ECE deterioration ≤1 pp, accuracy decline ≤3 pp, positive improvement in 3/4 chronological target-date blocks, and no block deterioration >10%. For horizon-pooled candidates, block on any horizon with at least 10 events if its Brier deteriorates >5%.
7. **Governance**: Evolution Controller independently reruns P2 gate and Verification checks, then records `PROMOTION_ELIGIBLE`, `REJECTED`, or `PARKED`. PASS creates owner-review handoff only. Stale or missing P2 state (36-hour limit) revokes historic eligibility.

## Artifacts

- Private cumulative artifact: `HYPOTHESIS_CHALLENGERS_STATE.json`. Contains preregistrations, source hashes, frozen Shadow forecasts, real settlements, gates and audit events.
- Public Decision LAB: `hypothesis_challengers` within `data/investments/decision_lab_public.json` — sanitized candidate identifiers, scopes, discovery and OOS counts, blockers, statuses and governance.
- Evolution Controller: independent gate and candidate records, without P2 production writer.

## Checks

`tests/test_hypothesis_challenger_engine.py` covers future-only freezing, 8 revisions = 1 event, 50 real OOS settlements to PASS, horizon-protected FAIL, no synthetic settlement and idempotent replay. `tests/test_hypothesis_challenger_controller.py` covers independent review-only handoff, forged PASS rejection, contradictory verification revocation and missing-source HOLD.

Workflows: `.github/workflows/belief-calibration-live.yml` and `.github/workflows/briefrooms-evolution-controller.yml`.

**Important limitation:** current executable challenger improves the probability-calibration methodology, not a new semantic outcome rule or independent Evidence selection model. That requires an additional benchmarkable candidate adapter and a separate promotion writer.
