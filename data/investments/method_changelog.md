# BriefRooms Investing Method Changelog

## v2.0.1 — once-daily thesis review for open positions

Added one rule-based review of every open weekly position per trading day.

Rules:

- review is attempted once daily after 23:00 Europe/Warsaw,
- existing SL/TP monitoring remains active independently and can close earlier,
- the full v2 signal is recalculated from fresh market data,
- a position closes on a confirmed full signal in the opposite direction,
- a long also closes when score is at most -15 with at least two negative signal groups,
- a short also closes when score is at least +15 with at least two positive signal groups,
- missing or poor-quality data never causes an automatic close,
- execution uses the last completed 5-minute bar, not an arbitrary current-price fallback,
- every keep, close or deferred-close decision is written to the weekly audit trail,
- no same-week re-entry is allowed after a daily-review close,
- the historical validation now includes an approximation of the saved daily review rule.

## v2.0.0 — frozen forecasts, real volatility input and validation gate

The legacy prototype was audited and replaced for new forecasts.

Main corrections:

- the forecast is frozen before the trading week and receives a SHA-256 audit hash,
- only directional signals can receive an entry; neutral scenarios never have entry or exit prices,
- entry is the first available 5-minute bar at or after Monday 08:00 Europe/Warsaw,
- scheduled close is the first available 5-minute bar at or after Friday 22:00 Europe/Warsaw,
- current prices are not used as late substitutes for missed entry or close timestamps,
- volatility is used in the signal score and in SL/TP distances frozen before entry,
- trend, momentum and breakout require two-of-three directional agreement,
- signal strength is not described as a probability,
- when SL and TP are both inside one 5-minute bar, the model records stop loss first as the conservative assumption,
- re-entry after stop loss is disabled,
- closed v2 weeks are sealed by a hash manifest and cannot be silently rewritten,
- a fixed-rule five-year validation is run with saved transaction-cost assumptions.

The first v2 historical validation did not support opening new EUR/USD or BTC/USD positions. Both instruments are disabled instead of tuning parameters after seeing the result. S&P 500 futures remains enabled only for provisional paper trading and still requires a separate live paper-trading sample before the model may be described as validated.

## v1.1.0 — intraweek model-scenario review

Added a rule-based intraweek review layer for the educational market log.

New behavior:

- a weekly model scenario can be ended before the planned Friday finish,
- the log records the end price, timestamp, data source and reason,
- the result is calculated immediately for that instrument,
- wording is kept as a model scenario log: "scenario ended early".

Initial intraweek thresholds:

- EUR/USD: positive model move 90 pips; negative model move 60 pips,
- S&P 500 futures: positive model move 120 points; negative model move 80 points,
- a fresh signal reversal can also end the scenario when the reverse score is strong enough.

## v1.0.0 — initial rule-based model

Initial weekly directional model for:

- EUR/USD
- S&P 500 futures

Core rules:

- forecast published on Sunday,
- entry price captured on Monday morning, Europe/Warsaw time,
- planned exit price captured on Friday evening after the weekly close,
- result marked as profit, loss, flat/no result or no scenario,
- method version saved with every forecast,
- past forecasts should not be rewritten after results are known.

Signal blocks:

- trend,
- momentum,
- volatility regime,
- manual macro bias,
- manual event-risk bias.
