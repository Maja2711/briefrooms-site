# Free data sources in BriefRooms: research-only

## Verified live network probes (15 October 2026)

- https://www.checkmyswap.com/api/curves/all responded with valid JSON and USD/EUR curve entries. Each has currency, curve, date and fetchedAt; par-rate nodes contain tenor, rate, rateBps, method, trades and closeTrades.
- The dated USD and EUR endpoints for 2026-10-09 both returned 10 point curve arrays.
- The Twelve Data EUR/USD time_series 1min endpoint returned HTTP 401 without an API key.

## CheckMySwap

Source: https://www.checkmyswap.com/rates
Method: https://www.checkmyswap.com/methodology

The real API supplies a DATED archive of estimated PAR OIS rates, from approximately 1Y onward, not tradable bid/ask prices or 3M/6M/12M OIS discount factors. Never substitute an HTTP fetchedAt timestamp for a market observation timestamp. Thin-close, derived and all-day rates may be estimates.

The collector reads the public two-leg USD/EUR JSON and validates date consistency, tenor coverage, rate bounds, source methods and record counts. A snapshot older than 3 calendar days is marked stale for research. No curve prices are committed or published. Only metadata health, observation dates, tenors and accepted record counts are retained for 7 days.

This feed must NOT enter the 5% policy-differential live Evidence. It does not meet the existing 75-minute real-time OIS forward contract.

## Twelve Data

Source: https://twelvedata.com/forex
Official terms: https://support.twelvedata.com/en/articles/5332349-commercial-and-personal-usage
API: https://twelvedata.com/docs

On free Basic, personal/internal non-commercial testing is allowed but commercial use and redistribution are restricted. A BRs public website or commercial execution-price use is NOT authorized on Basic.

Implementation: GET https://api.twelvedata.com/time_series with query parameters symbol=EUR/USD, interval=1min, outputsize=3, timezone=UTC, order=desc. Authentication uses header Authorization: apikey [SECRET] rather than putting the credential in the URL.

Activation requires BOTH:
- GitHub repository secret TWELVE_DATA_RESEARCH_API_KEY: a real personal API key.
- GitHub repository variable TWELVE_DATA_RESEARCH_ALLOWED=1: explicit confirmation that the particular use is permitted internal testing and not production/commercial redistribution.

Absent either requirement, no Twelve Data API request is made. The response must contain the requested EUR/USD symbol, one-minute timestamps and sane closing prices; reported OHLC data are never used as executable bid/ask. No raw prices or secrets are committed to the website or actions artifacts.

## Workflow and safety

- Script: scripts/research_free_feeds.py
- Tests: tests/test_research_free_feeds.py
- Workflow: .github/workflows/research-free-feeds-discovery.yml
- Schedule: twice per weekday, manual dispatch also available
- Artifact: free-market-feed-research-health, 7-day retention

This is an ISOLATED read-only research monitor. It cannot write to or alter the Daily EUR/USD trade, EPE execution proof, displayed quote, or Belief/Epistemic Policy Differential. Missing or stale inputs fail closed, and source health is not a claim of viable execution pricing.

Actual production promotion would require a permitted license, genuine source timestamps and bid/ask, continuous feed observability and independent EPE tests.
