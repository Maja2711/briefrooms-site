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
