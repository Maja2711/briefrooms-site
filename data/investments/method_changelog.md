# BriefRooms Investing Method Changelog

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
