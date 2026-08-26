# Canonical Instrument Registry (P0.1)

## Purpose

The Canonical Instrument Registry is the single stable identity and metadata layer for instruments used by BriefRooms investment engines and their market-data adapters.

It prevents each engine from maintaining independent copies of symbol, timezone, calendar and venue metadata. The registry is fail-closed: an unknown or ambiguous instrument is an error, not a best-effort guess.

P0.1 is identity/data-routing infrastructure. It does **not** change signal formulas, ranking, LONG/SHORT/NO TRADE rules, TP/SL formulas, position sizing or execution authority.

## Canonical identity

Existing governed WES IDs remain canonical in P0.1 so no historical state is rewritten:

- `eurusd` -> Yahoo `EURUSD=X`
- `sp500_futures` -> Yahoo `ES=F`
- `btcusd` -> Yahoo `BTC-USD`

The current WES macro reference series are also registered:

- `wti_futures` -> Yahoo `CL=F`
- `us10y_yield` -> Yahoo `^TNX`

Aliases are case-insensitive after whitespace trimming, but punctuation is preserved. This is deliberate: the registry must not collapse distinct instruments merely because their names look similar.

## Metadata contract

Each `InstrumentSpec` defines stable metadata such as:

- canonical `instrument_id`,
- display name,
- asset class and instrument type,
- IANA market timezone,
- calendar profile,
- venue,
- base/quote currency where applicable,
- explicit aliases,
- provider-specific symbols,
- optional tick/pip size and underlying ID.

Provider symbols are indexed separately, so adapters can resolve an external symbol without guessing the internal identity.

## Runtime API

The registry exposes:

- `get(id_or_alias)` - resolve or raise `UnknownInstrumentError`,
- `try_get(id_or_alias)` - optional lookup,
- `resolve_instrument_id(id_or_alias)` - canonical ID only,
- `get_vendor_symbol(id_or_alias, provider)` - canonical provider symbol,
- `find_by_vendor_symbol(provider, symbol)` - reverse provider lookup,
- `canonical_vendor_symbol(id_or_alias, provider, configured_symbol=...)` - return the registry symbol and fail closed if a legacy/configured symbol has drifted,
- `all()` - immutable tuple of registered specifications.

`DEFAULT_REGISTRY` is immutable after construction. Duplicate canonical IDs, ambiguous aliases, duplicate provider symbols, invalid IANA timezones and invalid currency codes are rejected during registry construction.

## WES runtime adoption

P0.1 now routes the governed WES market-data path through canonical IDs before Yahoo symbols are used:

1. `investments_weekly_v2.py`
   - daily signal history,
   - new weekly forecast symbol snapshots,
   - Monday entry reconstruction,
   - intraday SL/TP review,
   - scheduled weekly close.
2. `investments_weekly_v3.py`
   - weekly-candle history,
   - continuous-exposure 5-minute entry mark.
3. `investments_weekly_v4.py`
   - instrument configuration admission is registry-validated,
   - emergency-week symbol snapshots are canonical,
   - current 5-minute marks use canonical symbols.
4. `investments_weekly_macro.py`
   - WTI is requested as canonical `wti_futures`, then mapped to Yahoo `CL=F`,
   - US 10Y is requested as canonical `us10y_yield`, then mapped to Yahoo `^TNX`.
5. `investments_weekly_ma_structure.py`
   - EUR/USD MA structure obtains `EURUSD=X` from canonical `eurusd` instead of owning a hard-coded provider symbol.

The legacy symbol fields in `methodology.json` and `multi_instrument_exposure_policy.json` remain temporarily for backward compatibility. On connected runtime paths they are no longer the source of truth: they are drift assertions. If a configured symbol differs from the registry, the run fails closed before requesting market data.

The current WES symbols are intentionally unchanged, so the routing migration must be decision-neutral: same provider symbol -> same market data -> same signal formulas and decision logic.

## WES drift guard

P0.1 includes a validator:

```bash
python scripts/instrument_registry.py --validate-wes
```

It verifies that:

1. every WES methodology instrument has a canonical registry entry,
2. the current Yahoo symbol matches the registry,
3. the WES asset class matches the registry,
4. every policy instrument resolves canonically,
5. WTI and US10Y macro reference symbols match their canonical IDs.

The validator only detects drift. It does not mutate methodology or live/paper decision state.

## Migration policy

Migration remains incremental.

1. P0.1 establishes the registry, runtime WES routing, tests and CI drift guard.
2. Existing frozen T0 decisions and historical records are never rewritten.
3. Additional consumers must migrate one adapter/engine at a time and preserve point-in-time semantics.
4. Each migration must demonstrate that current canonical mappings preserve decision inputs unless a separate, explicitly governed strategy change is intended.
5. Dynamic GPW/US equity universes should be integrated through a dedicated canonicalization adapter rather than by hard-coding thousands of speculative entries in P0.1.
6. Futures/CFD roll and broker mapping should be added only when the corresponding engine is introduced or migrated, with explicit tests for contract identity and roll semantics.

## Governance rule

The registry is identity infrastructure, not prediction logic. A registry change can affect which market data an engine reads, so changes to canonical IDs, provider symbols, timezone/calendar metadata or instrument type require normal code review and validation. Historical frozen T0 records are never rewritten to match a newer registry version.
