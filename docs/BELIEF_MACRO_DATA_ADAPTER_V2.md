# Belief Core — Primary Macro Data Adapter v2

## Purpose

`belief_macro_data_adapter.py` adds deterministic macro evidence from official BLS time-series data. It complements, rather than replaces, the existing News/Event and Macro/Event Calendar adapters.

The adapter is deliberately narrow. It does not use consensus estimates, does not invent a release surprise, and does not let a single monthly data point directly control a trading engine.

## Primary source

The first source is the U.S. Bureau of Labor Statistics Public Data API:

- CPI-U All Items, seasonally adjusted: `CUSR0000SA0`
- Total Nonfarm Payrolls: `CES0000000001`
- Unemployment Rate: `LNS14000000`

Every current data series is retained as a primary Observation with the BLS API endpoint, series ID, monthly data period, reliability, age and independence cluster.

Monthly observations older than 75 days are retained with `status=stale` and are not promoted into Evidence.

## Deterministic inflation evidence

The adapter computes CPI three-month annualized inflation from the official seasonally-adjusted index.

This is explicitly a **policy-pressure proxy**, not a release-surprise model.

- at or above 3.5%: modest contradiction to `spx.financial_conditions.supportive`
- at or below 2.2%: modest support for `spx.financial_conditions.supportive`
- between the thresholds: Observation only, no Evidence

Year-over-year inflation is retained as audit metadata when enough history is available.

## Deterministic labor evidence

The adapter combines official payroll levels and unemployment into one labor-growth regime Observation for a single monthly period.

It computes:

- average payroll change over the latest three months,
- unemployment-rate change over the latest three months.

A strong regime requires payroll gains of at least 125k on average with no material unemployment deterioration. A weak regime is flagged when payroll gains fall to 50k or less or unemployment rises by at least 0.3 percentage point over three months.

The resulting Evidence is deliberately modest and maps only to `spx.trend.bullish`. It is a broad macro growth-regime input, not a direct SPX return forecast and not a market-breadth substitute.

## Independence and provenance

Inflation and labor use separate monthly clusters:

- `bls:inflation:<period>`
- `bls:labor:<period>`

Multiple primary observations used in one labor assessment remain one cluster. Repeated workflow scans of the same monthly release therefore cannot manufacture independent confidence.

Derived Evidence retains the upstream primary Observation IDs and official BLS source reference in metadata.

## What this changes

The external shadow cycle now has three evidence families:

1. primary-source News/Event + governed Gemini interpretation,
2. scheduled Macro/Event Calendar risk,
3. deterministic Primary Macro Data.

The new adapter can add an independent inflation cluster to `spx.financial_conditions.supportive` and an independent labor cluster to `spx.trend.bullish` when the current data pass explicit thresholds.

It does not fabricate evidence for `spx.breadth.healthy` or `spx.liquidity.supportive`; those remain primarily market-data / cross-asset beliefs until a suitable primary-source adapter is added.

## Safety

The adapter cannot place orders, size positions, emit policy output, alter BRACE/WES/BRACE-SPX decisions, auto-tune reliability, or rewrite frozen forecasts. It only writes Observations and Evidence into the same cumulative shadow Belief Core state.
