# Geopolitical Scenario Engine (GSE) v1

## Purpose

GSE is a shadow-only forecasting engine for geopolitical transmission into liquid global market assets. It is separate from trade execution and from policy output.

Architecture:

```text
Geopolitical Evidence
    -> Scenario Engine
    -> Transmission Graph
    -> Multi-Asset Forecast
    -> Frozen Forecast
    -> Verification
    -> Calibration
```

The engine is intentionally conservative. It does not forecast exact prices. It forecasts directional impact, probability, confidence and impact magnitude for fixed horizons.

## Geopolitical Evidence

The initial evidence layer uses three source families:

- GDELT DOC API for broad 30-day secondary coverage and source diversity,
- United Nations News RSS as institutional primary-source context,
- U.S. Treasury OFAC Recent Actions as high-reliability primary sanctions evidence.

Every evidence item stores a stable ID, source, source type, source URL, publication timestamp, reliability and text/title used for scenario classification.

The engine evaluates both the last 30 days and the last 7 days. Seven-day event intensity is compared with the 30-day baseline to estimate scenario acceleration.

## Scenario Engine

v1 is deterministic and auditable rather than LLM-driven. This avoids narrative hallucination before calibration data exist.

Initial scenario families:

- Middle East energy escalation,
- Russia / Ukraine / Black Sea escalation,
- Red Sea shipping disruption,
- China / Taiwan trade or military escalation,
- sanctions escalation,
- grain export disruption.

A scenario stores probability, confidence, 7-day and 30-day evidence counts, acceleration, representative frozen evidence IDs and a concise rationale.

Probabilities are capped conservatively in v1. They must not be interpreted as calibrated geopolitical event probabilities until sufficient verified history exists.

## Transmission Graph

The transmission graph is explicit code, not an LLM answer. Each scenario has signed transmission weights to relevant assets or data series.

Initial assets:

- Brent crude,
- WTI crude,
- gold,
- copper,
- wheat,
- natural gas,
- S&P 500 via SPY proxy,
- U.S. 10Y Treasury yield via Yahoo `^TNX`,
- U.S. Dollar Index.

A positive transmission weight means upward pressure on the named asset/data series; a negative weight means downward pressure. Multiple active scenarios are aggregated into one signed asset score.

## Multi-Asset Frozen Forecasts

GSE freezes forecasts for three horizons:

- 24 hours,
- 168 hours / 7 days,
- 720 hours / 30 days.

Each frozen forecast contains:

- asset and market symbol,
- direction,
- predicted probability that the forecast direction is realized,
- confidence,
- impact magnitude: small / medium / large,
- baseline market value,
- forecast and target timestamps,
- full frozen scenario snapshot,
- full frozen evidence snapshot.

Later geopolitical events cannot rewrite an existing frozen forecast.

## Verification

Every hourly GSE run checks for forecasts whose target time has passed. The verifier obtains the current market value and compares it with the frozen baseline.

The initial outcome rule requires a non-trivial move in the predicted direction. Verification records:

- realized value,
- realized return,
- binary outcome,
- Brier score,
- log loss.

Verification never mutates the original forecast snapshot.

## Calibration

After every verification cycle GSE writes `gse_calibration.json` with metrics:

- overall,
- by asset,
- by forecast horizon.

Calibration remains measurement-only. `automatic_tuning_enabled` is hard-disabled. v1 will not automatically change transmission weights or scenario probability mapping.

A sample is considered immature until at least 30 eligible verified forecasts exist in the relevant aggregate. After sufficient data accumulate, transmission weights and probability mapping should be reviewed out-of-sample rather than self-modified online.

## Runtime files

The private cumulative `gse-shadow-state` artifact contains:

```text
gse_state.json
gse_evidence.jsonl
gse_forecasts.jsonl
gse_verifications.jsonl
gse_calibration.json
```

The runtime state is not committed back into the public repository.

## Cadence

Workflow: `.github/workflows/gse-shadow-live.yml`

- Geopolitical evidence scan: every hour, 24/7, at minute 17 UTC.
- Scenario generation: every hourly run.
- Frozen multi-asset forecast: 00:17, 06:17, 12:17 and 18:17 UTC.
- Verification: every hourly run for forecasts that are due.
- Calibration: recomputed after every verification cycle.

A manual workflow dispatch can force a forecast freeze outside the normal six-hour windows.

## Safety boundaries

GSE cannot:

- place trades,
- size positions,
- connect to an execution engine,
- emit policy output,
- auto-tune scenario probabilities,
- auto-tune transmission weights,
- rewrite frozen forecasts.

The engine starts in `shadow` mode and the workflow asserts these controls before persisting state.

## v1 limitations

GSE v1 deliberately prioritizes auditability over breadth. Important next improvements after enough frozen outcomes are collected include:

1. adding more primary geopolitical feeds from governments and international institutions,
2. separating event occurrence probability from conditional market impact probability,
3. historical analogue retrieval for similar geopolitical episodes,
4. event-specific calibration by scenario type,
5. better yield-curve coverage beyond the U.S. 10Y series,
6. optional governed LLM scenario synthesis only after deterministic baseline calibration is measurable.
