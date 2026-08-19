# Daily Stock Core

## Purpose

`Daily Stock Core` is the shared deterministic engine for the BriefRooms 1–2 session paper-trading selectors. It removes duplicated quant logic while keeping market-specific evidence, calendars, currencies and execution controls in adapters.

The core is **not** allowed to mix GPW and US learning samples. Market memory remains isolated.

## Shared core

Implemented in `scripts/daily_stock_core.py`.

Shared responsibilities:

- momentum and relative momentum;
- volume/liquidity scoring;
- cross-sectional market/sector context;
- ATR-based position risk;
- entry/stop/target and reward/risk;
- six-factor composite score;
- bounded historical expectancy learning;
- frozen strategy weights.

Canonical weights remain:

- catalyst: 25%;
- relative momentum: 20%;
- volume/liquidity: 15%;
- market context: 15%;
- risk/reward: 15%;
- historical expectancy: 10%.

The common Bayesian historical overlay uses shrinkage and cannot change strategy weights after one trade. GPW keeps its established learning/control-loop implementation; the adapter calls it dynamically so no accumulated GPW behaviour is discarded.

## GPW adapter

Implemented in `scripts/daily_stock_gpw_adapter.py` and installed before the existing event runtime captures the quant functions.

Market-owned responsibilities stay unchanged:

- PLN;
- GPW trading calendar;
- session confirmation from 09:05 Europe/Warsaw;
- ESPI/EBI, PAP MediaRoom, Biznes PAP and independent evidence;
- existing GPW event-family learning;
- existing `gpw_daily_control_loop.py` Bayesian learning;
- opening-price cross-check and fail-closed controls;
- existing outcome monitor and durable GPW history.

## US adapter

Implemented in `scripts/daily_stock_us_adapter.py` and installed by `scripts/us_daily_stock_runtime.py`.

Market-owned responsibilities:

- USD;
- NYSE/Nasdaq calendar;
- analysis/session confirmation from 09:35 ET;
- SEC 8-K evidence, company releases and independent financial news;
- Yahoo/Stooq market-data resilience;
- US-only historical learning sample.

SEC lookup is best-effort. A provider failure cannot bypass the existing source-evidence and independent-review gates.

## Public presentation

PL and EN portfolio rooms use the same renderer:

- `scripts/daily-stock-markets-public.js`
- `assets/daily-stock-markets.css`

Both views show the GPW and US cards together. History is presented as one Daily Trade section with two market groups. The underlying GPW and US history indexes remain separate so learning and audit trails cannot contaminate each other.

## Invariants

1. GPW historical/event learning is preserved.
2. GPW and US samples are never pooled for model adaptation.
3. AI may analyse/review evidence but cannot bypass deterministic hard gates.
4. Strategy weights remain frozen unless a separately reviewed methodology change explicitly changes them.
5. Market adapters own calendars, currency, official evidence and execution timing; the core owns quant/scoring mechanics.
