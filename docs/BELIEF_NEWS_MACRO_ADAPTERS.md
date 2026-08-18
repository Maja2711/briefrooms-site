# Belief Core — News/Event + Macro/Event Calendar Adapters v1

## Purpose

These adapters extend the shared Belief Core evidence layer from market/technical data into primary-source text and scheduled macro events.

The contract stays unchanged:

```text
PRIMARY SOURCE
    -> Observation
    -> deterministic or governed LLM interpretation
    -> explicit EvidenceAssessment
    -> observation_to_evidence()
    -> Belief Core Evidence
```

The LLM is **never the factual source**. A Federal Reserve, BLS, BEA or SEC document is stored first as an immutable primary-source Observation with its source URL and observed timestamp. Gemini can only create a derived interpretation of that Observation.

If the model is unavailable, returns invalid JSON, selects a non-allowlisted belief, has low interpretation confidence, or judges the event immaterial, the primary Observation remains stored but no LLM Evidence is created.

## 1. News/Event Adapter v1

File: `scripts/belief_news_event_adapter.py`

### Initial primary sources

- Federal Reserve official press-release RSS.
- Federal Reserve official speeches/testimony RSS.
- BLS official latest-release RSS.
- BEA official current-releases page.
- SEC EDGAR submissions API and primary filing documents for watched U.S. stocks.

The SEC watchlist is built from:

1. `BELIEF_EVENT_TICKERS`, if configured, and
2. active U.S. stock positions in `data/investments/portfolio_10k_usd.json`.

The initial SEC form allowlist is:

- 8-K,
- 10-Q,
- 10-K,
- 6-K,
- 20-F,
- 40-F.

`SEC_USER_AGENT` may be configured in the workflow/environment for a more specific SEC contact-identifying user agent. If it is absent, the adapter uses the general BriefRooms research user agent.

### Primary Observation

Every fetched source document is first converted to:

```text
adapter = news_event
metric = primary_event_document
source_type = primary
source_ref = original official URL
independence_cluster = primary-event:<original URL>
```

The extracted document text is stored only in the private Belief Core runtime artifact, not in the public repository or GitHub Pages output.

## 2. Governed Gemini interpretation

File: `scripts/belief_llm_interpreter.py`

The adapter reuses the repository's existing governed Gemini runtime (`sitecustomize.py` + `comment_quality.py`) and the existing GitHub Actions secret `GEMINI_API_KEY`.

The model is constrained to the existing shared belief allowlist:

- `spx.trend.bullish`,
- `spx.breadth.healthy`,
- `spx.volatility.benign`,
- `spx.liquidity.supportive`,
- `spx.financial_conditions.supportive`.

It may also return `belief_id = none`.

The structured interpretation contains:

- belief ID or none,
- support/contradict direction,
- strength,
- interpretation confidence,
- broad-market materiality,
- event type,
- market scope,
- expected evidence horizon,
- concise interpretation,
- strongest alternative hypothesis.

### Fail-closed evidence gate

An LLM result creates Evidence only when all of these hold:

- source Observation status is `ok`,
- belief ID is allowlisted,
- direction is valid,
- interpretation confidence is at least 0.68,
- broad-market materiality is at least 0.55,
- effective strength is non-trivial.

Source reliability is **not** replaced by model confidence. Model confidence and materiality scale the derived evidence strength and remain separately auditable in metadata.

A company filing normally maps to `none` unless the supplied document plausibly changes a broad-market belief. This prevents a single portfolio holding's filing from automatically becoming an SPX-level signal.

### Provenance

The chain is explicit:

```text
Official document URL
    -> primary Observation ID
    -> Gemini-derived Observation
    -> Evidence
```

The Evidence keeps the primary Observation ID and original `source_ref` in metadata. Its independence cluster remains the primary event cluster so that repeated interpretations of the same source cannot become statistically independent evidence.

## 3. Macro/Event Calendar Adapter v1

File: `scripts/belief_macro_calendar_adapter.py`

### Initial calendar sources

- BLS official `bls.ics` release calendar.
- BEA official release schedule.
- Federal Reserve FOMC meeting calendar.

Each scheduled event is a primary Observation with:

- official source,
- source URL,
- stable event UID,
- event timestamp/date,
- time precision,
- importance level,
- hours until event.

### Importance classification

Initial high-impact families include:

- CPI,
- Employment Situation,
- FOMC,
- GDP,
- Personal Income and Outlays / PCE.

Medium-impact families include PPI, JOLTS, ECI, productivity/costs and international trade.

Importance classification is deterministic and does not predict the release value.

### Scheduled event-risk Evidence

An imminent high-impact event may create a **small deterministic contradiction** to `spx.volatility.benign`.

This means only:

> a major binary/information event is close, so the assumption that volatility remains benign deserves less conviction.

It does **not** mean the adapter predicts CPI, payrolls, GDP or the Fed decision direction.

For exact-time releases, the event-risk window is the final six hours before release. FOMC calendar dates are treated as `date_only`; the code uses 14:00 ET only as an internal scheduling anchor and explicitly records that this is not a timestamp sourced from the meeting calendar.

## 4. Runtime and cadence

File: `scripts/belief_external_live.py`

Workflow: `.github/workflows/belief-core-events-live.yml`

The event workflow runs every 30 minutes on U.S. weekdays over a UTC window covering U.S. pre-market through early evening across daylight-saving changes.

It uses the same cumulative private artifact as market-data collection:

```text
belief-core-shadow-state
```

and the same GitHub Actions concurrency group:

```text
belief-core-live-shadow
```

This serializes the market and external-evidence writers. A News/Macro cycle therefore cannot overwrite the state while the market-data cycle is freezing or verifying forecasts.

Only previously unseen primary event Observations are sent to Gemini. A completed `none` classification is remembered so the same source does not consume model quota every 30 minutes. If Gemini is unavailable, the source is not marked interpreted and can be retried later.

## 5. Frozen Forecast + Verification integration

No second forecast system is introduced.

Belief Core v2 already has:

```text
Belief State
    -> capture_forecast()
    -> ForecastSnapshot
       - predicted probability
       - confidence
       - forecast timestamp
       - target timestamp
       - regime
       - representative evidence IDs
       - full frozen evidence_snapshot
    -> verify_forecast()
    -> Verification
       - frozen probability
       - outcome
       - Brier score
       - log loss
       - same frozen evidence_snapshot
    -> Calibration Engine
```

Therefore, when News/Event or Macro/Event Evidence is representative at 10:00, 13:00, 16:00, or the WES weekly freeze, it is automatically captured inside the forecast's frozen evidence snapshot.

Later evidence cannot rewrite that forecast snapshot. Verification scores the original probability and keeps the original evidence snapshot.

`tests/test_belief_core_news_macro.py` includes an explicit regression test proving that a Gemini-derived Fed event Evidence enters a frozen forecast and remains byte-for-byte the same evidence snapshot in the later Verification object.

## 6. Safety boundaries

These adapters cannot:

- place orders,
- size positions,
- create policy output,
- alter BRACE/WES/BRACE-SPX decisions,
- auto-tune source reliability,
- rewrite a frozen forecast,
- verify a forecast before its target time under normal operation.

Belief Core remains shadow-only.

## 7. v1 limitations and next improvements

This is intentionally a narrow first production/shadow version.

Next improvements after stable collection should include:

1. richer primary company IR / earnings-release sources rather than relying only on SEC filings,
2. official earnings calendars with exact timestamps where licensing/access permits,
3. stronger source-document extraction for PDFs/tables,
4. a Data Quality adapter that records source latency and fetch failures as first-class telemetry,
5. historical event-type calibration: Fed, CPI, jobs, earnings/guidance and SEC filing evidence scored separately,
6. entity-level beliefs before allowing single-company news to influence engine-specific decisions.

Do not broaden the LLM allowlist or increase event evidence strength until enough frozen outcomes exist to measure calibration by event type and source.
