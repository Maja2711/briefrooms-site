# BriefRooms weekly paper methodology v4

## Status

The filename is retained for historical continuity, but the current governed runtime is newer than the original v4 exposure rule. Runtime authority lives in `data/investments/multi_instrument_exposure_policy.json` (currently policy v5.6.x) together with `data/investments/wes_methodology.json`.

The system remains experimental governed paper trading only. It places no broker orders.

## NO_TRADE and exposure

A weekly position is **not mandatory**.

Current policy explicitly sets:

- `mandatory_monday_position = false`;
- `continuous_position_required = false`;
- `NO_TRADE` is a first-class active state.

The tournament may rank a directional candidate, but ranking alone does not authorize a position. EUR/USD, S&P 500 futures or BTC/USD is opened only when the current admission gates qualify the candidate. Otherwise WES remains in `NO_TRADE` and continues monitoring for a later qualified trigger.

After a governed close, re-entry is not automatic merely because a position slot is empty. Re-entry requires the applicable WES trigger, validation and lifecycle rules. Monday/Tuesday early-close replacement may use the separately governed rolling seven-calendar-day path; material-event exits remain fail-closed and are not automatically replaced.

## Why an inverse signal is tested separately

A negative result for a short method does not prove that the corresponding long method is profitable. Transaction costs, timing, stop-losses, take-profits and asymmetric price behaviour can make both directions unprofitable. Therefore `base_v2` and `inverse_v2` are independent candidate methods and receive separate walk-forward results.

## Candidate methods

The strategy tournament evaluates:

1. `base_v2` — direction from the saved daily trend, momentum and breakout model.
2. `inverse_v2` — the opposite direction, tested as a separate hypothesis.
3. `weekly_trend` — direction from weekly EMA, momentum, breakout and candle structure.
4. `daily_weekly_blend` — weighted combination of daily and weekly scores.
5. `ema_mean_reversion` — controlled counter-trend response to distance from daily EMA20 measured in ATR.
6. `macro_weekly_blend` — bounded blend that incorporates approved macro context where configured.

The tournament always produces a ranked research candidate, but the admission layer may still return `NO_TRADE`. A default/tie direction never overrides the NO_TRADE gate.

## Learning

Learning uses only closed earlier paper legs. For each instrument and market regime, the system records:

- selected method and direction;
- entry and exit;
- gross result;
- estimated transaction cost;
- net result;
- weekly regime;
- frozen risk plan.

Historical method performance is shrunk toward zero, capped and combined with a small exploration bonus. Contextual learning can also use frozen counterfactual candidate outcomes to reduce exploration uncertainty, while realized selected-strategy legs remain the source for the legacy global performance adjustment. The system does not increase notional after losses, rewrite closed history or optimise weights on the current bar.

## Daily analysis

At entry, re-entry and during the scheduled daily review, the weekly file stores:

- current open direction;
- selected method and direction;
- daily v2 score;
- weekly score and regime;
- learning adjustment;
- current mark and unrealised result when available;
- a short PL and EN explanation.

The latest review is shown in the public `Pozycje tygodniowe / Weekly positions` card.

## Validation

`backtest_multi_strategy_v4.py` performs a separate walk-forward comparison for every instrument and every candidate method. Method selection at a simulated date can use only outcomes from earlier dates. Costs and frozen ATR SL/TP levels are included.

The historical report approximates one selected trade per week with daily OHLC data. It does not reconstruct every live same-week 5-minute re-entry, so it must not be presented as live validation or a guarantee of profit.
