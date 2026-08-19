# GSE Historical Analogue / Geopolitical Forecast v2

## Objective

GSE v2 tests whether historical geopolitical analogues add measurable forecasting value beyond the existing deterministic GSE v1 transmission model.

It is a research-only overlay. GSE v1 forecasts remain frozen and unchanged.

The comparison is explicit:

```text
GSE v1 forecast without historical analogues
                 vs
GSE v2 candidate with historical analogue overlay
```

The primary paired metrics are Brier score and log loss. Negative `delta_brier_v2_minus_v1` and negative `delta_log_loss_v2_minus_v1` mean that the v2 candidate improved probability quality for the same frozen forecast outcome.

## Curated historical event catalogue

`data/gse/historical_event_catalog.json` contains event anchors and provenance only. It deliberately does **not** contain market outcomes.

Initial catalogue coverage includes:

- Russia / Ukraine / Black Sea escalation,
- sanctions escalation,
- grain-export disruption,
- Red Sea shipping disruption,
- Middle East energy escalation,
- China / Taiwan escalation.

Each record contains a fixed event timestamp, scenario family, institutional source and source reference.

The catalogue is versioned and cannot be selected automatically from later market performance. Changing event membership is therefore a reviewed methodology change, not online learning.

## Historical market response construction

The analogue library fetches daily market history for the existing GSE asset universe.

For every event/scenario/asset/horizon combination:

- baseline = last daily close strictly before the event date,
- target = first daily close on or after event date + fixed horizon,
- horizons = 24h, 168h, 720h,
- raw return is stored,
- return is aligned to the pre-existing GSE transmission sign,
- a non-trivial aligned move is classified as directional success.

The event catalogue and market outcomes remain separate so that the catalogue cannot embed a hand-picked successful return.

## Anti-lookahead rule

A historical analogue can be used for a forecast only if:

```text
analogue.response_complete_at <= forecast.forecast_at
```

This is stronger than merely requiring the event date to be in the past. A 30-day analogue outcome is unavailable to a historical forecast until the full 30-day response window had actually completed.

## Correlated-event cap

Two catalogue entries can describe the same market shock, for example invasion and same-day sanctions.

The layer therefore caps effective samples by:

```text
event date + asset + horizon
```

rather than by catalogue label. This prevents same-day narrative variants from inflating historical N.

## Conditional market response

For each scenario/asset/horizon slice, GSE v2 records:

- effective historical N,
- directional hit rate,
- Beta(2,2)-shrunk directional probability,
- mean and median raw return,
- mean transmission-aligned return,
- dispersion,
- event IDs and market-anchor keys.

The candidate converts every scenario-level analogue probability into the **direction of the frozen v1 forecast** before aggregation. This matters for assets such as U.S. 10Y yields where different geopolitical scenario families can have opposite transmission signs.

## Bounded research overlay

The historical overlay is deliberately small:

```text
overlay_weight = min(0.20, 0.025 * effective_historical_N)
```

and:

```text
p_v2 = p_v1 + overlay_weight * (p_analogue_for_v1_direction - p_v1)
```

with candidate probability bounded to `[0.50, 0.85]`.

This is a pre-registered research formula, not an online optimizer. It can reduce or increase v1 probability but cannot reverse the frozen v1 direction, change transmission weights or alter the v1 forecast record.

## Prospective paired calibration

For every v1 frozen forecast with eligible analogues, the system freezes a separate v2 candidate containing:

- baseline v1 forecast ID,
- v1 probability,
- historical analogue probability,
- v2 candidate probability,
- overlay weight,
- effective analogue N,
- scenario-level diagnostics,
- catalogue version/hash,
- immutable candidate hash.

When the original v1 forecast is later verified, v2 uses the **same realized binary outcome** and records paired Brier/log-loss deltas.

Outputs inside the private cumulative `gse-shadow-state` artifact:

```text
gse_historical_analogue_library.json
gse_v2_state.json
gse_v2_forecasts.jsonl
gse_v2_verifications.jsonl
gse_v2_calibration.json
```

## Safety boundaries

Hard disabled:

```text
v1_forecast_modified = false
trade_execution_enabled = false
policy_output_enabled = false
automatic_tuning_enabled = false
decision_engine_connected = false
belief_core_connected = false
```

No result from this layer can alter WES, BRACE, BRACE-SPX, Belief Core or GSE v1.

## Maturity

Historical analogue counts are expected to be small initially. Labels are therefore:

- N < 3: `insufficient_sample`,
- N 3..7: `exploratory`,
- N >= 8: `measuring`.

The paired v1-v2 calibration remains `insufficient_sample` below 30 verified candidate pairs.

No automatic promotion exists.

## Next step

Only after this output is frozen and measurable should a separate **GSE -> Belief Core Geopolitical Forecast Adapter** consume qualified GSE forecasts. That adapter remains read-only/shadow and cannot connect Belief Core to an investment engine.
