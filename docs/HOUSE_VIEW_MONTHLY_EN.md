# BriefRooms S&P 500 — automated monthly House View (research-only)

## Scope and authority
A monthly, source-based 1M/6M/12M macro/tactical risk balance in Investing / Long View. **Not** a complete S&P 500 valuation model, target price, trading recommendation, or statistically validated forecast. The original October 2026 editorial House View remains clearly identified as an archive.

**Module IDs:** `TR-08` for contextual consumption, `CT-05` for public display. Never write `TR-05` WES/Weekly execution or risk state, `EP-05`, `IN-03`, `IN-09`, positions, policy, or promotion metadata.

## Provenance and freshness
FRED CSV observations sourced from BEA GDP (`GDPC1`), headline/core PCE (`PCEPI`, `PCEPILFE`), Treasury 10Y (`DGS10`), and EIA Brent (`DCOILBRENTEU`). An optional frozen WES weekly SPX futures return `signals.ret20_pct` is **market context only**, not an executable signal. Observation dates are preserved and distinguished from retrieval/publishing time. GDP max observation age 200 days, PCE 100 days, 10Y 15 days, oil 22 days, weekly SPX 21 days. Missing/stale/implausible primary values, missing year-ago baseline, or future observation dates BLOCK publication, retaining the last frozen report.

## Published method and limits
`monthly_macro_risk_balance_v1` is a deterministic unvalidated heuristic, not an estimated probability.
- Growth q/q SAAR: +1 if ≥2%, -1 if <1%, -2 if <0%, else 0.
- Core PCE y/y: -2 if ≥3.5%, -1 if ≥2.7%, +1 if ≤2.3%, else 0.
- U.S. 10Y: -1 if ≥5%, +1 if ≤3.5%, else 0.
- Brent: -1 if ≥$110, +1 if ≤$70, else 0.
- S&P futures 20-session return: +1 if ≥3%, -1 if ≤-3%, else 0.
- `1M = 2×momentum + core + 10Y + Brent`; `unassessed` without fresh SPX observation.
- `6M = 2×growth + 2×core + 10Y + Brent`.
- `12M = growth + core + 10Y + Brent`.
- Score ≥2 is mildly positive, ≤-2 defensive, otherwise neutral; neutral is NOT a projected zero return.
- Fixed `conviction=3/10` is an explicit presentational cap, not Belief confidence.
- Without fresh earnings and valuation inputs, stronger medium-term conclusions are unsupported. WES, GSE, FSE and BRACE cross-checks remain independent read-only panels.

## Workflow, archival and rollback
`.github/workflows/house-view-monthly.yml` runs at 09:17 UTC on the 8th, 10th and 12th of each month, or manually. The publisher writes immutable `data/investments/house_view_monthly_archive/YYYY-MM.json` then `house_view_monthly_current.json`; reruns are idempotent and restore a missing pointer, but never silently overrule an existing pointer intentionally rolled back to an older month. Only these publication files are included in the commit. The bilingual browser reader shows the snapshot on all four relevant pages and flags it stale after 45 days. Failures are reported through GitHub Actions; no new report/date is silently invented.

Tests: `python -m unittest tests/test_house_view_monthly.py -v`; `node --check scripts/house-view-monthly.js`. To roll back publication, make a controlled commit restoring the last verified `current` snapshot; never rewrite a month's frozen archive.
