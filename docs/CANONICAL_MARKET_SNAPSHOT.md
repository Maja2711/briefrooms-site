# P0.2 — Canonical MarketSnapshot + Data Quality + timestamp lineage

## Purpose

P0.2 defines one immutable point-in-time market-data contract for BriefRooms.
It answers a simple audit question for every future canonical decision:

> What exact market facts could the engine have observed, when did BriefRooms receive them, and were they admissible at decision time?

The contract does not rank instruments, size positions, change RiskPolicy, tune models or execute trades.

## Canonical snapshot

`CanonicalMarketSnapshot` records market facts only:

- `snapshot_id` and `snapshot_hash` — deterministic SHA-256 identity;
- `instrument_id` — stable canonical identity;
- `provider` and `provider_symbol`;
- `source_ref`;
- `observed_at` — source market observation time;
- `received_at` — runtime time at which BriefRooms had the source result;
- `created_at` — canonical snapshot construction time;
- `market_status` and `quote_kind`;
- available bid/ask/last/OHLC/volume fields;
- `source_schema` where available.

All canonical timestamps are timezone-aware and normalized to UTC. Naive timestamps are rejected rather than silently assigned a timezone.

The snapshot is immutable. Freshness is deliberately **not** part of its identity because a market fact can be fresh for one consumer/time and stale for another.

## Timestamp lineage

At snapshot construction the runtime enforces, subject only to a configured small clock-skew tolerance:

`observed_at <= received_at <= created_at`

At consumption/decision time P0.2 additionally enforces:

`created_at <= decision_at`

and rejects a market observation that occurs after the consumer's point-in-time state.

This creates the base lineage required by P0.3 DecisionEnvelope without fabricating historical timestamps.

## Data Quality

`FreshnessPolicy` is consumer/engine-specific. There is no global stale threshold for every market.

A policy specifies:

- maximum snapshot age;
- required price fields;
- future clock-skew tolerance;
- optionally allowed market statuses.

`DataQualityAssessment` returns one of:

- `OK`
- `STALE`
- `INCOMPLETE`
- `UNAVAILABLE`
- `INVALID_TIMESTAMP`
- `SOURCE_ERROR` (reserved for producer/source wrappers)

Only `OK` maps to `decision_status = ALLOWED`. Every other state maps to:

`DATA_QUALITY_BLOCKED`

No missing value is interpreted as a neutral signal.

## Prices that do not exist are not invented

Yahoo OHLC data does not provide an executable bid/ask spread. P0.2 therefore leaves `bid` and `ask` null instead of synthesizing them.

Execution costs, spread/slippage and market-impact assumptions belong to P0.5 Execution Simulation, not MarketSnapshot.

## Instrument identity

P0.1 `InstrumentRegistry` remains authoritative for its governed static instruments, including EUR/USD and WES instruments.

GPW and US Daily use dynamic equity universes. During their incremental migration P0.2 assigns deterministic scoped IDs such as:

- `PKO.WA -> equity.pl.pko`
- `AAPL -> equity.us.aapl`

This does not pretend that all dynamic equities have already been promoted into the static P0.1 registry. A later registry extension can replace the compatibility mapping without changing the snapshot contract.

## Current producer coverage

### CANONICALIZED

**GPW Daily**

The existing Yahoo/Stooq opening snapshot first passes `gpw-data-gates-v1`, then P0.2 wraps it in a canonical snapshot. The existing GPW freshness/cross-check settings remain the market-specific policy source. A canonicalization failure is fail-closed.

**US Daily Stock**

The existing Yahoo regular-session quote is wrapped by `daily_stock_us_adapter` before repricing/final selection. US-specific freshness settings live in `us_daily_stock_config.json`. The wrapper preserves the original price/ranking/risk geometry and adds lineage only.

### PARTIAL

- Daily EUR/USD: point-in-time timestamps exist, but the active decision lifecycle does not yet attach a P0.2 snapshot.
- EURUSD A/B/C shadow: Yahoo OHLC is point-in-time research data, but remains in its existing shadow signal snapshot.
- WES: P0.1 canonical instrument routing exists; P0.2 snapshot attachment is deferred to a dedicated WES migration.

### NOT_YET_CANONICALIZED

- BRACE-SPX: currently consumes its own point-in-time engine state plus authoritative EpistemicState rather than P0.2 MarketSnapshot.

`coverage_report()` is explicit: unknown coverage never means PASS.

## Legacy data

P0.2 is prospective.

Existing Learning Ledger events, Experience Store rows, GPW/US history and EURUSD history are not rewritten and do not receive fabricated snapshot IDs.

A legacy record without a canonical `market_snapshot_id` remains a legacy record. P0.3 DecisionEnvelope will make canonical lineage mandatory for newly governed decisions and can keep legacy records in a separate non-canonical cohort.

## Relationship to later layers

Target architecture:

`InstrumentRegistry -> CanonicalMarketSnapshot -> EpistemicState -> DecisionEnvelope -> per-engine RiskPolicy -> Execution/Shadow -> Outcome -> Experience Store`

P0.2 provides the market-data fact and quality boundary only. P0.3 will bind `market_snapshot_id`, `epistemic_state_id`, decision time and authority state into one immutable DecisionEnvelope.
