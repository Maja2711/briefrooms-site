# BriefRooms Investing Method Changelog

## v1.0.0 — initial rule-based model

Initial weekly directional model for:

- EUR/USD
- S&P 500 futures

Core rules:

- forecast published on Sunday,
- entry price captured on Monday morning, Europe/Warsaw time,
- exit price captured on Friday evening after the weekly close,
- result marked as profit, loss, flat/no result or no trade,
- method version saved with every forecast,
- past forecasts should not be rewritten after results are known.

Signal blocks:

- trend,
- momentum,
- volatility regime,
- manual macro bias,
- manual event-risk bias.

This is an educational market-analysis framework, not financial advice.
