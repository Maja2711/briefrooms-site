# GSE v2 Historical Event Discovery & Verification Pipeline

## Goal

Expand the GSE v2 historical research base from a small hand-curated seed catalogue to at least **100 independent, machine-verified geopolitical clusters** without using later market performance to decide which events enter the sample.

The pipeline is research/shadow only. It cannot change GSE v1, Belief Core, WES, BRACE, portfolio weights or execution.

## Architecture

```text
Official primary-source archives
        ↓
Archive discovery
        ↓
Deterministic scenario pre-classification
        ↓
Primary-page fetch + date/title verification
        ↓
Machine-verified source documents
        ↓
Correlation-aware geopolitical clustering
        ↓
Effective historical event catalogue
        ↓
Historical market response builder
        ↓
GSE v2 regime-aware learning loop
```

## Primary sources

The first automated backfill uses:

- OFAC Sanctions List Updates,
- U.S. Treasury press releases,
- U.S. Department of Defense news/search archives.

Only allow-listed official government/institutional domains are accepted. Source adapters fail softly: an archive redesign cannot inject unverified data into the catalogue.

The source configuration is versioned in `data/gse/historical_event_sources.json`.

## Discovery versus verification

Discovery is deliberately permissive: archive titles are scanned for scenario actor/action combinations. A discovered document is **not** part of the historical catalogue yet.

Verification requires:

1. official allow-listed primary-source host,
2. successful fetch of the primary page,
3. parseable publication/event date inside the configured historical window,
4. title agreement between archive listing and primary page,
5. deterministic scenario classification above the minimum score,
6. aggregate verification score >= `0.78`.

Passing documents receive `verification_status=machine_verified_primary_source` and a hash of the fetched page. This is machine verification against primary evidence, not a claim of human editorial review.

## Scenario families

The discovery classifier maps only into the existing GSE families:

- Middle East energy escalation,
- Russia / Ukraine / Black Sea escalation,
- Red Sea shipping disruption,
- China / Taiwan trade or military escalation,
- sanctions escalation,
- grain export disruption.

Pure removals, delistings, settlements and other de-escalatory/administrative actions are excluded unless the same headline contains a separate positive escalation action.

## No outcome leakage

The discovery, verification, classification and clustering pipeline never reads market returns, forecast outcomes, Brier score, log loss or later GSE performance.

The effective catalogue explicitly asserts:

```text
market_outcomes_used_for_event_selection = false
automatic_tuning_enabled = false
decision_influence = false
```

Historical market outcomes are attached only **after** the event catalogue has been frozen for that cycle.

## Clustering

Multiple official documents can describe one geopolitical shock. The pipeline clusters verified documents by:

- dominant GSE scenario,
- dominant geopolitical actor bucket,
- publication/event proximity,
- title-token similarity.

Sanctions use a wider five-day correlation window; military/shipping escalation generally uses three days. A cluster contributes one `event_cluster_id` to effective N. This prevents a burst of press releases from pretending to be many independent geopolitical episodes.

## Event features

Each cluster receives deterministic ex-ante features used by the advanced v2 similarity model:

- severity,
- surprise,
- global scope,
- military relevance,
- energy relevance,
- shipping relevance,
- sanctions relevance,
- food relevance,
- China/Taiwan relevance.

These features are derived from the primary-source text and scenario family, not subsequent asset performance.

## Effective catalogue

The curated v2 catalogue remains the seed/ground-truth layer. Automatically verified clusters are merged into a private runtime catalogue:

`gse_historical_event_catalog_effective.json`

A machine event that overlaps a curated event by <=1 day and shares a scenario is discarded in favour of the curated anchor.

Target gate:

```text
verified_cluster_n >= 100
```

The discovery state additionally reports a balance diagnostic: at least 20 non-sanctions auto-clusters and at least four scenario families with five or more clusters. The count target and the balance diagnostic are reported separately so a large sanctions archive cannot silently masquerade as broad geopolitical coverage.

## Persistence

Private cumulative `gse-shadow-state` gains:

```text
gse_historical_event_candidates.jsonl
gse_historical_event_clusters.json
gse_historical_event_catalog_effective.json
gse_historical_discovery_state.json
```

Candidates are append-only once machine-verified. The effective cluster catalogue is deterministically rebuilt from the verified document ledger.

## Cadence

Workflow: `.github/workflows/gse-historical-discovery.yml`

- first deployment / target not met: full backfill from 2014,
- weekly: full refresh,
- subsequent non-full cycles: recent overlap window of 45 days,
- after a successful discovery cycle, historical response construction and the advanced GSE v2 learning loop are rerun on the effective catalogue.

The normal hourly GSE workflow does not recrawl historical archives. It consumes the latest effective catalogue restored from the cumulative artifact and falls back to the curated catalogue only if no effective catalogue exists yet.

## Fail-closed target

The dedicated backfill workflow uses `--require-target`. If fewer than 100 effective verified clusters exist after a backfill, the workflow fails rather than claiming that the expansion succeeded.

## Safety boundary

Hard disabled:

```text
automatic_tuning_enabled = false
decision_engine_connected = false
belief_core_connected = false
trade_execution_enabled = false
policy_output_enabled = false
market_outcomes_used_for_event_selection = false
```

Reaching 100+ clusters improves the research sample; it does not grant GSE v2 authority over WES or BRACE.
