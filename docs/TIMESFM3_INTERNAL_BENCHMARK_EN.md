# TimesFM3 — internal EUR/USD benchmark

## Purpose

TimesFM3 is an external directional-skill benchmark for BriefRooms-owned engines. It is not part of the BriefRooms decision architecture and has no trading authority.

v1 scope:
- **Daily EUR/USD** — compare the engine LONG/SHORT direction with TimesFM3 over 24h, defined as 48 subsequent completed 30m bars.
- **WES EUR/USD** — compare the WES LONG/SHORT direction with TimesFM3 through that week's frozen `exit_target_local`.

## Paired benchmark methodology

For each new economic decision after prospective activation:
1. freeze the BriefRooms engine direction,
2. cap TimesFM3 context strictly at data available by that decision,
3. generate a TimesFM3 direction from the same decision origin,
4. later settle both directions against the same future market price.

The private report tracks separately for Daily and WES:
- paired resolved N,
- BriefRooms engine hit rate,
- TimesFM3 hit rate,
- hit-rate delta,
- direction agreement rate,
- disagreement count,
- who wins when the directions disagree,
- both-correct and both-wrong counts.

## Boundaries

TimesFM3:
- has no decision influence,
- owns no Risk Policy,
- opens no position,
- has no PnL, sizing or execution authority,
- writes nothing to Belief Core or EpistemicState,
- cannot automatically tune or promote BriefRooms engines,
- has no public result projection.

PnL and risk-adjusted performance remain the primary evaluation of BriefRooms trading engines. TimesFM3 answers only: **does an external model choose direction better or worse at a comparable horizon?**

## Anti-hindsight

- no historical backfill,
- a source decision must occur after `activated_at`,
- inference must be frozen before the benchmark outcome becomes observable,
- model context is capped at information available at the decision timestamp,
- the research ledger is append-only and hash-chained.

## TimesFM3 license gate

The integration is ready for private research, but pretrained-weight inference runs only when this GitHub Actions repository variable is explicitly set:

`TIMESFM3_RESEARCH_LICENSE_OK=true`

This is a deliberate fail-closed gate. The current TimesFM3 pretrained weights are restricted to non-commercial / non-production use. Setting the variable does not alter the license; it only records that the repository owner has determined the intended use is covered.

Research state is stored only in the private GitHub Actions artifact `timesfm3-internal-benchmark-state`.
