# GSE -> Belief Core Geopolitical Forecast Adapter

## Purpose

This adapter is the first governed boundary from the Geopolitical Scenario Engine into Belief Core.

It remains entirely **shadow/read-only with respect to investment engines**.

```text
Geopolitical sources
        ↓
       GSE
        ↓
immutable frozen GSE v1 forecast
        ↓
Geopolitical Forecast Adapter
        ↓
Observation + qualified derived Evidence
        ↓
Belief Core shadow state
```

There is no path from this adapter to WES, BRACE, BRACE-SPX, sizing, execution or policy output.

## Why GSE v1 is used and GSE v2 is not

The historical-analogue GSE v2 layer is still a research candidate being measured against GSE v1.

Therefore:

```text
GSE v1 frozen forecast -> may become Evidence after calibration gate
GSE v2 candidate       -> research telemetry only
```

The adapter records the matching v2 candidate ID, candidate probability, analogue N and hash where available, but the v2 probability cannot replace the v1 probability and cannot create Evidence.

This prevents historical-analogue research from being promoted indirectly before paired v1-v2 calibration has demonstrated value.

## Point-in-time rule

Only a frozen GSE forecast satisfying:

```text
forecast_at <= belief_as_of < target_at
```

is eligible for the adapter run.

Future forecasts and expired forecasts are excluded. For each asset/horizon, only the latest active frozen forecast is read.

The adapter never reads the mutable current GSE scenario state as a directional signal.

## Calibration gate

A GSE forecast remains observation-only unless all of the following are true:

- GSE runs in `shadow` mode,
- trade execution is hard-off,
- policy output is hard-off,
- automatic tuning is hard-off,
- decision-engine connection is hard-off,
- asset-level calibration N >= 30,
- horizon-level calibration N >= 30,
- exact asset x horizon joint calibration N >= 30,
- worst mean Brier across those slices <= 0.25,
- worst absolute calibration bias across those slices <= 0.15.

The joint gate is intentional. Good SPX calibration in aggregate plus good 24h calibration in aggregate must not substitute for evidence about the actual `SPX x 24h` slice being consumed.

Passing the gate does not mean GSE is allowed to affect an investment engine. It means only that a frozen GSE v1 forecast may contribute modest derived Evidence inside the **shadow Belief Core**.

## Serial forecast independence

GSE can freeze forecasts every six hours. Those serial forecasts are not independent research sources.

All forecasts from the same asset/horizon use one stable independence cluster:

```text
gse:serial_forecast:<asset>:<horizon>
```

This lets Belief Core select one representative forecast from the serial stream instead of accumulating multiple six-hour batches as separate evidence mass.

## Evidence strength

GSE probability and Belief evidence strength are deliberately separate concepts.

The maximum geopolitical Evidence strength is capped at `0.30` even after the calibration gate passes. The source remains `derived`, with lineage to the originating adapter Observation and metadata that preserves:

- GSE forecast ID,
- GSE batch ID,
- scenario IDs,
- underlying GSE evidence IDs,
- target timestamp,
- horizon,
- calibration qualification,
- v2 research telemetry where available.

## Initial Belief mapping

The first version is deliberately narrow.

### SPX 24h

A calibrated 24-hour GSE SPX forecast may map to the existing:

```text
spx.trend.bullish
```

A positive GSE direction supports the belief; a negative direction opposes it.

This does **not** create a separate geopolitical outcome rule yet. Using the existing SPX trend belief keeps verification tied to an observable market outcome while the geopolitical source itself is separately calibrated in GSE.

### Commodities, rates and USD

Brent, WTI, gold, copper, wheat, natural gas, U.S. 10Y and USD forecasts are stored as Observations only in this adapter version.

They are not forced into unrelated SPX beliefs. They can become Evidence only after explicit atomic Belief definitions and outcome rules exist for those assets/domains.

## Runtime order

The Belief Core market workflow performs:

```text
restore Belief shadow state
restore latest GSE shadow state read-only
GSE -> Belief geopolitical ingest
normal Belief market adapters
Belief recompute / frozen forecast if due
verification
persist cumulative Belief artifact
```

This makes a qualified GSE observation available before the next frozen Belief snapshot without changing the investment-engine boundary.

## Safety contract

Hard disabled:

```text
decision_engine_connected = false
trade_execution_enabled = false
policy_output_enabled = false
GSE v2 evidence enabled = false
```

The adapter cannot change:

- WES score, direction, TP/SL or exposure,
- BRACE decisions or portfolio weights,
- BRACE-SPX exposure,
- GSE v1 forecasts,
- GSE v2 candidate probabilities,
- Belief priors or source weights automatically.

## Next evidence question

After enough frozen Belief forecasts contain qualified GSE-derived Evidence, calibration can compare Belief forecast quality with and without that source.

Only after Belief Core itself is calibrated should the next engine-facing step be considered:

```text
BRACE-SPX frozen decision
        +
frozen Belief State
        ↓
read-only Engine-Belief observation
        ↓
WITH vs WITHOUT BELIEF counterfactual analysis
```

No bounded influence is authorized by this adapter.
