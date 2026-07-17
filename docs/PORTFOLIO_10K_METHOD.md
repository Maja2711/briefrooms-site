# BriefRooms 10K model portfolio — method

## Purpose

The public portfolio starts with PLN 10,000 and a roughly five-year horizon. Its objective is capital growth with moderately high risk. It uses unlevered stocks and UCITS ETFs available in XTB. CFDs and automatic broker execution are excluded.

## Initial allocation

- FWIA.DE — 25%
- ZPRV.DE — 10%
- GOOGL.US — 15%
- AMZN.US — 15%
- TSM.US — 12%
- V.US — 10%
- SPGI.US — 8%
- NOVOB.DK — 5%

The first successful workflow run freezes the public model's entry close, FX rate and fractional number of units. This is a model execution, not proof of a fill in an XTB account. The model assumes a 0.5% FX conversion charge on each foreign-currency purchase. Dividends are recorded gross before withholding tax, because the actual tax depends on instrument domicile and investor circumstances.

## Weekly review

The workflow runs on Sunday and checks:

1. latest available market close and PLN conversion;
2. portfolio and benchmark return;
3. 50- and 200-session trend;
4. six-month momentum, 20-session volatility and drawdown from the one-year high;
5. next reported earnings date when the source provides it;
6. recent public headlines and explicit risk keywords;
7. actual weight versus target weight.

The model produces one of four review flags:

- `HOLD` — no threshold requiring action;
- `ADD_REVIEW` — underweight position with strong market confirmation;
- `TRIM_REVIEW` — material concentration above the target band;
- `THESIS_REVIEW` — weak signal set or multiple material risk headlines.

A flag is never an automatic order. Rotation requires a human review of primary sources and the original investment thesis.

## Rotation principles

A falling price alone is not a reason to sell. Reduction is considered after thesis failure, persistent business deterioration, material regulatory risk or excessive portfolio concentration. Target weights are guides. A single stock should normally remain below 18% and the broad global ETF below 30%.

## Data integrity

- Missing data is displayed as `—`, never as artificial zero.
- Entry records are frozen after initialization.
- Weekly snapshots and decision-journal entries are retained.
- The public page identifies external data sources, cost assumptions and explains that broker BID/ASK may differ.
- The model does not claim that a technical score is a probability of profit.
