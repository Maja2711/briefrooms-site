# Stock Trading v2 – SEC XBRL Fundamentals (shadow)

This is a **working, deterministic, zero-LLM** research layer for the Stock
Trading v2 Opportunity Loop. It is not an automatic live-trading signal.

## What runs

```bash
python scripts/stock_trading_v2_fundamentals.py --market US \
  --frontier data/investments/stock_trading_v2_frontier/us.json \
  --output data/investments/stock_trading_v2_fundamentals/us.json
python scripts/stock_trading_v2_fundamentals.py --market US \
  --output data/investments/stock_trading_v2_fundamentals/us.json --verify
```

The GitHub research workflow runs it **after Dynamic Universe/Market Scan and
before Deep Evidence**, for the top ten US candidates. For GPW it emits an
explicit `UNSUPPORTED_SOURCE` status; it **does not claim SEC is a source of
Polish issuer financials**. A dedicated authoritative GPW ESEF/XBRL parser with
report-level validation is needed before GPW fundamental scoring.

The module downloads the public SEC ticker/CIK map and then one SEC
`companyfacts` JSON per matched US issuer. It computes revenue YoY, diluted
EPS YoY, operating/net margin, free cash flow (operating cash flow minus
property/plant/equipment capex), FCF margin, return on equity, and
liabilities/assets. It additionally reports **indicative** FY P/E, with an
explicit warning that share splits, ADR conversion and TTM alignment have
**not** been verified. Indicative P/E has **zero scoring authority**.

## Temporal and accounting controls

- Source: official `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`.
- Forms: SEC annual `10-K`, `10-K/A` (not 10-Q YTD values).
- Fiscal duration: 330–400 days, years must be separated by 330–400 days.
- SEC facts with filed date >= the UTC decision date are excluded: filing
  acceptance times are not included in these observations, so same-day values
  cannot be safely presumed available earlier in the day.
- Select the latest available restatement for **each** annual fiscal end.
  Preserve XBRL concept, unit, accession, form, filing date and period for
  every used observation.
- Missing concepts mean null metrics; no inferred zeros, fabricated quarters,
  mixing of different periods, or LLM-created accounting facts.
- Banks/financial companies receive `NOT_COMPARABLE` scores until an
  industry-specific fundamental scoring model is separately validated.
- Stale filings (>475 days from fiscal end) do not receive a score.
- Source data failures are `DATA_ERROR` or `NO_CIK`, not buy signals.
- SEC client uses a descriptive User-Agent, serial HTTPS, >=0.5-second spacing,
  a bounded response size and a short retry; no key and no token consumption.

The prototype score uses deliberately simple *research* weights for
revenue growth (25), diluted EPS growth (25), operating margin (20), FCF
margin (20), and ROE (10). The score is not statistically calibrated as
expected return, valuation, or probability of success.

A few deterministic anomaly flags propose **questions for a future selective
LLM review**, but this code does not invoke an LLM. Detection and explanation
are distinct.

## Wiring and explicit non-authority

`stock_trading_v2_fundamentals/{market}.json` has a content hash and an
exact `source_frontier_sha256`. Deep Evidence validates both before copying
the fundamental result into `candidates[].fundamental_analysis`.
Opportunity Engine forwards this object for observability in
`evaluated_candidates[].fundamental_analysis` without changing candidate
utility or any hard gate. Production bridge and canonical portfolio code are
unchanged; no automatic BUY/SELL/REPLACE or score promotion.

To grant fundamental metrics trading authority, first establish GPW/US sector
coverage and temporal quality, forward-test **prospective** results, quantify
net out-of-sample contribution, then pass a separate champion/challenger
promotion and risk review. Never retroactively enrich historical trade
decisions with current SEC facts.
