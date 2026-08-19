# Belief Calibration Foundation

## Purpose

`BELIEF_CALIBRATION_REPORT.json` is the canonical measurement report for the BriefRooms Belief Core shadow programme.

This layer answers two questions before any engine bridge can influence a decision:

1. Is the Belief Core data pipeline healthy, fresh, auditable and sufficiently covered by frozen forecasts and later outcomes?
2. Are the probabilities empirically calibrated, rather than merely plausible-looking?

The foundation is **measurement-only**. It cannot change WES, BRACE, BRACE-SPX, GPW Daily, position size, exposure, priors, source reliability, GSE transmission weights or execution policy.

## Data Quality Adapter

`scripts/belief_data_quality_adapter.py` is deliberately a diagnostic adapter, not an Evidence Adapter.

Hard properties:

- `emits_evidence = false`
- `decision_influence = false`
- it never creates a `belief_id`
- it never changes probability, confidence, reliability or policy

It reports:

- observation status counts,
- stale/invalid rate,
- unavailable rate,
- freshness age distribution,
- collection latency where ingest timestamps exist,
- source health,
- adapter health,
- runtime age and scheduler gaps,
- ledger integrity,
- frozen forecast snapshot coverage,
- due-forecast verification coverage,
- unresolved due forecasts,
- evidence latency at forecast freeze.

The most important latency measure is point-in-time forecast evidence latency: the difference between `forecast_at` and the timestamp of the evidence that was actually frozen into that forecast. This remains auditable even for old observations that predate explicit collection-latency telemetry.

## Proper calibration metrics

The report reuses the existing Belief Core calibration engine and surfaces in one place:

- Brier score,
- log loss,
- ECE,
- MCE,
- calibration bias,
- reliability curve,
- Brier decomposition,
- confidence-vs-error diagnostics,
- source diagnostics,
- evidence-type diagnostics,
- domain/entity/regime/horizon slices,
- drift,
- calibration recommendations.

The existing sample safeguards remain unchanged:

- global calibration gate: 30 eligible outcomes,
- slice labels: 8 outcomes,
- source/evidence reliability review: 15 distinct forecasts,
- drift: at least 30 eligible outcomes split across two windows.

These thresholds enable analysis only. They never enable policy automatically.

## GSE as a forecast-source calibration input

The Geopolitical Scenario Engine is included in the foundation as a **separate forecast source**.

The calibration workflow restores the latest private `gse-shadow-state` artifact and reads:

- `gse_state.json`,
- `gse_evidence.jsonl`,
- `gse_forecasts.jsonl`,
- `gse_verifications.jsonl`,
- `gse_calibration.json`.

The report measures:

- GSE runtime freshness,
- source health,
- JSONL integrity,
- frozen scenario/evidence coverage,
- forecast evidence latency,
- due/unresolved forecast rate,
- verification coverage,
- Brier/log-loss/bias,
- native GSE calibration by asset/horizon when present,
- GSE safety controls.

**GSE is not yet Belief Core evidence in this PR.** The report explicitly records:

`future_adapter_role = forecast_source_only_until_separate_GSE_to_Belief_Core_read_only_adapter_PR`

This prevents an architectural shortcut in which an uncalibrated geopolitical forecast silently becomes a trading signal.

## Canonical output

The cumulative private Belief Core artifact contains:

```text
BELIEF_CALIBRATION_REPORT.json
```

Top-level sections are:

```text
data_quality
latency_freshness
stale_invalid
source_health
frozen_forecast_coverage
belief_calibration
proper_scoring
source_evidence_diagnostics
regime_horizon_slices
drift
gse_forecast_source
promotion_gate
```

## Promotion gate

For this foundation version:

```text
decision_influence_allowed = false
bounded_modifier_allowed = false
```

This is hard governance, not a temporary sample-size calculation. Even if all metrics become green, a separate reviewed bridge/promotion PR is required before any engine can consume Belief Core for decisions.

## Next sequence

After this foundation is producing stable reports:

1. **GSE Historical Analogue / Geopolitical Forecast v2**
   - retrieve historical analogues for comparable geopolitical episodes,
   - measure historical conditional responses in indices and commodities,
   - separate event-occurrence probability from conditional market-response probability,
   - freeze analogue provenance and prevent future-data leakage.

2. **GSE -> Belief Core Geopolitical Forecast Adapter**
   - read-only/shadow,
   - converts only calibrated, frozen, point-in-time GSE forecasts into explicitly derived Belief evidence,
   - preserves GSE forecast IDs, scenario IDs, evidence lineage and horizon,
   - no engine decision influence.

3. **BRACE-SPX read-only Belief bridge**
   - WITH vs WITHOUT BELIEF counterfactual measurement,
   - no score/exposure change until a later promotion gate.
