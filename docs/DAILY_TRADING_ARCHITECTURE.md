# BriefRooms Daily Trading architecture — legacy compatibility record

## Status

**SUPERSEDED FOR STOCKS.**

The former user-facing stock architecture:

```text
Daily GPW
Daily US Stocks
```

has been replaced by the unified **Stock Trading v2** product for GPW and US.

The stable historical module IDs remain for traceability:

- `TR-01` — legacy GPW Daily pipeline;
- `TR-02` — legacy US Daily pipeline;
- both are `REPLACED_BY TR-04`.

They are not separate active stock-trading products and do not own production stock admission.

## Current investment boundary

```text
INWESTYCJE
├── STOCK TRADING
│   ├── GPW  -> Stock Trading v2
│   └── US   -> Stock Trading v2
│
├── DAILY EUR/USD SPOT
│   └── separate FX engine / compatibility path
│
├── WEEKLY POSITIONS
│   ├── EUR/USD
│   ├── S&P 500 Futures
│   └── BTC/USD
│
├── PORTFEL 10K
│   └── BRACE / portfolio
│
└── LONG VIEW
    └── SPX and other long-view research
```

## What remains from the former Daily Stock architecture

Legacy GPW/US code, workflows and data may remain while they still serve one of these purposes:

- historical settlement,
- rejected-candidate / MISS research,
- lineage and audit,
- compatibility migration,
- v1 Challenger comparison.

Their existence in the repository does **not** mean that Daily GPW or Daily US are active products.

The production source of truth is `data/investments/stock_trading_policy.json`, which currently sets v2 as Champion and disables legacy candidate admission.

## Daily Engine Contract

`scripts/daily_engine_contract.py` and `scripts/daily_engine_adapters.py` remain compatibility contracts. For stocks they no longer define the active product boundary.

Daily EUR/USD may continue to use the Daily-family contract independently; its lifecycle and promotion status must be evaluated separately from Stock Trading v2.

## Governance invariants

1. Do not reintroduce Daily GPW/US as production stock authorities without an explicit architecture change.
2. Legacy candidate output cannot bypass Stock Trading v2 production admission.
3. Do not rewrite historical Daily GPW/US ledgers during migration.
4. Preserve point-in-time lineage and NO RETROACTIVE execution semantics.
5. Keep GPW and US market-specific data/risk handling inside Stock Trading v2 where market behavior differs.


## Daily EUR/USD v1.9 — Belief-first final decision

Daily EUR/USD now follows the same epistemic ordering as the canonical BriefRooms architecture.

```text
RAW / PRIMARY / MARKET SOURCES
        |
        v
SPECIALIZED ADAPTERS
        |
        v
OBSERVATIONS
        |
        v
EVIDENCE ASSESSMENT
        |
        v
BELIEF CORE
        |
        v
EPISTEMIC STATE
        |
        v
CF-07 EPISTEMIC CONSUMER INTERFACE
profile: DAILY_EURUSD
        |
        v
NATIVE DAILY EURUSD BELIEF-FIRST DECISION ENGINE
        |
        v
FINAL LONG / SHORT / FLAT
        |
        +---- FLAT ----------------------> END
        |
        v
EXECUTION ADMISSION / SAFETY
        |
        v
EPE VERIFIED FILL
        |
        v
CANONICAL POSITION LIFECYCLE
```

### Direction authority

There is one final production direction owner:

`NATIVE_DAILY_EURUSD_BELIEF_FIRST_DECISION_ENGINE`.

It does not create a preliminary EUR/USD direction before Belief. Belief Core is projected into the authoritative read-only EpistemicState, and the existing CF-07 Epistemic Consumer Interface exposes only the five governed EUR/USD states through profile `DAILY_EURUSD`. The decision engine cannot override or write back probabilities/confidence; it synthesizes the final `LONG / SHORT / FLAT` only from that bounded projection. Production requires this interface and fails closed to FLAT when the projection is missing, invalid, future-dated or stale.

The former direct path:

`EURUSD trend + UUP + TLT -> score -> LONG/SHORT/FLAT`

is legacy implementation lineage only. It has no v1.9 production direction authority.

UUP and TLT remain useful upstream cross-asset Evidence. They are produced by `belief_wes_assets_adapter.py`, enter Belief Core, preserve their own market timestamps, decay according to freshness, and are never re-stamped with a fresher EUR/USD timestamp.

EUR/USD market Evidence is refreshed across the tradable FX week (approximately Sunday 17:00 New York through Friday 17:00 New York). Closed US-market UUP/TLT observations are allowed to age naturally instead of being treated as current.

### Belief coverage and missing data

v1.9 uses the existing governed EUR/USD Belief family:

- `eurusd.trend.bullish`;
- `eurusd.usd_environment.supportive`;
- `eurusd.us_rates_pressure.supportive`;
- `eurusd.macro_surprise.supportive`;
- `eurusd.policy_differential.supportive`.

The migration intentionally preserves the existing bridge weights as an initial production prior. It does **not** claim those weights are statistically optimal. Tuning remains a future-only calibration/promotion task.

Missing or stale Evidence is not converted into a neutral vote. It reduces coverage. If the required EUR/USD trend anchor is unavailable or total qualified coverage is below the minimum contract, the final decision fails closed to `FLAT`.

Official ECB releases are now collected into the primary-source News/Event intake, so they can become Evidence for the EUR/USD policy-differential belief before the final decision. Fed, macro-release, expectations and broader event inputs follow the same Observation -> Evidence -> Belief path. An LLM interpreter may structure already-observed sourced material but may not invent consensus, release values or forecasts.

### Shadow isolation

The following remain research/shadow and have zero v1.9 production direction **and timing** authority:

- A/B/C;
- FSE;
- EURUSD X;
- legacy `A_TECHNICAL_FALLBACK`;
- legacy `LOW_EDGE_LEARNING_EXPLORATION`.

The v1.8 Contextual Entry Policy is reset to `SHADOW_AFTER_BELIEF_FIRST_MIGRATION`. Its historical/counterfactual state is retained for audit, but it cannot choose NOW/PULLBACK/FLAT in v1.9 production until a future prospective promotion explicitly proves value under the new Belief-first semantics. This also removes the previous indirect FSE influence on production entry timing.

### What remains after the final decision

After `FINAL LONG / SHORT / FLAT`, later layers may not manufacture or reverse market direction.

For a directional final decision, execution admission may block the trade for operational/risk reasons, including:

- missing/stale/failed required macro-calendar source coverage;
- high-impact scheduled event proximity or post-release Evidence still pending;
- re-entry / same-thesis safety;
- invalid or unavailable execution geometry;
- EPE quote-consensus failure.

A blocked execution does not rewrite the epistemic decision. Runtime metadata preserves the final decision separately from the executable candidate.

EPE remains the only new-entry fill authority and requires at least two distinct fresh agreeing EUR/USD quote providers.

Open-position risk remains separate from new-entry direction. SL/TP, dynamic risk, macro-event risk and governed Event Intelligence may close an already-open position when new information invalidates its risk economics. They cannot originate the next LONG/SHORT.

### Migration / history

v1.9 is prospective only.

- Existing v1-v1.8 trades remain immutable historical records.
- No historical trade is re-labelled or recomputed.
- Legacy adaptive Daily component weights are retained for audit but are not applied to the v1.9 direction decision.
- Legacy pending contextual entries are cancelled prospectively on v1.9 activation rather than reinterpreted under new semantics.
- Code pushes remain validation-only; scheduled/manual runtime owns trading-state mutation.

Runtime implementation:

- `scripts/daily_eurusd_belief_decision.py`;
- `scripts/daily_eurusd_spot_v19.py`;
- `scripts/belief_epistemic_state.py`;
- `scripts/epistemic_consumer_interface.py` (`DAILY_EURUSD`);
- upstream Belief adapters / `belief_core_live.py`;
- `scripts/execution_price_engine.py`;
- canonical Daily lifecycle.

## Daily EUR/USD high-impact macro risk

Daily EUR/USD v1.9 makes the new-entry direction only through the Belief-first path above, while open-position risk remains independently event-aware.

For high-impact releases such as Employment Situation/NFP, CPI/PCE, FOMC and ECB events:

- dynamic risk exits have no minimum position-age gate;
- a profitable position with little remaining reward relative to downside can be closed before the release by the macro-event asymmetry overlay;
- sourced institutional expectations can be converted into an auditable distribution and probability proxy before the release;
- the governed EURUSD-only LLM interprets that sourced package but is not allowed to invent consensus or bank forecasts;
- after the release, official BLS/BEA/Eurostat data are re-read and a fresh interpretation is required before the post-release context is marked ready;
- the BLS monthly period contract is `YYYY-MMM`, and NFP comparison uses the monthly payroll change carried in the primary payroll observation metadata;
- missing expectations fail closed and do not create directional LLM influence.

The low-latency runtime is `scripts/daily_eurusd_macro_fast.py`; open-position event-risk authority is `scripts/daily_eurusd_macro_event_risk.py`. Neither component may create a new direction or reverse an existing position.

### Re-entry and weekly-close governance

Risk exit and new-entry authority are deliberately separated.

- An open EUR/USD position may still be closed immediately by SL/TP, Dynamic Risk or macro-event risk; there is no minimum position age.
- A new opposite-direction entry within 60 minutes of a recent opposite STOP_LOSS, DYNAMIC_RISK_EXIT or MACRO_EVENT_THESIS_INVALIDATION cannot be created by a secondary fallback alone. It requires either the primary NATIVE direction engine or fresh source-backed macro/event evidence.
- A recent meaningful loss in the same direction cannot be bypassed merely by switching source family from NATIVE_COMPONENTS to A_TECHNICAL_FALLBACK (or vice versa). Cross-family re-entry must show material score/confidence/state change or fresh source-backed external evidence.
- On Friday from 12:00 New York time onward, secondary entry sources A_TECHNICAL_FALLBACK and LOW_EDGE_LEARNING_EXPLORATION are blocked from opening new positions. This does not automatically block a primary NATIVE signal.
- These guards affect only future admission. They never retroactively close, rewrite or reverse an already-open position.

### Data and execution admission integrity

A NATIVE LONG/SHORT signal is necessary but not sufficient for a new fill.

- EUR/USD, UUP and TLT model inputs must be timestamp-aligned within 90 minutes. Misaligned cross-asset proxies block new admission rather than being treated as neutral.
- EPE requires at least two independent fresh EUR/USD quotes for a new paper fill.
- With two fresh quotes, cross-feed disagreement above the configured tolerance blocks the fill.
- With three or more feeds, at least two must form an inlier consensus cluster.
- Single-source and degraded-divergence fills are not considered verified.
- Code pushes to `main` are validation-only for Daily EUR/USD. Production state reconciliation runs only from the scheduled market cycle or explicit manual dispatch.

