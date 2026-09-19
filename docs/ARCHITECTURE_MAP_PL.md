# Kanoniczna mapa architektury BriefRooms — PL

**Wersja mapy:** 1.4  
**Stan na:** 2026-09-19  
**Bazowy commit `main`:** `4541344d60a3e6a6076b98d86a033c203a4d3b08`  
**Repozytorium:** `Maja2711/briefrooms-site`

## 0. Rola tego dokumentu

Ten dokument jest **kanoniczną mapą nawigacyjną architektury BriefRooms**. Ma odpowiadać na pytania: jakie moduły istnieją, kto ma authority (władzę decyzyjną), jaki jest przepływ danych, gdzie znajduje się kod i dokumentacja oraz które granice bezpieczeństwa są nienaruszalne.

Kod runtime pozostaje źródłem prawdy implementacyjnej. Jeżeli mapa i kod się rozchodzą, jest to **architecture drift** i wymaga poprawy mapy albo implementacji. Mapa nie zastępuje szczegółowych dokumentów modułów; wskazuje ich miejsce w całym systemie.

### Obowiązkowa zasada utrzymania

Każdy PR zmieniający architekturę BriefRooms MUSI w tym samym PR zaktualizować:

- `docs/ARCHITECTURE_MAP_PL.md`,
- `docs/ARCHITECTURE_MAP_EN.md`.

Za zmianę architektury uważa się w szczególności: zmianę kontraktu, authority, przepływu danych, adaptera, silnika, pętli learning/verification, semantyki persystencji, granicy production/shadow, reguł promocji/rollbacku albo invariantów bezpieczeństwa.

### Obowiązkowy bootstrap AI / agentów

Każdy AI/agent pracujący nad zmianą architektury lub logiki inwestycyjnej musi zacząć od repozytoryjnego `AGENTS.md`, a następnie otworzyć tę mapę, wskazać dotknięte `module_id`, przeczytać dokumentację szczegółową i dopiero potem wejść w kod. Nie wolno rekonstruować architektury z pamięci rozmowy ani tworzyć nowego subsystemu bez sprawdzenia istniejących, nakładających się możliwości. `scripts/validate_architecture_bootstrap.py` oraz workflow `Architecture Bootstrap Guard` chronią trwałość tego mechanizmu.

## 1. L0 — mapa całego systemu

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

**Najważniejsze:** BriefRooms nie jest jednym modelem AI. Jest zestawem kontraktów, adapterów, silników epistemicznych, silników decyzji, ledgerów doświadczenia, pętli uczenia i kontrolowanych bramek promocji.

## 2. Kanoniczne kontrakty i identity layer

| ID | Moduł | Odpowiedzialność | Główna implementacja / dokumentacja | Stan / authority |
|---|---|---|---|---|
| `CF-01` | Instrument Registry | Stabilna tożsamość instrumentów i routing symboli | `scripts/instrument_registry.py`, `docs/CANONICAL_INSTRUMENT_REGISTRY.md` | Authority dla zarządzanych statycznych instrumentów; dynamiczne akcje mają deterministyczne scoped IDs podczas migracji |
| `CF-02` | Canonical MarketSnapshot | Immutable point-in-time fakt rynkowy, provenance, timestamp lineage i DataQuality | `scripts/canonical_market_snapshot.py`, `scripts/market_snapshot_adapters.py`, `docs/CANONICAL_MARKET_SNAPSHOT.md` | Nie rankuje i nie decyduje; fail-closed dla złej jakości danych |
| `CF-03` | Daily Engine Contract (legacy compatibility) | Historyczny kontrakt normalizacji dawnych GPW/US Daily oraz Daily EUR/USD | `scripts/daily_engine_contract.py`, `scripts/daily_engine_adapters.py`, `docs/DAILY_TRADING_ARCHITECTURE.md` | Compatibility/migration only dla stocków; aktywnym stock product authority jest `TR-04` Stock Trading v2 |
| `CF-04` | Canonical Epistemic State | Kanoniczny snapshot stanu epistemicznego dla konsumentów | `scripts/canonical_epistemic_state.py`, `scripts/canonical_epistemic_state_builder.py`, dokumenty canonical epistemic state | Warstwa informacyjna, nie execution authority |
| `CF-05` | DecisionEnvelope | Wiąże decyzję z engine/version/time/snapshot/risk/lineage | `scripts/decision_envelope.py`, `scripts/decision_envelope_adapters.py`, `docs/CANONICAL_DECISION_ENVELOPE.md` | Standaryzuje rekord decyzji, ale nie tworzy decyzji |
| `CF-06` | RiskPolicy Contract | Wspólny shape oceny ryzyka przy zachowaniu niezależnych limitów per-engine | `scripts/risk_policy_contract.py` + polityki GPW/US itd. | Nie istnieje jeden globalny próg ryzyka BriefRooms |
| `CF-07` | Epistemic Consumer Interface | Kontrolowany interfejs między epistemic state a konsumentem | `scripts/epistemic_consumer_interface.py`, `scripts/epistemic_consumer_live.py`, `docs/EPISTEMIC_CONSUMER_INTERFACE.md` | Ogranicza coupling między Belief a silnikami decyzyjnymi |

### Aktualny rollout DecisionEnvelope / MarketSnapshot

- Legacy GPW Daily final-decision path: **canonicalized**, ale nie jest już aktywnym produktem tradingowym.
- Legacy US Daily new-entry / FLAT path: **canonicalized**; post-entry HOLD/CLOSE: **partial**. Nie jest już aktywnym stock product authority.
- Stock Trading v2: **production Champion**; produkcyjny routing i portfolio state są obsługiwane przez `stock_trading_v2_production_bridge.py`, `stock_trading_portfolio.py` oraz NO RETROACTIVE airlock. Pełny rollout P0.2/P0.3 dla każdego wewnętrznego eventu v2 jest osobnym zadaniem migracyjnym i nie należy utożsamiać go ze starymi nazwami Daily GPW/US.
- Daily EUR/USD: **partial**.
- WES: **partial**.
- BRACE-SPX: **not yet canonicalized** dla DecisionEnvelope/MarketSnapshot path.

Migracja jest prospektywna. Stare rekordy nie dostają sztucznie wygenerowanych identyfikatorów ani timestampów.

## 3. Adapter layer — rzeczywiste adaptery BriefRooms

Adapter nie jest silnikiem decyzji. Jego podstawowym zadaniem jest tłumaczenie źródła na obserwację, Evidence albo kanoniczny kontrakt z zachowaniem provenance i jakości danych.

| ID | Adapter / rodzina | Główne pliki | Kontrakt / rola |
|---|---|---|---|
| `AD-01` | Belief Adapter Contract | `scripts/belief_adapter_contract.py` | `RAW SOURCE -> Observation -> EvidenceAssessment -> Evidence` |
| `AD-02` | Market Data Adapter | `scripts/belief_market_data_adapter.py` | OHLCV i fakty rynkowe; nie fabrykuje bid/ask |
| `AD-03` | Technical Adapter | `scripts/belief_technical_adapter.py` | Momentum, RSI, MA, breakout, VWAP, ATR, deterministic trend evidence |
| `AD-04` | Liquidity Adapter | `scripts/belief_liquidity_adapter.py` | RVOL, turnover, price-impact proxy, credit/liquidity evidence |
| `AD-05` | Regime / Cross-Asset Adapter | `scripts/belief_regime_adapter.py` | breadth, volatility, financial conditions, `risk_on/neutral/risk_off/high_vol` |
| `AD-06` | News / Event Adapter | `scripts/belief_news_event_adapter.py` | News/event observations z provenance; authority zależy od downstream assessment |
| `AD-07` | Macro adapters | `scripts/belief_macro_calendar_adapter.py`, `scripts/belief_macro_data_adapter.py` | Kalendarz i dane makro jako time-stamped evidence |
| `AD-08` | Geopolitical adapter | `scripts/belief_geopolitical_forecast_adapter.py`, `scripts/belief_geopolitical_live.py` | Translacja GSE/geopolityki do kontrolowanej warstwy Belief |
| `AD-09` | WES Assets Adapter | `scripts/belief_wes_assets_adapter.py` | Pokrycie aktywów WES dla warstwy Belief |
| `AD-10` | Data Quality Adapter | `scripts/belief_data_quality_adapter.py` | Jawna jakość/braki; brak danych nie jest sygnałem neutralnym |
| `AD-11` | Daily Engine Adapters (legacy) | `scripts/daily_engine_adapters.py` | Compatibility normalization dawnych GPW/US; nie jest aktywnym stock decision path |
| `AD-12` | Market/Decision canonical adapters | `scripts/market_snapshot_adapters.py`, `scripts/decision_envelope_adapters.py` | Przejście natywnych payloadów do kanonicznych kontraktów bez zmiany decyzji |
| `AD-13` | Legacy GPW / US Daily domain adapters | `scripts/daily_stock_gpw_adapter.py`, `scripts/daily_stock_us_adapter.py` | Utrzymanie historycznych kontraktów, lineage, settlement i compatibility; bez production stock authority po promocji v2 |

### Twarde zasady adapterów

- Observation sama nie zmienia Belief; potrzebny jest jawny EvidenceAssessment.
- `unavailable`, `stale`, `invalid` nie stają się Evidence.
- Brakujące pola nie są wymyślane.
- Provenance i `observed_at` muszą przeżyć transformację.
- Adapter nie otrzymuje authority do trade execution tylko dlatego, że dostarcza sygnał.

## 4. Epistemic / Belief architecture

| ID | Moduł | Funkcja | Implementacja / uwagi |
|---|---|---|---|
| `EP-01` | Observation + Evidence Store | Trwały, audytowalny zapis obserwacji i Evidence | `belief_adapter_contract.py`, Belief Core state/ledger |
| `EP-02` | Provenance & Independence Audit | Deduplikacja klastrów, source-ref collisions, lineage cycles, conflict, look-ahead rejection | część `belief_core.py` / audit path |
| `EP-03` | Epistemic State | Strukturyzuje aktualny stan wiedzy niezależnie od decyzji | `belief_epistemic_state.py`, `belief_epistemic_live.py` |
| `EP-04` | Epistemic Causal Graph | Jawne relacje i semantyka przyczynowa/relacyjna w epistemic layer | `belief_epistemic_causal_graph.py`, `belief_epistemic_causal_graph_semantic.py` |
| `EP-05` | Belief Core v2 | Probability, oddzielna evidence confidence, freshness decay, support/opposition, alternatives | `belief_core.py`, `belief_core_live.py`, `docs/BELIEF_CORE.md`; engineering-complete dla shadow data collection |
| `EP-06` | Frozen Forecast + Verification | Zamraża probability/evidence przed outcome i później weryfikuje | `belief_core_shadow.py`, `belief_core_verify.py` |
| `EP-07` | Calibration | Brier, log loss, ECE/MCE, slices, drift, source diagnostics | `belief_calibration.py`, `belief_calibration_foundation.py`; rekomendacje bez silent auto-tuning |
| `EP-08` | Read-only Belief Bridges | Dostarczają epistemic/belief state do wybranych silników w trybach kontrolowanych | WES/BRACE/SPX bridge files; wpływ decyzji zależy od konkretnego gate |

### Belief Core nie jest „konstytucją”

`Belief Core` jest **epistemic engine**: utrzymuje i kalibruje przekonania na podstawie Evidence. Zasady bezpieczeństwa i authority są osobną warstwą governance. Belief Core nie ma prawa samodzielnie handlować, zmieniać sizingu ani cicho dostrajać własnych wag.

## 5. Intelligence / world-model layer

| ID | Moduł | Rola | Implementacja / status |
|---|---|---|---|
| `IN-01` | News Claim / Event Intelligence | Ekstrakcja i strukturyzacja claimów/eventów, źródła, jakość, dedupe | `news_claim_intelligence.py`, `news_event_intelligence_v4.py`, source architecture |
| `IN-02` | Investment Event Intelligence | Eventy korporacyjne/rynkowe i bridge do stock/weekly | `investment_event_intelligence.py`, `investment_corporate_event_intelligence.py`, `investment_event_stock_bridge.py`, `investment_event_weekly_bridge.py` |
| `IN-03` | Geopolitical Scenario Engine (GSE) | `Evidence -> Scenario -> Transmission Graph -> Multi-Asset Forecast -> Verification -> Calibration` | `geopolitical_scenario_engine.py`; GSE v1 shadow-only, bez execution authority |
| `IN-04` | BRACE Entity Intelligence | Company entity framework, primary-source evidence, disagreement, interpretation, belief-state forecast, calibration | rodzina `brace_entity_*` |
| `IN-05` | Broad/Sector Market Beliefs | Broad market i sector/factor beliefs dla BRACE | `brace_broad_market_belief.py`, `brace_sector_factor_belief.py` |
| `IN-06` | Investment Semantics / World State | Semantyczne odwzorowanie stanu inwestycyjnego | `INVESTMENT_SEMANTICS_WORLD_STATE.md` i powiązane moduły |
| `IN-07` | TimesFM Shadow Forecaster | Niezależny forecaster badawczy/shadow i benchmark | `timesfm_shadow_forecaster.py`, `timesfm3_internal_benchmark.py`; nie jest samodzielnym production authority |

## 6. Decision / trading engines

| ID | Silnik | Horyzont / rola | Stan architektoniczny |
|---|---|---|---|
| `TR-01` | Legacy GPW Daily pipeline | Historyczny GPW Daily: candidate research, settlement, lineage i MISS/rejected-candidate memory | **DEPRECATED AS PRODUCT / REPLACED_BY `TR-04`**. Kod i workflow mogą działać jako legacy research/settlement compatibility, ale nie są production Championem i nie mają prawa przyjmować pozycji do portfela v2 |
| `TR-02` | Legacy US Daily pipeline | Historyczny US Daily lifecycle/risk/memory | **DEPRECATED AS PRODUCT / REPLACED_BY `TR-04`**. Zachowany jako migration/history compatibility; aktywny stock authority należy do v2 |
| `TR-03` | Daily EUR/USD Spot | Intraday–24h; wspólny Daily contract | Historycznie rollout shadow; własne lifecycle/event overlay/ABC learning |
| `TR-04` | Stock Trading v2 | Wspólny aktywny stock engine dla GPW i US: Dynamic Universe, Opportunity Frontier, Deep Evidence, Fixed Notional Sizing, Portfolio Opportunity Engine | **PRODUCTION CHAMPION**. Każda nowa pozycja ma `FIXED_NOTIONAL_V1`: 5 000 PLN na GPW lub 5 000 USD na US; `champion_engine=v2`, `challenger_engine=v1`, `legacy_candidate_admission_enabled=false` |
| `TR-05` | Weekly Positions / WES family | EUR/USD, S&P 500 futures, BTC/USD; tygodniowy paper/research + WES memory/counterfactual/belief bridges | Weekly v4 = experimental paper research; WES ma własną pamięć i counterfactual layer |
| `TR-06` | Portfolio 10K baseline | Długoterminowy portfel, historyczny champion/baseline | Zachowywany jako production baseline i fallback dla BRACE |
| `TR-07` | BRACE Portfolio Engine | Portfolio research/control z optimizerem, governance, paper control | `ACTIVE_BASELINE + BRACE_SHADOW`; promocja deterministyczna, LLM nie może promować |
| `TR-08` | BRACE-SPX / Long View | Read-only/forecasting oriented SPX long-view family | Konsumuje własny point-in-time state + epistemic state; canonical DecisionEnvelope rollout nieukończony |

### Stock Trading v2 — wewnętrzny podział

```text
Dynamic Universe
 -> Broad Discovery / Fast Scan
 -> Opportunity Frontier / Ranking
 -> Deep Evidence
 -> Research Risk Plan
 -> Fixed Notional Sizing
      GPW = 5 000 PLN / spółkę
      US  = 5 000 USD / spółkę
 -> Portfolio Opportunity Engine
      BUY / REPLACE / HOLD / CASH
 -> Experience Freeze (selected + rejected)
 -> Outcome Replay
 -> Opportunity Regret
 -> Challenger learning/evaluation
 -> Statistical promotion gate
```

Główne implementacje: `stock_trading_v2_*`, `stock_trading_component_*`, `stock_trading_portfolio.py`. Kontrakt sizingu i normalizacji historii: `docs/STOCK_TRADING_FIXED_NOTIONAL_PL.md` / `_EN.md`. Historia publiczna pokazuje nominał 5K, ułamkową quantity i P&L liczony od tej wspólnej bazy.

## 7. Shared Learning / Evolution Fabric

**Obecny stan:** istnieje rozproszona, rzeczywiście zaimplementowana warstwa uczenia. Nie istnieje jeszcze jeden monolityczny moduł runtime o nazwie `AXIOM Evolution Kernel`.

| ID | Moduł | Odpowiedzialność | Przykładowa implementacja |
|---|---|---|---|
| `LE-01` | Experience Store | Prospektywne doświadczenia, selected/rejected/position experiences | `experience_store.py`, `experience_store_multi_source.py`, `stock_trading_v2_experience_store.py` |
| `LE-02` | Learning Ledger / Outcome Loop | Immutable decyzja/forecast przed outcome, późniejsze outcome binding | `learning_ledger.py`, `learning_outcome_loop.py`, `LEARNING_OUTCOME_LOOP_INTEGRATION.md` |
| `LE-03` | MISS / Regret | Analiza błędów także dla NIEwybranych kandydatów | `daily_stock_miss_engine.py`, `stock_trading_v2_regret_engine.py`, rejected-candidate outcome/attribution modules |
| `LE-04` | Hypothesis / Experiment Registry | Hipoteza -> eksperyment -> lesson | `lesson_hypothesis_registry.py`, `hypothesis_experiment_compiler.py`, `experiment_registry.py`, `experiment_result_lesson_loop.py` |
| `LE-05` | Replay / Counterfactual Labs | Ocena alternatyw bez przepisywania prawdziwej historii | `stock_trading_v2_outcome_replay.py`, `gpw_loco_counterfactual_replay.py`, `investments_wes_counterfactual.py`, `brace_portfolio_counterfactual_lab.py` |
| `LE-06` | Champion / Challenger + Statistical Gate | Porównuje przyszłościowe challengery, sample gates, OOS/holdout, promotion conditions | `daily_stock_champion_challenger.py`, `stock_trading_component_champion.py`, `statistical_promotion_gate.py`, `statistical_promotion_gate_v2.py` |
| `LE-07` | Autonomous Policy Loop | Observatory -> validation -> bounded promotion/materialization | `autonomous_policy_observatory.py`, `autonomous_policy_promotion.py`, `autonomous_policy_closed_loop.py`, policy materializers/overlays |
| `LE-08` | Engine-specific self-learning | Lokalna pętla dla BRACE, Daily Stock, EURUSD, WES itd. | `brace_portfolio_self_learning.py`, `daily_stock_self_improvement.py`, `daily_eurusd_abc_learning.py`, `gse_v2_learning_loop.py` |
| `LE-09` | Integrity / anti-hindsight | Zero retroactive live history, timestamp integrity, activation boundaries, workflow airlocks | `no_retroactive_execution.py`, `daily_stock_timestamp_integrity.py`, `promotion_learning_integrity.py`, verification scripts |

### Kanoniczna pętla ewolucji

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

Nie każdy silnik implementuje dziś każdy krok w identyczny sposób. Architecture Map opisuje wspólny wzorzec oraz lokalne implementacje.

## 8. Content / research publication layer

| ID | Moduł | Rola | Główne elementy |
|---|---|---|---|
| `CT-01` | News pipeline | Bilingual news ingestion, quality, dedupe, summaries/publication | `fetch_news_pl.py`, `fetch_news_en.py`, news source/quality/intelligence modules |
| `CT-02` | AI Outlook | Governed daily outlook, provider/freshness/status/metrics | `ai_outlook_engine.py` + `ai-outlook-*` workflows/data |
| `CT-03` | AI Tournament | Niezależna warstwa porównania/submission/rounds/public UI | `ai_tournament_engine.py`, intake/bootstrap/UI modules |
| `CT-04` | AXIOM Thought | Publikacja myśli/motta z guardem jakości | `axiom_thought_guard.py`, `publish_axiom_thought.py` |
| `CT-05` | Public projections / UI | Sanitized JSON/JS -> PL/EN pages | liczne `*_public_projection.py`, JS renderers, `pl/`, `en/` |
| `CT-06` | Home / editorial | Home brief, market signal, Hot X, editorial rules | home build pipelines, `briefrooms_editorial_rules.md`, content contracts |

Warstwa publikacyjna **nie może stać się ukrytym źródłem authority dla silnika decyzyjnego**. Publiczny renderer pokazuje stan; nie powinien tworzyć lub retroaktywnie zmieniać decyzji ekonomicznej.

## 9. Runtime / automation layer

`GitHub Actions` jest istotną częścią runtime BriefRooms. Workflowy obejmują m.in.:

- Belief Core / Epistemic / causal graph / calibration,
- BRACE entity + portfolio,
- legacy GPW/US Daily compatibility i settlement,
- Daily EUR/USD,
- Stock Trading v2 discovery/learning/validation/production,
- Weekly/WES,
- GSE,
- Autonomous Policy Observatory/Promotion/Closed Loop,
- Learning Outcome Loop,
- AI Outlook,
- AI Tournament,
- AXIOM Thought,
- publication, freshness guardians i automation health.

### State boundaries

1. **Public repo state** — tylko dane przeznaczone do wersjonowania/publicznej projekcji.
2. **Private GitHub Actions artifacts** — m.in. surowa/cumulative learning history i shadow state tam, gdzie dokumentacja tego wymaga.
3. **Append-only / hash-chained ledgers** — tam, gdzie integralność historii jest częścią kontraktu.
4. **Cache** — nie jest authority dla historii decyzji.
5. **Public projection** — sanitized derivative, nigdy pierwotny ledger decyzji.

Przykłady prywatnego durable state: Learning Outcome Loop oraz GSE/Belief shadow artifacts. Portfolio10K/BRACE posiada jawnie opisane pliki registry, shadow log, promotion history i paper portfolio.

## 10. Nienaruszalne invarianty systemowe

1. **NO RETROACTIVE LIVE HISTORY.** Trade/forecast/decision może być uznany za rzeczywisty tylko wtedy, gdy został zamrożony prospektywnie przed outcome. Replay po fakcie jest `SIMULATED/COUNTERFACTUAL`, nigdy `LIVE/EXECUTED`.
2. **Point-in-time lineage.** `observed_at <= received_at <= created_at <= decision_at` w obowiązującym kanonicznym path, z tylko jawnie zdefiniowaną tolerancją clock skew.
3. **Frozen means immutable.** Frozen forecast, admission, closed trade history i immutable decision event nie mogą być przepisywane po poznaniu wyniku.
4. **Missing != neutral.** Brak danych nie staje się `0`, neutralnym evidence ani syntetycznym quote.
5. **Source engine owns the decision.** Adapter, DecisionEnvelope, renderer i Learning Ledger nie przejmują authority silnika źródłowego.
6. **Risk remains per-engine.** Wspólny kontrakt nie homogenizuje limitów GPW, US, EURUSD, WES, BRACE itd.
7. **Belief influence is gated.** Sam fakt istnienia Belief Core nie daje mu prawa zmienić decyzji konkretnego engine bez jawnego WITH/WITHOUT / read-only / promotion gate.
8. **No silent auto-tuning.** Belief calibration może rekomendować; zmiana produkcyjnej polityki wymaga jawnej pętli challenger/promotion odpowiedniej dla danego modułu.
9. **No hindsight learning.** Outcome może być związany z decyzją/forecastem tylko, jeśli upstream event istniał przed poznaniem outcome zgodnie z activation boundary.
10. **Selected AND rejected matter.** Tam, gdzie wspiera to engine, odrzucone kandydaty są zamrażane i analizowane pod kątem MISS/opportunity regret.
11. **Production vs shadow is explicit.** Shadow output nie może być prezentowany jako historycznie wykonana transakcja produkcyjna.
12. **PL/EN architecture sync.** Zmiana architektury aktualizuje obie wersje mapy i wymaganą dokumentację.
13. **Stock Trading fixed notional + normalized history.** Każda nowa pozycja `TR-04` ma stały nominalny rozmiar 5 000 PLN (GPW) albo 5 000 USD (US), zapisany przez `FIXED_NOTIONAL_V1`. Zamknięta historia — także legacy — jest porównywana przez jawne derived analytics `FIXED_NOTIONAL_HISTORY_V1`, gdzie `history_normalized_quantity = 5000 / entry`. Ceny, timestampy i execution facts pozostają immutable.\n14. **Architecture bootstrap before architecture work.** AI/agent rozpoczyna zmianę od `AGENTS.md` i kanonicznej mapy, a nie od pamięci rozmowy ani izolowanego wyszukiwania kodu.

## 11. Authority map — kto czego NIE może robić

```text
Adapter              -> może tłumaczyć/oceniać dane; NIE handluje
Belief Core           -> może utrzymywać beliefs/forecasts; NIE wykonuje trade
Epistemic Bridge      -> może dostarczyć state; NIE przejmuje decyzji bez gate
DecisionEnvelope      -> zapisuje lineage; NIE rankuje i NIE wybiera instrumentu
RiskPolicy Contract   -> standaryzuje assessment; NIE narzuca jednego globalnego limitu
Learning Ledger       -> obserwuje/freeze/outcome; NIE przepisuje source state
Replay/Counterfactual -> symuluje alternatywę; NIE tworzy historycznej transakcji
LLM                   -> może interpretować/proponować; NIE może samodzielnie promować tam,
                         gdzie governance wymaga deterministic/statistical gate
Public UI             -> renderuje stan; NIE jest źródłem decyzji
```

## 12. Status ważniejszych subsystemów na snapshot 1.4

- **Belief Core v2:** engineering-complete dla shadow data collection; decision-independent.
- **Evidence adapters:** rzeczywista modularna warstwa; podstawowe market/technical/liquidity/regime adapters są deterministyczne.
- **GSE v1:** shadow-only forecasting; bez execution i auto-tuning authority.
- **Legacy Daily GPW / US:** nie są już aktywnymi produktami stock-tradingowymi; pozostają śledzalne jako deprecated migration/research/settlement paths.
- **Daily EUR/USD:** odrębny Daily engine, własny learning/lifecycle; canonical rollout partial.
- **Stock Trading v2:** **aktywny production Champion w fazie CANARY** dla GPW i US. V1 jest Challengerem/rodzicem rollback, a legacy candidate admission jest wyłączone.
- **Weekly v4:** experimental paper-trading research layer.
- **Portfolio 10K:** zachowany baseline/champion.
- **BRACE Portfolio:** `ACTIVE_BASELINE + BRACE_SHADOW`, z deterministic controller i fallbackiem.
- **Shared learning fabric:** istnieje i jest aktywnie rozwijany; nie jest jeszcze jednym zunifikowanym Evolution Kernel.

## 13. Planowana warstwa ponad obecną architekturą

### `FUT-01` — AXIOM Evolution Kernel / Meta-Exploration

Status: **kierunek architektoniczny, nie należy mylić z istniejącym pojedynczym runtime module**.

Celem jest orkiestracja istniejących pętli, a nie ich zastąpienie:

```text
Experience Graph / Learning Fabric
 -> Regret & failure attribution
 -> wybór modułu o największym expected improvement
 -> bounded Challenger Factory dla tego modułu
 -> Replay / Holdout / Shadow
 -> component-level Promotion Gate
 -> Composite Champion
```

Potencjalne moduły ewolucji: adaptery, evidence assessment, belief update/calibration, candidate discovery, ranking, entry, risk, exit, portfolio, event intelligence. Każdy moduł musi mieć lokalną metrykę jakości oraz end-to-end metrykę wpływu na system.

## 14. Jak AXIOM i inni agenci mają korzystać z mapy

Każda nowa sesja lub agent pracujący nad architekturą BriefRooms zaczyna od repozytoryjnego `AGENTS.md`. Następnie:

1. Otwiera tę kanoniczną mapę przed projektowaniem zmiany.
2. Identyfikuje dotknięte `module_id`.
3. Otwiera wskazane dokumenty szczegółowe zamiast rekonstruować architekturę z pamięci rozmowy.
4. Sprawdza rzeczywisty kod runtime, readers/writers, workflowy, state i testy oraz wyszukuje istniejące/nakładające się możliwości.
5. Określa impact surface: kontrakty, authority, state ownership, upstream/downstream, migrację, rollback i wymagane testy.
6. Przy zmianie architektury aktualizuje odpowiednie sekcje mapy PL+EN w tym samym PR.
7. Nowy subsystem powstaje dopiero po wykazaniu braku równoważnej funkcji; dostaje stabilny `module_id` oraz responsibility, authority, inputs, outputs, state, learning mode, safety invariants i code/docs.

## 15. Dokumenty źródłowe mapy

Najważniejsze dokumenty szczegółowe użyte do utworzenia v1.0:

- `AGENTS.md`\n- `scripts/validate_architecture_bootstrap.py`\n- `ARCHITECTURE_DOCUMENTATION_POLICY_EN.md` / `_PL.md`
- `BELIEF_CORE.md`
- `BELIEF_EVIDENCE_ADAPTERS.md`
- `BELIEF_EPISTEMIC_STATE.md`
- `BELIEF_EPISTEMIC_CAUSAL_GRAPH.md`
- `CANONICAL_INSTRUMENT_REGISTRY.md`
- `CANONICAL_MARKET_SNAPSHOT.md`
- `CANONICAL_DECISION_ENVELOPE.md`
- `DAILY_TRADING_ARCHITECTURE.md`
- `stock-trading-v2-architecture.md`
- `stock-trading-v2-production-promotion.md`
- `STOCK_TRADING_FIXED_NOTIONAL_PL.md` / `_EN.md`
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

- 2026-09-18: aktywna architektura stock trading została skonsolidowana pod `TR-04 Stock Trading v2`.
- `TR-01 Daily GPW` i `TR-02 Daily US` zachowują stabilne ID wyłącznie dla traceability i są oznaczone `DEPRECATED AS PRODUCT / REPLACED_BY TR-04`.
- Nie usuwamy ich kodu ani historycznych ledgerów w ramach tej zmiany, ponieważ część ścieżek nadal pełni funkcje research, settlement, compatibility lub rollback lineage. Ich output nie może być traktowany jako production admission do portfela v2.
- Produkcyjny authority określa `data/investments/stock_trading_policy.json`: `champion_engine=v2`, `challenger_engine=v1`, `legacy_candidate_admission_enabled=false`.

---

**Maintenance invariant:** ten plik i `ARCHITECTURE_MAP_EN.md` są jednym logicznym artefaktem. Nie wolno aktualizować tylko jednej wersji.