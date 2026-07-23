# BRACE-SPX Research Lab

BRACE-SPX is a research-only laboratory for one traded instrument: **SPY / S&P 500 exposure**.

The project borrows the useful parts of AlphaGo/AlphaZero:

- one clearly defined environment and reward;
- repeated champion–challenger competition;
- a persistent experiment ledger;
- many independent trials instead of one hand-tuned strategy;
- promotion only after out-of-sample progress.

It does **not** copy the fiction that a financial market is a stationary board game. Market rules, participants and regimes change, so the system is built around chronological validation, costs, risk controls and a sealed holdout.

## Research loop

1. Download the longest reliable SPY history plus exogenous market features.
2. Build price, trend, momentum, volatility, drawdown, VIX, rates, credit, dollar and sector-breadth features.
3. Evaluate new candidates on purged chronological walk-forward folds.
4. Compare each candidate with buy-and-hold, a 200-day trend filter and a dual-trend baseline.
5. Evaluate the sealed four-year holdout only after a candidate passes the development robustness gate.
6. Mark a candidate as `wow_candidate` only when the holdout shows a material improvement, not a cosmetic one.
7. Preserve all experiment results in an immutable-style ledger for future search.

## “Wow” gate

A candidate must satisfy all of the following on the sealed holdout:

- CAGR improvement of at least 2 percentage points **or** Sharpe improvement of at least 0.25;
- maximum drawdown no more than 2 percentage points worse;
- Calmar improvement of at least 0.10;
- positive return in at least 60% of calendar years;
- annualized turnover no higher than 3.0.

Passing the gate does not activate live trading. Human review remains mandatory.

## Safety

- one traded instrument, no leverage;
- no automatic orders;
- transaction costs included;
- no random train/test shuffle;
- signals at month `t` apply only to month `t+1`;
- research output is isolated under `data/research/`;
- the live BriefRooms portfolio is never modified by this workflow.
