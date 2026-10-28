# Current US sector recovery

Modules: TR-04, CF-01, LE-09. This is an additional collection path for the
existing sourced-metadata ledger, not another risk engine or policy writer.

## Failure and source selection

The bulk Nasdaq screener timed out, leaving the three current US holdings
without verified sector metadata. The official small endpoints work:

- `https://api.nasdaq.com/api/company/{listing_symbol}/company-profile`
- `https://api.nasdaq.com/api/quote/{listing_symbol}/summary?assetclass=stocks`

`stock_trading_us_sector_collector.py` runs before the established metadata
collector in the main production and portfolio workflows. It requires complete
HTTP 200 JSON responses with Nasdaq status 200, exact listing symbols, a company
name matching the official Nasdaq Trader universe, matching exchange, stock
asset class, and consistent explicit sector fields from both endpoints.
Different exchanges, issuers, incomplete responses and unknown or contradictory
classifications cannot become observations.

This is a separate access path within the same Nasdaq provider. It does not
claim independence from a complete provider outage. A cached classification
remains subject to the existing 30-day validity boundary. Source failure does
not erase a valid cached record and permits the established screener collector
to try its normal path afterward.

## Data contract

The collector appends to `stock_trading_sourced_metadata.json`; all existing
record contents and IDs are preserved. It retains the URL and SHA-256 of both
responses, actual receipt times, original reported sectors and exchange.
Source spelling normalization is explicit and keeps Nasdaq's spaced labels,
including `Health Care`; it is not a cross-provider or inferred GICS mapping.

Collection is bounded to six US issuers and two eight-second requests per issuer.
Current holdings have priority, followed by eligible names from the optional
production opportunity frontier, without duplicate symbol requests. Candidate
observations enter the same ledger and can be consumed by the existing
pre-admission concentration gate. Candidates outside that collection budget
retain the established explicit UNKNOWN policy; this does not claim complete
universe coverage. Observations younger than 24 hours are reused without a provider
request. A separate derived `stock_trading_us_sector_collection.json` report
shows collected, reused, unavailable and retained observations. Partial source
failure remains visible. Existing ledger corruption fails before persistence.

The established metadata validator and current risk view consume these receipts
unchanged. Replay receives the same frozen ledger through native input frames;
it does not invoke this collector. No entry price, position, stop, target,
historical sector or historical cost is rewritten. The 25 audited CASH source
files remain byte-identical, so this additional input collector does not retire
the existing eight v2 experiments or consume a new set of trial registrations.

## Safe operational refresh

The main-only manual `Stock Trading Risk Metadata Refresh` workflow collects
classification and regenerates the existing risk coverage report under
`stock-trading-state-writer`. It checks portfolio, policy, manifest, registry
and production runtime bytes before persisting only the ledger and reports.
It invokes no admission, lifecycle, fills or learning experiment registration.
The final check requires all currently open US holdings to have verified
classifications in the actual risk view. Reports are retained as an artifact.
The existing derived coverage report is regenerated after deployment rather
than replaced by a stale PR snapshot while the live portfolio keeps updating.

## Acceptance

For the three holdings present during the repair, both source endpoints
explicitly classify SAP and NXPI as `Technology`, and MCK as `Health Care`.
The risk view must report two technology positions, one health-care position,
and zero unknown US sectors. A third verified technology candidate must fail
the existing concentration gate, while original portfolio and history bytes
remain unchanged.

Tests cover cache reuse, endpoint outage/fallback, wrong ticker/name/exchange,
contradictory and malformed sectors, receipt times, stale/future observations,
source hashes, unchanged original records and actual risk-gate consumption.
The existing 25-file CASH contract and production-code fingerprint are compared
before and after the repair. This task does not alter PRE/REGULAR, the lifetime
24-test budget or any execution policy.
