# BriefRooms weekly paper methodology v4

## Status

Version 4 is an experimental paper-trading research layer. It does not place broker orders and it does not replace the historical validation report for model v2.

## Mandatory exposure

From Monday 08:00 Europe/Warsaw until the scheduled Friday 22:00 close, the system attempts to maintain one paper position in each enabled instrument:

- EUR/USD
- S&P 500 futures
- BTC/USD

After SL, TP, daily thesis invalidation, strategy-direction change or a material-event close, the completed leg is archived. Re-entry is attempted after at least five minutes on the first completed 5-minute bar available to the workflow.

## Why an inverse signal is tested separately

A negative result for a short method does not prove that the corresponding long method is profitable. Transaction costs, timing, stop-losses, take-profits and asymmetric price behaviour can make both directions unprofitable. Therefore `base_v2` and `inverse_v2` are independent candidate methods and receive separate walk-forward results.

## Candidate methods

The strategy tournament evaluates:

1. `base_v2` — direction from the saved daily trend, momentum and breakout model.
2. `inverse_v2` — the opposite direction, tested as a separate hypothesis.
3. `weekly_trend` — direction from weekly EMA, momentum, breakout and candle structure.
4. `daily_weekly_blend` — weighted combination of daily and weekly scores.
5. `ema_mean_reversion` — controlled counter-trend response to distance from daily EMA20 measured in ATR.

A position is always directional. A neutral base signal is resolved by the tournament rather than published as no-trade.

## Learning

Learning uses only closed earlier paper legs. For each instrument and market regime, the system records:

- selected method and direction;
- entry and exit;
- gross result;
- estimated transaction cost;
- net result;
- weekly regime;
- frozen risk plan.

Historical method performance is shrunk toward zero, capped and combined with a small exploration bonus. The system does not increase notional after losses, rewrite closed history or optimise weights on the current bar.

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
