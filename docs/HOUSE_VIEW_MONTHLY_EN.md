# BriefRooms House View Intelligence v2 — monthly S&P 500 (EN)

## Purpose and boundaries
TR-08 (Long View / BRACE-SPX) and CT-05 (public projection) publish a **complete analysis structure** monthly in the existing Investing room: 1M/6M/12M thesis, macro observations, rationale, earnings/valuation section, conditional scenarios, monthly changes, model disagreements, thesis-change gates, source provenance and coverage gaps. Polish and English share the same structure.

v2 is deterministic source-bounded synthesis, **not** an LLM editor, a validated probability forecast, a valuation model or investment advice. Missing source data is disclosed; it is never replaced by invented consensus.

## Implementation
- Workflow: .github/workflows/house-view-monthly.yml, UTC 09:17 on day 8, 10 and 12; manual workflow_dispatch supported.
- Macro: scripts/publish_monthly_house_view.py retains the fixed v1 1M/6M/12M factor balance. Mandatory FRED GDPC1 (max age 200d), PCEPI/PCEPILFE (100d), DGS10 (15d), DCOILBRENTEU (22d). Headline and core PCE must cover the same month. Missing, implausible, future or stale => NO PUBLICATION.
- Synthesis: scripts/house_view_research_v2.py consumes existing read-only GSE (3d), FSE (7d), BRACE-SPX (14d) and optional frozen WES SPX (14d) projections; nonqualifying observations cannot be treated as current.
- UI: scripts/house-view-monthly.js and assets/house-view-monthly.css render the entire current article in the established design. The October 2026 editorial is inside an expandable archive, open if no verified new report exists.
- Immutable monthly files: data/investments/house_view_monthly_archive/YYYY-MM.json and house_view_monthly_current.json pointer. A frozen v1 month and a deliberately rolled-back pointer are never overwritten.

## Existing engine authority
- **WES**: only verified frozen SPX futures 20-session return ret20_pct contributes to the baseline 1M balance. Forecast freeze time is not a market tick (price_tick_at=null).
- **GSE**: fresh SPX/720h source, at least 10 effective clusters, research_only=true, decision_influence=false. DOWN with model reading >=0.60 yields only a **geopolitical caution flag**, not a calibrated index downside probability.
- **FSE**: SPX risk_score/regime; caution at risk >=0.65 or regime TURBULENT. ABSTAIN stays no opinion. SPY price is not passed off as an SPX index value.
- **BRACE-SPX**: research/candidate and promotion gate only; no single authorized direction without single_champion_authorized. Challenger status cannot vote in the House View.
- **Belief Core**: private SPX bridges do not expose a qualified complete public point-in-time contract for this report. Missing coverage is disclosed; no probabilities are reconstructed and no private artifacts are read.
- **Conflicts**: overlapping signals and mismatched horizons are described without averaging, duplicate evidence-counting or turning cross-checks into trading vetoes.

## EPS and valuation — explicit unautomated coverage gap
This pipeline has no approved automated index-wide EPS consensus, earnings revisions or forward P/E feed. It must not republish October FactSet numbers, single-stock figures, SPY pricing or invented index targets as current.

An optional **manually reviewed** file data/investments/house_view_fundamentals.json (schema 1.0) may provide timezone-aware generated_at and source_published_at, source_document_url HTTPS on insight.factset.com / www.spglobal.com / www.yardeni.com, and numeric forward_pe and forward_eps_growth_pct (max age 45d). Missing, stale or unapproved documents yield earnings_valuation=unavailable and no strong 6M/12M fundamental thesis. This is curated evidence, not a live feed.

## Provenance and safety
Sections carry source_ids; local engine projections carry SHA-256 hashes, observation timestamps and freshness status; macro values carry dated FRED source URLs. Each month's evidence is frozen. Conditional scenarios do not invent probabilities. Conviction 3/10 is a cap on unvalidated heuristic claims, not Belief confidence. The UI warns on reports over 45 days old.

Checks: python -m unittest tests/test_house_view_monthly.py tests/test_house_view_research_v2.py -v; Python compilation; node --check scripts/house-view-monthly.js; Architecture Bootstrap Guard.

No WES, BRACE, GSE, FSE, Belief, risk-policy, positions, orders, research promotion or other production state writeback. Factor scoring weights cannot self-modify. Rollback changes current pointer or reverts the code, never edits frozen monthly archives.

## Investor-facing editorial v2.1
The system may not stop at listing raw indicators or GSE/FSE/BRACE statuses. Every section must explain **what the observation implies for the S&P 500, the relevant horizon and what would reverse the thesis**:
- Rates/energy: the 10-year yield affects the cost of capital and P/E multiples; Brent affects inflation, costs and margins. Conditional implications are separated from unsupported predictions of the next Fed decision.
- EPS/valuation: without verified index EPS consensus and forward P/E, no assumption of index gains solely through valuation expansion. Missing coverage has a stated 6M/12M investment implication rather than a technical placeholder.
- Momentum: a 20-session decline means correction risk, not proof of a bottom; positive momentum supports a rebound without establishing a 12M bull market.
- The 1M/6M/12M cards show plain-language directional meaning, with raw score and 3/10 heuristic strength moved to expanded methodological details.
- Conditional scenarios and revision gates state concrete downside/upside drivers without invented probabilities.

**The archive remains immutable.** November 2026 was previously frozen with technical prose. The v2.1 frontend derives only a new *display interpretation* from the same frozen inputs, biases and disclosed risk flags; it does not mutate the underlying JSON or fetch replacement model values. The presentation change is visibly versioned v2.1. Future monthly reports also receive investor-first wording directly from _investment_editorial_sections in scripts/house_view_research_v2.py. PL/EN follow equivalent rules without any claim of validated forecasting skill.

Additional test: node tests/test_house_view_editorial_ui.cjs replays the frozen November report in Polish and English, checks investor-facing language, no raw engine flags in the main text, immutable archive, and fail-closed behavior for incomplete macro data.
