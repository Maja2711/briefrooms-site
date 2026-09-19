# BriefRooms Canonical Architecture Map — EN

**Map version:** 1.5  
**Snapshot date:** 2026-09-19  
**Base `main` commit:** `331314840c1f1ddeccd814b46df688ccf9971d2b`  
**Repository:** `Maja2711/briefrooms-site`

## 0. Purpose of this document

This document is the **canonical navigation map of the BriefRooms architecture**. It answers: which modules exist, which component owns decision authority, how data flows, where code and documentation live, and which safety boundaries are non-negotiable.

Runtime code remains the implementation source of truth. If the map and code diverge, that is **architecture drift** and either the map or implementation must be corrected. The map does not replace detailed module documents; it places them in the system-wide architecture.

### Mandatory maintenance rule

Every PR that changes BriefRooms architecture MUST update, in the same PR:

- `docs/ARCHITECTURE_MAP_EN.md`,
- `docs/ARCHITECTURE_MAP_PL.md`.

Architecture changes include, in particular: a changed contract, authority boundary, data-flow boundary, adapter, engine, learning/verification loop, persistence semantics, production/shadow boundary, promotion/rollback rule, or safety invariant.

### Mandatory AI / agent bootstrap

Every AI/coding agent making an architecture or investment-logic change must start from repository-root `AGENTS.md`, then open this map, identify the affected `module_id` set, read the referenced detailed documentation, and only then inspect the code. Architecture must not be reconstructed from conversational memory, and a new subsystem must not be created before checking for existing overlapping capability. `scripts/validate_architecture_bootstrap.py` and the `Architecture Bootstrap Guard` workflow protect this mechanism from silent removal or PL/EN map-version drift.

## 1. L0 — whole-system map

```text
EXTERNAL / PRIMARY / MARKET SOURCES
        |
        v
SOURCE + EVIDENCE ADAPTERS
        |
        v
OBSERVATIONS / NORMALIZED EVIDENCE
        |
        +-------------------------------+
        |                               |
        v                               v
EVENT / DOMAIN INTELLIGENCE      CANONICAL MARKET FACTS
News / Events / GSE / BRACE      InstrumentRegistry
Entity / Macro / Technical       CanonicalMarketSnapshot
        |                               |
        +---------------+---------------+
                        v
                 EPISTEMIC LAYER
        EpistemicState / Causal Graph
                        |
                        v
                   BELIEF CORE
        probability != evidence confidence
                        |
              frozen forecasts / audit
                        |
        +---------------+----------------+
        |                                |
        v                                v
READ-ONLY BRIDGES / CONSUMERS       VERIFICATION
        |                            + calibration
        v                                |
DECISION / RESEARCH ENGINES              |
Stock Trading v2 / EURUSD / WES /         |
Portfolio10K / BRACE                      |
        |                                 |
        v                                 |
CANONICAL DECISION ENVELOPE               |
        |                                 |
        v                                 |
PER-ENGINE RISK POLICY                    |
        |                                 |
        v                                 |
EXECUTION / PAPER / SHADOW / PUBLICATION  |
        |                                 |
        v                                 |
OUTCOMES ---------------------------------+
        |
        v
SHARED LEARNING / EVOLUTION FABRIC
Experience Store / Learning Ledger / MISS / Regret
Hypotheses / Experiments / Replay / Counterfactuals
Statistical Gates / Shadow / Promotion / Rollback
        |
        +---- future-only policy/component changes ----> engines
```

**Core point:** BriefRooms is not one AI model. It is a system of contracts, adapters, epistemic engines, decision engines, experience ledgers, learning loops and governed promotion gates.

## 2. Canonical contracts and identity layer

| ID | Module | Responsibility | Main implementation / documentation | State / authority |
|---|---|---|---|---|
| `CF-01` | Instrument Registry | Stable instrument identity and symbol routing | `scripts/instrument_registry.py`, `docs/CANONICAL_INSTRUMENT_REGISTRY.md` | Authority for governed static instruments; dynamic equities use deterministic scoped IDs during migration |
| `CF-02` | Canonical MarketSnapshot | Immutable point-in-time market fact, provenance, timestamp lineage and DataQuality | `scripts/canonical_market_snapshot.py`, `scripts/market_snapshot_adapters.py`, `docs/CANONICAL_MARKET_SNAPSHOT.md` | Does not rank or decide; fails closed on invalid data quality |
| `CF-03` | Daily Engine Contract (legacy compatibility) | Historical normalization contract for former GPW/US Daily paths and Daily EUR/USD | `scripts/daily_engine_contract.py`, `scripts/daily_engine_adapters.py`, `docs/DAILY_TRADING_ARCHITECTURE.md` | Compatibility/migration only for stocks; active stock product authority is `TR-04` Stock Trading v2 |
| `CF-04` | Canonical Epistemic State | Canonical epistemic-state snapshot for consumers | `scripts/canonical_epistemic_state.py`, `scripts/canonical_epistemic_state_builder.py`, canonical epistemic-state docs | Information layer, not execution authority |
| `CF-05` | DecisionEnvelope | Binds a decision to engine/version/time/snapshot/risk/lineage | `scripts/decision_envelope.py`, `scripts/decision_envelope_adapters.py`, `docs/CANONICAL_DECISION_ENVELOPE.md` | Standardizes the decision record; does not originate a decision |
| `CF-06` | RiskPolicy Contract | Shared assessment shape while preserving independent per-engine limits | `scripts/risk_policy_contract.py` plus GPW/US/etc. policies | There is no one global BriefRooms risk threshold |
| `CF-07` | Epistemic Consumer Interface | Governed interface between epistemic state and consumers | `scripts/epistemic_consumer_interface.py`, `scripts/epistemic_consumer_live.py`, `docs/EPISTEMIC_CONSUMER_INTERFACE.md` | Limits coupling between Belief and decision engines |

### Current DecisionEnvelope / MarketSnapshot rollout

- Legacy GPW Daily final-decision path: **canonicalized**, but it is no longer an active trading product.
- Legacy US Daily new-entry / FLAT path: **canonicalized**; post-entry HOLD/CLOSE: **partial**. It is no longer the active stock product authority.
- Stock Trading v2: **production Champion**; production routing and portfolio state are handled by `stock_trading_v2_production_bridge.py`, `stock_trading_portfolio.py` and the NO RETROACTIVE airlock. Full P0.2/P0.3 rollout for every internal v2 event remains a separate migration concern and must not be conflated with the former Daily GPW/US labels.
- Daily EUR/USD: **partial**.
- WES: **partial**.
- BRACE-SPX: **not yet canonicalized** on the DecisionEnvelope/MarketSnapshot path.

Migration is prospective. Legacy records do not receive fabricated identifiers or timestamps.

## 3. Adapter layer — real BriefRooms adapters

An adapter is not a decision engine. Its primary job is to translate a source into an Observation, Evidence or canonical contract while preserving provenance and data quality.

| ID | Adapter / family | Main files | Contract / role |
|---|---|---|---|
| `AD-01` | Belief Adapter Contract | `scripts/belief_adapter_contract.py` | `RAW SOURCE -> Observation -> EvidenceAssessment -> Evidence` |
| `AD-02` | Market Data Adapter | `scripts/belief_market_data_adapter.py` | OHLCV and market facts; does not invent bid/ask |
| `AD-03` | Technical Adapter | `scripts/belief_technical_adapter.py` | Momentum, RSI, MA, breakout, VWAP, ATR, deterministic trend evidence |
| `AD-04` | Liquidity Adapter | `scripts/belief_liquidity_adapter.py` | RVOL, turnover, price-impact proxy, credit/liquidity evidence |
| `AD-05` | Regime / Cross-Asset Adapter | `scripts/belief_regime_adapter.py` | Breadth, volatility, financial conditions, `risk_on/neutral/risk_off/high_vol` |
| `AD-06` | News / Event Adapter | `scripts/belief_news_event_adapter.py` | News/event observations with provenance; downstream assessment retains authority |
| `AD-07` | Macro adapters | `scripts/belief_macro_calendar_adapter.py`, `scripts/belief_macro_data_adapter.py` | Calendar and macro data as time-stamped evidence |
| `AD-08` | Geopolitical adapter | `scripts/belief_geopolitical_forecast_adapter.py`, `scripts/belief_geopolitical_live.py` | Translates GSE/geopolitical state into the governed Belief layer |
| `AD-09` | WES Assets Adapter | `scripts/belief_wes_assets_adapter.py` | WES asset coverage for Belief |
| `AD-10` | Data Quality Adapter | `scripts/belief_data_quality_adapter.py` | Explicit quality/missingness; absent data is not a neutral signal |
| `AD-11` | Daily Engine Adapters (legacy) | `scripts/daily_engine_adapters.py` | Compatibility normalization for former GPW/US paths; not the active stock decision path |
| `AD-12` | Market/Decision canonical adapters | `scripts/market_snapshot_adapters.py`, `scripts/decision_envelope_adapters.py` | Converts native payloads to canonical contracts without changing decisions |
| `AD-13` | Legacy GPW / US Daily domain adapters | `scripts/daily_stock_gpw_adapter.py`, `scripts/daily_stock_us_adapter.py` | Historical contracts, lineage, settlement and compatibility; no production stock authority after v2 promotion |

### Hard adapter rules

- An Observation does not change a Belief by existing; an explicit EvidenceAssessment is required.
- `unavailable`, `stale`, and `invalid` observations do not become Evidence.
- Missing fields are never invented.
- Provenance and `observed_at` must survive transformation.
- An adapter does not gain trade-execution authority merely because it supplies a signal.

## 4. Epistemic / Belief architecture

| ID | Module | Function | Implementation / notes |
|---|---|---|---|
| `EP-01` | Observation + Evidence Store | Durable auditable observation/Evidence representation | `belief_adapter_contract.py`, Belief Core state/ledger |
| `EP-02` | Provenance & Independence Audit | Cluster de-duplication, source-ref collisions, lineage cycles, conflict, look-ahead rejection | Belief Core audit path |
| `EP-03` | Epistemic State | Structures current knowledge independently of decisions | `belief_epistemic_state.py`, `belief_epistemic_live.py` |
| `EP-04` | Epistemic Causal Graph | Explicit causal/relational semantics in the epistemic layer | `belief_epistemic_causal_graph.py`, `belief_epistemic_causal_graph_semantic.py` |
| `EP-05` | Belief Core v2 | Probability, separate evidence confidence, freshness decay, support/opposition, alternatives | `belief_core.py`, `belief_core_live.py`, `docs/BELIEF_CORE.md`; engineering-complete for shadow data collection |
| `EP-06` | Frozen Forecast + Verification | Freezes probability/evidence before outcome and verifies later | `belief_core_shadow.py`, `belief_core_verify.py` |
| `EP-07` | Calibration | Brier, log loss, ECE/MCE, slices, drift and source diagnostics | `belief_calibration.py`, `belief_calibration_foundation.py`; recommendations without silent auto-tuning |
| `EP-08` | Read-only Belief Bridges | Deliver epistemic/belief state to selected engines in governed modes | WES/BRACE/SPX bridge files; decision influence depends on the explicit gate |
| `EP-09` | Belief ARIS Shadow Diagnostics | Read-only research over alternative Evidence representations: model+residual, competing representations, ROI/pruning | `scripts/belief_aris_shadow.py`, `scripts/belief_aris_shadow_live.py`, `docs/BELIEF_ARIS_SHADOW.md`; `research_shadow`, no Belief writeback, consumer export, decision influence or auto-promotion |

### Belief Core is not a “constitution”

`Belief Core` is an **epistemic engine**: it maintains and calibrates beliefs from Evidence. Safety and authority rules are a separate governance layer. Belief Core cannot independently trade, alter sizing, or silently retune its own weights.

## 5. Intelligence / world-model layer

| ID | Module | Role | Implementation / status |
|---|---|---|---|
| `IN-01` | News Claim / Event Intelligence | Claim/event extraction and structuring, source quality and dedupe | `news_claim_intelligence.py`, `news_event_intelligence_v4.py`, source architecture |
| `IN-02` | Investment Event Intelligence | Corporate/market events and bridges into stock/weekly engines | `investment_event_intelligence.py`, `investment_corporate_event_intelligence.py`, `investment_event_stock_bridge.py`, `investment_event_weekly_bridge.py` |
| `IN-03` | Geopolitical Scenario Engine (GSE) | `Evidence -> Scenario -> Transmission Graph -> Multi-Asset Forecast -> Verification -> Calibration` plus prospective learning | `geopolitical_scenario_engine.py` remains the v1 foundation; active hourly research/learning runtime is `gse_v2_fast_cycle.py`, `gse_v2_learning_loop.py`, `.github/workflows/gse-hourly-cycle-v2.yml`; shadow/research, no execution authority |
| `IN-04` | BRACE Entity Intelligence | Company entity framework, primary-source evidence, disagreement, interpretation, belief-state forecast, calibration | `brace_entity_*` family |
| `IN-05` | Broad/Sector Market Beliefs | Broad-market and sector/factor beliefs for BRACE | `brace_broad_market_belief.py`, `brace_sector_factor_belief.py` |
| `IN-06` | Investment Semantics / World State | Semantic representation of investment state | `INVESTMENT_SEMANTICS_WORLD_STATE.md` and related modules |
| `IN-07` | TimesFM Shadow Forecaster | Independent research/shadow forecaster and benchmark | `timesfm_shadow_forecaster.py`, `timesfm3_internal_benchmark.py`; not standalone production authority |
| `IN-08` | Market Relationship / Trigger Engine | `EVENT × ENTITY × PEERS × MARKET REACTION × TIME -> ATTENTION`; scarce research routing for Stock Trading v2 | Research branch `stock-trading-v2`: `scripts/briefrooms_market_relationship_trigger.py`, `briefrooms_market_relationship_outcomes.py`, `briefrooms_trigger_deep_belief.py`, `briefrooms_trigger_deep_belief_learning.py`; max 6 attention (4 trigger + 2 exploration), max 2 Deep BELIEF proxy, zero production decision authority |

## 6. Decision / trading engines

| ID | Engine | Horizon / role | Architectural state |
|---|---|---|---|
| `TR-01` | Legacy GPW Daily pipeline | Historical GPW Daily candidate research, settlement, lineage and MISS/rejected-candidate memory | **DEPRECATED AS PRODUCT / REPLACED_BY `TR-04`**. Code/workflows may remain for legacy research/settlement compatibility, but are not the production Champion and may not admit positions into the v2 portfolio |
| `TR-02` | Legacy US Daily pipeline | Historical US Daily lifecycle/risk/memory | **DEPRECATED AS PRODUCT / REPLACED_BY `TR-04`**. Retained for migration/history compatibility; active stock authority belongs to v2 |
| `TR-03` | Daily EUR/USD Spot | Intraday–24h; shared Daily contract | Historically shadow rollout; independent lifecycle/event overlay/ABC learning |
| `TR-04` | Stock Trading v2 | Unified active stock engine for GPW and US: Dynamic Universe, Opportunity Frontier, Trigger research, Deep Evidence, Fixed Notional Sizing, Portfolio Opportunity Engine | **PRODUCTION CHAMPION — FULL**. Every new position uses `FIXED_NOTIONAL_V1`: PLN 5,000 on GPW or USD 5,000 in US; `champion_engine=v2`, `challenger_engine=v1`, `legacy_candidate_admission_enabled=false`; `main` is production authority, `stock-trading-v2` is research/evidence runtime |
| `TR-05` | Weekly Positions / WES family | EUR/USD, S&P 500 futures, BTC/USD; governed paper/research plus WES memory/counterfactual/belief bridges | Current runtime policy v5.6.x / WES: experimental governed paper only; `NO_TRADE` is first-class, `mandatory_monday_position=false`, `continuous_position_required=false` |
| `TR-06` | Portfolio 10K baseline | Long-horizon portfolio, historical champion/baseline | Preserved production baseline and BRACE fallback |
| `TR-07` | BRACE Portfolio Engine | Portfolio research/control with optimizer, governance and separate model paper portfolio | **PROBATIONARY_CONTROL**; paper-only, deterministic controller, immutable Portfolio10K baseline as fallback; LLM cannot promote |
| `TR-08` | BRACE-SPX / Long View | Read-only/forecast-oriented SPX long-view family | Uses its point-in-time state plus epistemic state; canonical DecisionEnvelope rollout incomplete |

### Stock Trading v2 internal decomposition

```text
Dynamic Universe
 -> Broad Discovery / Fast Scan
 -> Opportunity Frontier / Ranking
 -> Market Relationship / Trigger research (US)
      max 6 attention = max 4 trigger + 2 exploration
      max 2 targeted Deep BELIEF proxy
 -> Broad Deep Evidence Champion
 -> Research Risk Plan
 -> Fixed Notional Sizing
      GPW = PLN 5,000 / company
      US  = USD 5,000 / company
 -> Portfolio Opportunity Engine
      BUY / REPLACE / HOLD / CASH
 -> Experience Freeze (selected + rejected)
 -> Outcome Replay
 -> Opportunity Regret
 -> Challenger learning/evaluation
 -> Statistical promotion gate
```

Main implementation families: `stock_trading_v2_*`, `stock_trading_component_*`, `stock_trading_portfolio.py`. Sizing and history-normalization contract: `docs/STOCK_TRADING_FIXED_NOTIONAL_EN.md` / `_PL.md`. Public history displays 5K notional, fractional analytical quantity and P&L calculated from that common base.

### Stock Trading v2 branch/runtime authority

```text
main
  = production + governance + default-branch orchestration
        |
        | frozen production snapshots
        v
stock-trading-v2
  = research/evidence runtime
  = discovery / Trigger / Deep BELIEF proxy / outcomes / regret / Challengers
        |
        | exact bounded evaluated candidate
        v
main: Stock Trading Component Promotion
  = sole component production-promotion authority
```

Scheduled workflow definitions are maintained on `main`, while research jobs explicitly check out `stock-trading-v2`. The research branch may not directly push a production mutation to `main`.

## 7. Shared Learning / Evolution Fabric

**Current state:** a distributed, actually implemented learning layer exists. There is not yet one monolithic runtime module named `AXIOM Evolution Kernel`.

| ID | Module | Responsibility | Example implementation |
|---|---|---|---|
| `LE-01` | Experience Store | Prospective selected/rejected/position experience | `experience_store.py`, `experience_store_multi_source.py`, `stock_trading_v2_experience_store.py` |
| `LE-02` | Learning Ledger / Outcome Loop | Immutable decision/forecast before outcome, later outcome binding | `learning_ledger.py`, `learning_outcome_loop.py`, `LEARNING_OUTCOME_LOOP_INTEGRATION.md` |
| `LE-03` | MISS / Regret | Error analysis including candidates that were NOT selected | `daily_stock_miss_engine.py`, `stock_trading_v2_regret_engine.py`, rejected-candidate outcome/attribution modules |
| `LE-04` | Hypothesis / Experiment Registry | Hypothesis -> experiment -> lesson | `lesson_hypothesis_registry.py`, `hypothesis_experiment_compiler.py`, `experiment_registry.py`, `experiment_result_lesson_loop.py` |
| `LE-05` | Replay / Counterfactual Labs | Evaluate alternatives without rewriting true history | `stock_trading_v2_outcome_replay.py`, `gpw_loco_counterfactual_replay.py`, `investments_wes_counterfactual.py`, `brace_portfolio_counterfactual_lab.py`, `counterfactual_decision_gate_diagnostics.py` |
| `LE-06` | Champion / Challenger + Statistical Gate | Prospective challenger comparison, sample gates, OOS/holdout and controlled promotion | `stock_trading_component_champion.py`, `stock_trading_component_candidate_intake.py`, `stock_trading_component_bound_promotion.py`, `statistical_promotion_gate.py`, `statistical_promotion_gate_v2.py`; Stock Trading production promotion is main-owned |
| `LE-07` | Autonomous Policy Loop (legacy/research) | PR35/PR36 observatory, prospective calibration research and preserved historical actuator lineage | `autonomous_policy_observatory.py`, `autonomous_policy_promotion.py`, `autonomous_policy_closed_loop.py`; after Reconciliation 1.5 `automatic_materialization_enabled=false` for Stock Trading — no production write authority |
| `LE-08` | Engine-specific self-learning | Local loops for BRACE, Daily Stock, EURUSD, WES, etc. | `brace_portfolio_self_learning.py`, `daily_stock_self_improvement.py`, `daily_eurusd_abc_learning.py`, `gse_v2_learning_loop.py` |
| `LE-09` | Integrity / anti-hindsight | No retroactive live history, timestamp integrity, activation boundaries, workflow airlocks | `no_retroactive_execution.py`, `daily_stock_timestamp_integrity.py`, `promotion_learning_integrity.py`, verification scripts |
| `LE-10` | Shared Learning Loop v2 Diagnostics | Shared read-only primitives: ex-ante decision quality, ex-post outcome quality, near-miss/shadow, calibration/model-ablation/evidence-delta diagnostics | `scripts/learning_loop_v2.py`, `scripts/learning_loop_v2_observer.py`; challenger/observer has zero production authority and does not rewrite decisions or thresholds |

### Canonical evolution loop

```text
Experience
 -> Outcome / Verification
 -> MISS / Regret / Attribution
 -> Hypothesis
 -> Challenger
 -> Replay / Counterfactual
 -> OOS / Holdout
 -> Shadow / Canary
 -> Statistical / Governance Gate
 -> Promotion OR Reject
 -> Champion
 -> Monitor
 -> Rollback if invariants fail
```

Not every engine currently implements every step identically. The Architecture Map captures the shared pattern and local implementations.

## 8. Content / research publication layer

| ID | Module | Role | Main elements |
|---|---|---|---|
| `CT-01` | News pipeline | Bilingual news ingestion, quality, dedupe, summaries/publication | `fetch_news_pl.py`, `fetch_news_en.py`, news source/quality/intelligence modules |
| `CT-02` | AI Outlook | Governed daily outlook, provider/freshness/status/metrics | `ai_outlook_engine.py` plus `ai-outlook-*` workflows/data |
| `CT-03` | AI Tournament | Independent comparison/submission/round/public-UI layer | `ai_tournament_engine.py`, intake/bootstrap/UI modules |
| `CT-04` | AXIOM Thought | Thought/motto publication with quality guard | `axiom_thought_guard.py`, `publish_axiom_thought.py` |
| `CT-05` | Public projections / UI | Sanitized JSON/JS -> PL/EN pages | multiple `*_public_projection.py`, JS renderers, `pl/`, `en/` |
| `CT-06` | Home / editorial | Home brief, market signal, Hot X, editorial rules | home build pipelines, `briefrooms_editorial_rules.md`, content contracts |

The publication layer **must not become a hidden authority source for a decision engine**. A public renderer displays state; it must not originate or retroactively alter an economic decision.

## 9. Runtime / automation layer

`GitHub Actions` is a material part of BriefRooms runtime. Workflow families cover, among others:

- Belief Core / Epistemic / causal graph / calibration,
- BRACE entity + portfolio,
- legacy GPW/US Daily compatibility and settlement workflows,
- Daily EUR/USD,
- Stock Trading v2 discovery/learning/validation/production,
- Weekly/WES,
- GSE,
- Autonomous Policy Observatory/Promotion/Closed Loop,
- Learning Outcome Loop,
- AI Outlook,
- AI Tournament,
- AXIOM Thought,
- publication, freshness guardians and automation health.

### State boundaries

1. **Public repository state** — data intended for versioning/public projection only.
2. **Private GitHub Actions artifacts** — raw/cumulative learning history and shadow state where architecture requires privacy/durability boundaries.
3. **Append-only / hash-chained ledgers** — where history integrity is contractual.
4. **Cache** — never authority for economic-decision history.
5. **Public projection** — sanitized derivative, never the primary decision ledger.

Examples of private durable state include the Learning Outcome Loop and GSE/Belief shadow artifacts. Portfolio10K/BRACE has explicitly documented registry, shadow log, promotion history and paper-portfolio state.

## 10. Non-negotiable system invariants

1. **NO RETROACTIVE LIVE HISTORY.** A trade/forecast/decision is real only if frozen prospectively before its outcome. Post-hoc replay is `SIMULATED/COUNTERFACTUAL`, never `LIVE/EXECUTED`.
2. **Point-in-time lineage.** `observed_at <= received_at <= created_at <= decision_at` on the applicable canonical path, subject only to explicitly configured clock-skew tolerance.
3. **Frozen means immutable.** Frozen forecasts, admissions, closed-trade history and immutable decision events cannot be rewritten after outcomes are known.
4. **Missing != neutral.** Missing data does not become `0`, neutral Evidence or a synthetic quote.
5. **Source engine owns the decision.** Adapter, DecisionEnvelope, renderer and Learning Ledger do not take authority from the source engine.
6. **Risk remains per-engine.** The common contract does not homogenize GPW, US, EURUSD, WES, BRACE, etc. thresholds.
7. **Belief influence is gated.** Belief Core existence alone does not authorize decision influence without an explicit WITH/WITHOUT, read-only or promotion gate.
8. **No silent auto-tuning.** Belief calibration may recommend; production policy change requires the relevant challenger/promotion loop.
9. **No hindsight learning.** An outcome can bind to a decision/forecast only if the upstream event existed before the outcome under the activation boundary.
10. **Selected AND rejected matter.** Where supported, rejected candidates are frozen and analyzed for MISS/opportunity regret.
11. **Production vs shadow is explicit.** Shadow output cannot be presented as a historically executed production trade.
12. **PL/EN architecture sync.** Every architecture change updates both map versions and required detailed documentation.
13. **Stock Trading fixed notional + normalized history.** Every new `TR-04` position uses a fixed nominal size of PLN 5,000 (GPW) or USD 5,000 (US) through `FIXED_NOTIONAL_V1`. Closed history, including legacy history, is compared through explicit `FIXED_NOTIONAL_HISTORY_V1` derived analytics with `history_normalized_quantity = 5000 / entry`; prices, timestamps and execution facts remain immutable.
14. **Architecture bootstrap before architecture work.** An AI/agent starts an architecture change from `AGENTS.md` and the canonical map, not from conversational memory or an isolated code search.
15. **Main owns Stock Trading production; the research branch owns research only.** `stock-trading-v2` may generate Evidence, Trigger state and Challengers, but may not directly mutate production `main`.
16. **Single Stock Trading promotion writer.** Stock Trading component production promotion flows only through the main-owned `Stock Trading Component Promotion`; the legacy Autonomous Policy Loop has `automatic_materialization_enabled=false`.
17. **Trigger attention is research authority, not trade authority.** `IN-08` may allocate at most 6 attention slots and at most 2 targeted Deep BELIEF proxy slots, but cannot open positions, write policy or promote itself into production.

## 11. Authority map — what each layer may NOT do

```text
Adapter              -> may translate/assess data; does NOT trade
Belief Core           -> may maintain beliefs/forecasts; does NOT execute trades
Epistemic Bridge      -> may deliver state; does NOT take decision authority without a gate
DecisionEnvelope      -> records lineage; does NOT rank or select instruments
RiskPolicy Contract   -> standardizes assessment; does NOT impose one global limit
Learning Ledger       -> observes/freezes/binds outcomes; does NOT rewrite source state
Replay/Counterfactual -> simulates alternatives; does NOT create historical live trades
LLM                   -> may interpret/propose; does NOT self-promote where governance requires
                         deterministic/statistical approval
Research branch       -> may generate evidence/challengers; does NOT directly mutate production main
Public UI             -> renders state; is NOT a decision source
```

## 12. Important subsystem status at map snapshot 1.5

- **Belief Core v2:** engineering-complete for shadow data collection; decision-independent.
- **Evidence adapters:** real modular layer; core market/technical/liquidity/regime adapters are deterministic.
- **GSE:** v1 remains the forecasting foundation; active hourly research/learning runtime is **GSE v2**, still without execution authority.
- **Legacy Daily GPW / US:** no longer active stock-trading products; retained as deprecated migration/research/settlement paths.
- **Daily EUR/USD:** independent Daily engine with own lifecycle/learning; canonical rollout partial.
- **Stock Trading v2:** **active production Champion in FULL** for GPW and US. `main` is production/governance authority, `stock-trading-v2` is research/evidence runtime; v1 remains Challenger/rollback lineage.
- **Weekly/WES:** experimental governed paper layer; current policy v5.6.x, `NO_TRADE` active, exposure is not mandatory.
- **Portfolio 10K:** preserved baseline/champion.
- **BRACE Portfolio:** **PROBATIONARY_CONTROL**, paper-only; deterministic controller and immutable Portfolio10K fallback baseline.
- **Shared learning fabric:** exists and is actively developed; not yet one unified Evolution Kernel.

## 13. Planned layer above the current architecture

### `FUT-01` — AXIOM Evolution Kernel / Meta-Exploration

Status: **architectural direction; it must not be confused with an existing single runtime module**.

The goal is to orchestrate existing loops rather than replace them:

```text
Experience Graph / Learning Fabric
 -> Regret & failure attribution
 -> choose the module with highest expected improvement
 -> bounded Challenger Factory for that module
 -> Replay / Holdout / Shadow
 -> component-level Promotion Gate
 -> Composite Champion
```

Candidate evolution targets include adapters, evidence assessment, belief update/calibration, candidate discovery, ranking, entry, risk, exit, portfolio and event intelligence. Each component needs both a local quality metric and an end-to-end system-impact metric.

## 14. How AXIOM and other agents should use this map

Every new session or agent working on BriefRooms architecture starts from repository-root `AGENTS.md`. Then:

1. Open this canonical map before designing the change.
2. Identify the affected stable `module_id` set.
3. Open the referenced detailed documents instead of reconstructing architecture from conversational memory.
4. Inspect actual runtime code, readers/writers, workflows, state and tests, and search for existing/overlapping capability.
5. Establish the impact surface: contracts, authority, state ownership, upstream/downstream consumers, migration, rollback and required tests.
6. When architecture changes, update the relevant PL+EN map sections in the same PR.
7. Create a new subsystem only after demonstrating that equivalent capability does not already exist; assign a stable `module_id` and document responsibility, authority, inputs, outputs, state, learning mode, safety invariants and code/docs references.

## 15. Source documents for map v1.0

Primary detailed documents used to build and maintain the current map:

- `AGENTS.md`
- `scripts/validate_architecture_bootstrap.py`
- `ARCHITECTURE_DOCUMENTATION_POLICY_EN.md` / `_PL.md`
- `ARCHITECTURE_RECONCILIATION_1_5_EN.md` / `_PL.md`
- `scripts/validate_architecture_reconciliation.py`
- `BELIEF_CORE.md`
- `BELIEF_ARIS_SHADOW.md`
- `BELIEF_EVIDENCE_ADAPTERS.md`
- `BELIEF_EPISTEMIC_STATE.md`
- `BELIEF_EPISTEMIC_CAUSAL_GRAPH.md`
- `CANONICAL_INSTRUMENT_REGISTRY.md`
- `CANONICAL_MARKET_SNAPSHOT.md`
- `CANONICAL_DECISION_ENVELOPE.md`
- `DAILY_TRADING_ARCHITECTURE.md`
- `stock-trading-v2-architecture.md`
- `stock-trading-v2-production-promotion.md`
- `STOCK_TRADING_FIXED_NOTIONAL_EN.md` / `_PL.md`
- `LEARNING_OUTCOME_LOOP_INTEGRATION.md`
- `CHAMPION_CHALLENGER_STATISTICAL_GATE_V1.md`
- `AUTONOMOUS_POLICY_OBSERVATORY_V1.md`
- `AUTONOMOUS_POLICY_PROMOTION_V1.md`
- `BRACE_SELF_LEARNING.md`
- `BRACE_PORTFOLIO_ENGINE.md`
- `GEOPOLITICAL_SCENARIO_ENGINE.md`
- `GSE_V2_ADVANCED_LEARNING_LOOP.md`
- `WES_HISTORICAL_MEMORY.md`
- `WES_COUNTERFACTUAL_INCREMENTAL_ALPHA.md`
- `weekly_trading_methodology_v4.md`
- `ai-outlook-engine-v1.md`
- `ai-tournament-methodology-v1.md`
- `BRIEFROOMS_CHANGE_RULES.md`

## 16. Migration record — Stock Trading consolidation

- 2026-09-18: active stock-trading architecture was consolidated under `TR-04 Stock Trading v2`.
- `TR-01 Daily GPW` and `TR-02 Daily US` retain stable IDs only for traceability and are marked `DEPRECATED AS PRODUCT / REPLACED_BY TR-04`.
- Their code and historical ledgers are retained where they still serve research, settlement, compatibility or rollback lineage. Their output cannot be treated as v2 production admission.
- Production authority is defined by `data/investments/stock_trading_policy.json`: `champion_engine=v2`, `challenger_engine=v1`, `legacy_candidate_admission_enabled=false`.
- 2026-09-19: Architecture Reconciliation 1.5 separated authority: `main` = production/governance/orchestration, `stock-trading-v2` = research/evidence runtime.
- Trigger/Relationship plus targeted Deep BELIEF proxy were wired into default-branch schedulers without granting production decision authority.
- The direct research-branch auto-promoter was removed; Stock Trading Component Promotion on `main` is the sole production-promotion authority.
- The legacy Autonomous Policy Closed Loop retains research lineage but stock `automatic_materialization_enabled=false`.

---

**Maintenance invariant:** this file and `ARCHITECTURE_MAP_PL.md` are one logical artifact. Updating only one version is not allowed.