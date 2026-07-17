# BRACE — BriefRooms Adaptive Conviction Engine

BRACE is a transparent challenger model for the public **Inwestycje 10k / 10K Investing** portfolio. It runs in parallel with the baseline model and never places broker orders.

## Seven pillars

| Pillar | Weight | Main evidence |
|---|---:|---|
| Business quality | 20% | revenue growth, margins, ROE, free cash flow, leverage |
| Results & revisions | 20% | earnings growth, recent surprises, analyst consensus where available |
| Valuation attractiveness | 15% | forward/trailing P/E, PEG, sales multiple, FCF yield |
| Market confirmation | 15% | MA50/MA200, 6M momentum and relative strength |
| Risk resilience | 15% | volatility, drawdown, beta, leverage and concentration |
| Market context | 10% | S&P 500 trend, VIX, US 10Y yield and market regime |
| Events & information | 5% | relevant news with source quality and time decay |

## What is new

1. **Evidence Ledger** — every material input has a source, direction, strength, quality, observation date and half-life.
2. **Asymmetric evidence** — negative evidence counts 1.35 times as strongly as equal positive evidence.
3. **Contradiction penalty** — strong fundamentals with weak market confirmation, or strong momentum with weak risk resilience, triggers investigation rather than an automatic buy.
4. **Signal persistence** — an `ADD_REVIEW` requires a strong score in two consecutive reviews. A single good week is not enough.
5. **Earnings blackout** — the model does not recommend adding within five days of earnings; it switches to `WAIT_FOR_EVENT`.
6. **Thesis Clock** — each investment thesis is evaluated against an explicit time horizon rather than being allowed to remain permanently “early”.
7. **Decision quality vs outcome** — the decision is frozen when published, while 30- and 90-day relative outcomes are evaluated later without rewriting the original reasoning.

## Decisions

- `ADD_REVIEW` — a human should review a possible increase; it is not an order.
- `HOLD` — thesis remains intact without a sufficiently strong add or trim case.
- `HOLD_BUILD_EVIDENCE` — the setup is strong but has not persisted for two reviews.
- `WAIT_FOR_EVENT` — strong setup but earnings or another event is too close.
- `WAIT_INVESTIGATE` — signals conflict and require deeper analysis.
- `TRIM_REVIEW` — persistent deterioration or concentration requires review.
- `THESIS_REVIEW` — material risk or very low conviction; urgent analysis.
- `EXIT_REVIEW` — simultaneous business, results and risk failure; exit must be reviewed.

## Backtest rules

The historical test is deliberately called **BRACE-Lite**. Reliable point-in-time historical fundamentals are not reconstructed from current data. The test therefore uses only information available at each historical week:

- 26- and 52-week momentum,
- price versus a 40-week moving average,
- relative strength versus the benchmark,
- 13-week volatility,
- drawdown,
- benchmark trend, volatility and portfolio breadth.

Weights calculated at week `t` are applied only to returns from `t` to `t+1`. The test includes 0.25% transaction cost per unit of turnover, monthly rebalancing, cash when conviction is low, and three parameter variants. It reports CAGR, total return, volatility, Sharpe, Sortino, maximum drawdown, Calmar and turnover.

## Limitations

- The historical universe is the current portfolio. Delisted historical candidates are not reconstructed, so survivorship bias remains.
- Public data can be incomplete or temporarily unavailable. Missing fields reduce confidence instead of being replaced with optimistic assumptions.
- News headlines are review signals, not proof that a thesis is true or false.
- BRACE remains a challenger until it demonstrates stable out-of-sample behaviour and useful live decision quality.
