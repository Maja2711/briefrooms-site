# BRACE — BriefRooms Adaptive Conviction Engine

BRACE is a transparent challenger model for the public **Inwestycje 10k / 10K Investing** portfolio. It runs in parallel with the baseline model and never places broker orders.

## Objective

The formal objective is to maximise five-year geometric return net of costs while controlling the risk of permanent capital loss. The model remains subject to no leverage, no CFDs, concentration limits, a target maximum drawdown and mandatory human approval.

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

## Decision framework

1. **Evidence Ledger** — every material input has a source, direction, strength, quality, observation date and half-life.
2. **Asymmetric evidence** — negative evidence counts 1.35 times as strongly as equal positive evidence.
3. **Contradiction penalty** — strong fundamentals with weak market confirmation, or strong momentum with weak risk resilience, triggers investigation rather than an automatic buy.
4. **Signal persistence** — an `ADD_REVIEW` requires a strong score in two consecutive reviews. A single good week is not enough.
5. **Earnings blackout** — the model does not recommend adding within five days of earnings; it switches to `WAIT_FOR_EVENT`.
6. **Thesis Clock** — each investment thesis is evaluated against an explicit time horizon rather than being allowed to remain permanently “early”.
7. **Decision quality vs outcome** — the published decision is frozen and evaluated later without rewriting the original reasoning.

## BRACE 2.0 learning architecture

### Immutable Decision Memory

Every weekly position decision is stored in `portfolio_10k_brace_memory.json` with:

- score and confidence,
- market regime,
- pillar scores and contradictions,
- reference asset and benchmark prices,
- the complete published evidence snapshot,
- the decision that would have occurred without each evidence item.

A deterministic decision ID prevents duplicate publication. Once stored, the decision record is not edited. Later results are written as separate append-only outcome events.

### Multi-Horizon Outcome Engine

Each decision is evaluated at the first weekly review after:

- 7 days,
- 30 days,
- 90 days,
- 180 days,
- 365 days.

The engine records asset return, benchmark return, excess return, decision outcome and evidence-level attribution. Short-term outcomes receive less learning weight than 90-, 180- and 365-day outcomes.

### Counterfactual Evidence Attribution

For every published evidence item BRACE performs a local ablation test:

> What score and decision would have been produced if this single item had not been available?

The model records:

- marginal score contribution,
- alternative decision,
- whether the evidence was pivotal,
- an attribution importance value.

Evidence that actually changed the decision receives more credit or blame than evidence that was merely present.

### Adaptive Evidence Learning

Reliability is learned separately for each evidence code and, when sufficient data exist, for the combination:

`evidence code × asset type × market regime`

The learning rule is Bayesian:

- every signal starts with a neutral Beta prior,
- correct evidence increases posterior reliability,
- incorrect evidence reduces posterior reliability,
- neutral excess returns do not update reliability,
- counterfactual importance controls update size.

The learned reliability multiplier remains exactly `1.00` until at least eight effective observations are collected. It then matures gradually and is permanently bounded to `0.80–1.20`.

These multipliers may adjust only the influence of evidence. BRACE does **not** autonomously change pillar weights, decision thresholds, portfolio limits or execute orders.

### One-Review Delay

Outcome events observed during the current review update reliability for the **next** review. This prevents a signal from using its own current-period outcome to rewrite the decision being published at the same time.

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

Weights calculated at week `t` are applied only to returns from `t` to `t+1`. Weights drift naturally between monthly rebalances. Buy & Hold is funded once and is not periodically reset to target weights. The test includes 0.25% transaction cost per unit of turnover, cash when conviction is low, and three parameter variants. It reports CAGR, total return, volatility, Sharpe, Sortino, maximum drawdown, Calmar and turnover.

For live instruments with insufficient history, the test uses explicitly disclosed economic proxies only in the historical layer:

- `FWIA.DE` → `VT`,
- `ZPRV.DE` → `VBR`,
- `NOVO-B.CO` → `NVO`.

These proxies do not change the live holdings or execution records and do not perfectly reproduce UCITS structure, currency, taxation or tracking differences.

## Champion–challenger promotion gate

BRACE does not become the official model merely because it is newer or more complicated. The baseline remains champion until the challenger passes every pre-defined test:

- at least 260 weekly observations,
- CAGR at least 0.5 percentage point above baseline,
- Sharpe ratio no lower than baseline,
- maximum drawdown no more than 2 percentage points worse,
- stable results across conservative, standard and aggressive parameter variants.

Failing any criterion produces `not_promoted`. Parameters are not retuned after seeing the result simply to make BRACE win. Even a full historical pass only makes BRACE eligible for live-shadow confirmation; it does not automatically replace the official process.

## Limitations

- Learning initially has no active multipliers because real outcome history must first accumulate.
- Counterfactual attribution is a transparent local ablation of published evidence, not proof of economic causality.
- The historical universe is the current portfolio. Delisted historical candidates are not reconstructed, so survivorship bias remains.
- Public data can be incomplete or temporarily unavailable. Missing fields reduce confidence instead of being replaced with optimistic assumptions.
- News headlines are review signals, not proof that a thesis is true or false.
- BRACE remains a challenger until it demonstrates stable out-of-sample behaviour and useful live decision quality.
