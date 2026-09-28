# BriefRooms Evolution Controller — architektura i governance

**Module ID:** LE-11  
**Runtime:** scripts/briefrooms_evolution_controller.py  
**Kontrakty:** scripts/briefrooms_evolution_contracts.py  
**Workflow:** .github/workflows/briefrooms-evolution-controller.yml  
**Public projection:** data/investments/evolution_controller_public.json  
**Stan kanoniczny:** data/investments/evolution_controller_state.json  
**Audit:** data/investments/evolution_controller_audit.jsonl

## 1. Cel

BriefRooms Evolution Controller domyka wspólną pętlę uczenia nad istniejącym Shared Learning / Evolution Fabric. Nie zastępuje Experience Store, Learning Ledger, lokalnych challengerów, replay, OOS ani engine-owned writerów. Normalizuje ich wynik do jednego cyklu życia:

Experience / forecast / decision → outcome / verification → failure, regret albo pattern → hypothesis → EvolutionCandidate → prospective OOS / holdout → PromotionGate → ProductionVersion albo reject → monitoring → RollbackEvent / retirement → kolejna hipoteza.

Controller jest warstwą governance i orchestration. Nie jest modelem sygnałowym, silnikiem tradingowym ani execution engine.

## 2. Kanoniczne kontrakty

EvolutionCandidate opisuje zamrożoną propozycję zmiany wraz z activation boundary, źródłem, challenger version, target module, evaluator profile i promotion route.

PromotionGate zapisuje prospektywną decyzję walidacyjną: minimum sample, observed sample, metryki, protected-slice checks i blockers. PASS poniżej minimum sample jest zabroniony.

ProductionVersion jest wersjonowanym śladem aktywowanego komponentu. Sam kontrakt nie nadaje trade execution authority.

RollbackEvent zapisuje cofnięcie wersji z przyczyną i metrykami. Rollback nie przepisuje historii ani dawnych decyzji.

## 3. Etapy wdrożonego loopu

### Etap 1 — kanoniczne kontrakty
Wdrożone w scripts/briefrooms_evolution_contracts.py.

### Etap 2 — Belief Calibration jako pierwszy klient
belief_closed_loop.py pozostaje lokalnym badaczem. Wykrywa problem, zamraża calibration challenger i liczy prospective OOS, ale nie zapisuje produkcji. Po spełnieniu lokalnych warunków przekazuje status ready_for_evolution_controller.

Evolution Controller ponownie sprawdza centralny gate: prospective N >= 50, Brier relative improvement >= 5%, lepszy Log Loss, ECE nie gorszy o więcej niż 1 pp, accuracy nie gorsza o więcej niż 3 pp, stabilność co najmniej 3 z 4 bloków i brak materialnej degradacji chronionych slice instrument/horizon.

Dopiero PASS może materializować wersjonowany calibration overlay w belief_core_production_overrides.json. Raw probability pozostaje zachowane jako Control.

### Etap 3 — Belief v3 i Evidence Patterns
Belief v3 jest porównywany z deklarowanym control belief na matched, prospektywnych 24H outcome. Discovery wymaga co najmniej 50 rozliczonych matched observations. Następnie zamrażany jest nowy OOS boundary. Production activation wymaga kolejnych 50 OOS matched observations oraz minimum 5% poprawy Brier, lepszego Log Loss i ograniczenia degradacji ECE.

Aktywacja v3 trafia do belief_v3_production_registry.json. Rejestr nie daje trade execution authority; decision-engine influence wymaga jawnego consumer bridge.

Evidence Patterns pozostają ASSOCIATION_ONLY. Pattern z replikacją/OOS nie może bezpośrednio zmienić produkcji. Controller zamienia go na hipotezę/challenger research intent, który musi przejść dalszy prospective test.

### Etap 4 — Experience Store → automatic hypothesis creation
Controller czyta canonical Experience Store zbudowany z immutable Learning Ledger. Powtarzalne, wystarczająco liczne niekorzystne doświadczenia mogą automatycznie utworzyć cross-domain research hypothesis. Outcome nie mutuje polityki w tym samym cyklu.

Istniejący lesson_hypothesis_registry.py zachowuje wyspecjalizowany kontrakt PR35/PR36. Controller nie wciska do niego semantycznie niepasujących hipotez Belief/Evidence; utrzymuje wspólną kolejkę hipotez i deleguje do właściwego engine compiler/writera.

### Etap 5 — trading outcomes / P&L / regret
Controller konsumuje Stock Trading v2 opportunity-regret hypotheses. Gdy research branch oznacza hipotezę jako gotową do challenger holdout, powstaje EvolutionCandidate typu trading_component_replacement.

Controller nie mutuje Stock Trading production. Tworzy delegated action do main-owned Stock Trading Component Promotion, który pozostaje jedynym writerem produkcyjnego komponentu Stock Trading.

### Etap 6 — retirement i component replacement
Dla komponentów materializowanych przez Controller monitoring trwa po promocji. Belief calibration overlay ma automatyczny rollback po minimum 30 nowych outcome przy relatywnej degradacji Brier >= 5% lub ECE >= 5 pp. Belief v3 ma analogiczny retirement względem matched Control. Stock Trading replacement/rollback pozostaje wykonywany przez Stock Trading Component Promotion.

## 4. Authority

Controller może rejestrować kandydatów i hipotezy, oceniać prospective, prowadzić centralne promotion gates i segment-safety, wersjonować komponenty, materializować bounded Belief calibration overlay i v3 activation registry oraz delegować zmianę do engine-owned writera.

Controller nie może wykonywać transakcji, ustalać sizingu, osłabiać risk limits, przepisywać frozen history, zmieniać Evidence/source provenance ani bezpośrednio mutować Stock Trading production.

## 5. Anti-hindsight

Każdy challenger ma activation_boundary. Dane sprzed boundary mogą służyć discovery, ale formalny OOS/promotion evidence musi pochodzić z okresu po boundary. Promocja nie może być przyznana przez retroaktywny backfill.

## 6. Production routing

- Belief calibration → belief_core_probability_overlay.
- Belief v3 → belief_v3_production_registry; consumer influence wymaga jawnego bridge.
- Evidence Patterns → hypothesis/challenger only.
- Experience Store → hypothesis/challenger only.
- Stock Trading regret → Stock Trading Component Promotion.
- Inne engine'y zachowują własnych writerów do czasu jawnego adaptera Evolution Controller.

## 7. Frontend

Decision LAB może pokazywać evolution_controller_public.json: kandydatów, OOS, gates, aktywne wersje, rollbacki, retirement, hipotezy i delegated actions. Frontend nie ma authority i nie inicjuje promocji.
