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


## Daily EUR/USD v1.8 — Contextual Entry Policy

Daily EUR/USD now separates **directional thesis** from **entry timing**.

```text
Daily Direction Engine -> LONG / SHORT
Contextual Entry Policy -> NOW / PB20 / PB35 / PB50 / FLAT
EPE -> verified executable paper fill
Daily lifecycle -> OPEN / SL / TP / dynamic exit / outcome
counterfactual settlement -> learner update -> next context-local authority
```

The contextual learner is prospective and counterfactual. For each frozen decision context it later settles all entry-policy variants against the same future path and EPE spread model. Similarity uses market stretch/move, Daily score/confidence, Belief probability and macro score, FSE state, Event Intelligence and recency. FSE regime mismatch is explicitly penalized.

Authority is **not** granted by a fixed raw trade count. The learner estimates expected R, uncertainty and effective-neighbor support for the current context. Strong, highly similar evidence can earn LOW authority with a small sample; many inconsistent observations can remain SHADOW. Authority progresses context-locally through `SHADOW -> LOW -> MEDIUM -> FULL` and falls automatically when the robust edge disappears.

Production authority is intentionally narrow:

- market direction always remains owned by the Daily directional engine;
- `REVERSAL_NOW` is retained as research/counterfactual evidence only;
- the learner may choose only `CONTINUATION_NOW`, `PULLBACK_20_ATR`, `PULLBACK_35_ATR`, `PULLBACK_50_ATR`, or `FLAT`;
- a pullback choice creates a frozen pending trigger for at most the learner horizon and does not move the trigger behind price;
- the pending entry is cancelled when direction/admission is invalidated or when it expires;
- EPE remains the only verified-fill layer and the canonical Daily lifecycle remains the only position/outcome owner;
- no historical episode is rewritten and no single episode directly mutates production policy.

Runtime implementation: `scripts/daily_eurusd_spot_v18.py` plus `scripts/daily_eurusd_contextual_policy_learning.py`.

## Daily EUR/USD high-impact macro risk

Daily EUR/USD v1.8 keeps directional authority in the Daily Direction Engine, but open-position risk is event-aware.

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

