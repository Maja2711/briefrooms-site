# BRACE-SPX Research Lab

BRACE-SPX is a research-only laboratory for one traded instrument: **SPY / S&P 500 exposure**.

The project borrows the useful parts of AlphaGo/AlphaZero:

- one clearly defined environment and reward;
- repeated champion–challenger competition;
- a persistent experiment ledger;
- many independent trials instead of one hand-tuned strategy;
- promotion only after out-of-sample progress.

It does **not** copy the fiction that a financial market is a stationary board game. Market rules, participants and regimes change, so the system is built around chronological validation, costs, risk controls and a sealed holdout.

## Research independence

BRACE-SPX is hypothesis-agnostic. Human suggestions, macro data, event intelligence, indicators, model families and trading rules are optional challengers, never required components.

A component survives only when it adds stable incremental value over stronger baselines outside the sample used to design it. If performance is statistically indistinguishable, the simpler and more reproducible candidate wins. No feature family, model class or narrative is privileged.

## Research loop

1. Download the longest reliable SPY history plus exogenous market features.
2. Build price, trend, momentum, volatility, drawdown, VIX, rates, credit, dollar and sector-breadth features.
3. Evaluate genuinely new candidates on purged chronological walk-forward folds.
4. Compare each candidate with buy-and-hold, a 200-day trend filter and a dual-trend baseline.
5. Calculate conventional excess-return Sharpe and multiple-testing controls, including Deflated Sharpe Ratio and Probability of Backtest Overfitting.
6. Keep the final four-year holdout sealed during development and open it only once for a predeclared generation.
7. Mark a candidate as `wow_candidate` only when the one-time holdout shows a material improvement, not a cosmetic one.
8. Preserve all experiment results in an immutable-style ledger for future search.

## “Wow” gate

A candidate must satisfy all of the following on the genuinely sealed holdout:

- CAGR improvement of at least 2 percentage points **or** Sharpe improvement of at least 0.25;
- maximum drawdown no more than 2 percentage points worse;
- Calmar improvement of at least 0.10;
- positive return in at least 60% of calendar years;
- annualized turnover no higher than 3.0;
- no unresolved statistical-governance blocker.

Passing the gate does not activate live trading. Human review remains mandatory.

## Safety

- one traded instrument, no leverage;
- no automatic orders;
- transaction costs included;
- no random train/test shuffle;
- signals at month `t` apply only to month `t+1`;
- research output is isolated under `data/research/`;
- the live BriefRooms portfolio is never modified by this workflow.
