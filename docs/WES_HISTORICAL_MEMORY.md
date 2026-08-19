# WES Historical Learning Migration / Memory Unification

## Status

This layer is **derived, read-only historical memory**. It does not alter WES entry gates, strategy ranking, TP/SL, active exposure, or any other trading decision.

The migration covers `2026-W24` through `2026-W34` and preserves the original weekly JSON files unchanged.

## Why this exists

WES inherited market history from several generations of the Weekly Engine, but that history is stored in different schemas. Older weeks use top-level instrument outcomes, while later continuous-exposure weeks archive canonical `position_legs`. Reading only `position_legs` understates historical experience; treating every old outcome as if it validated current strategies would overstate it.

The memory layer solves both problems by separating:

- **Market Experience Memory** — what happened after a recorded directional episode.
- **Strategy Performance Memory** — only observations where the strategy id was explicitly recorded in the contemporaneous decision record.

Legacy v1.x outcomes can therefore inform market experience without being used as evidence that a strategy invented later was profitable.

## Quality grades

- `A = 1.00` — canonical closed leg with explicit strategy, complete timestamps and no reconstruction flags.
- `B = 0.70` — complete historical execution with trustworthy prices/timestamps but not a canonical strategy leg.
- `C = 0.40` — reconstructed, late-recovery, target-bar reconstruction, after-deadline close, or otherwise partially reconstructed execution.
- `D = 0.00` — missing entry/exit execution, no-trade, open trade, or insufficient record. Informational only.

A `C` observation may contribute to **Market Experience Memory**, but reconstructed observations do not contribute to **Strategy Performance Memory**.

## Anti-inflation controls

The report exposes two sample measures:

1. `effective_samples_raw` — sum of quality weights.
2. `effective_samples_week_instrument_capped` — caps all episodes for the same instrument in the same week at one effective market episode.

The capped measure exists because multiple same-week exits/re-entries are correlated and should not make WES look more mature than it is.

## Provenance and immutability

The builder:

- never writes to `data/investments/weekly/*.json`;
- records the source week/path for every normalized record;
- generates deterministic record hashes and a learning-projection hash;
- prefers canonical `position_legs` when they exist and does not also ingest the mutable top-level position;
- never infers a strategy id for legacy trades.

Generated files:

- `data/investments/wes_historical_memory.json`
- `data/investments/wes_memory_report.json`

Builder:

```bash
python scripts/investments_wes_memory.py --write
```

Determinism check:

```bash
python scripts/investments_wes_memory.py --check
```

## Governance gate

`active_decision_influence` is hard-coded to `false` in both the memory and report. The active WES learner continues to use its existing logic until a later, separately reviewed calibration step explicitly promotes some part of the historical memory into decision support.
