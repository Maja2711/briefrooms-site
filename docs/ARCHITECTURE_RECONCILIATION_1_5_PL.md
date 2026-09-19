# Architecture Reconciliation 1.5 — raport zgodności

## Cel

Reconciliation 1.5 zamyka drift wykryty pomiędzy kanoniczną Architecture Map, `main`, runtime GitHub Actions i gałęzią badawczą `stock-trading-v2`.

Punkt odniesienia produkcji: `main` od `331314840c1f1ddeccd814b46df688ccf9971d2b`.
Punkt odniesienia research branch po fazie 1: `4690ecd1597956b16549913258d8ab57380b70ee`.

## Rozstrzygnięta topologia branch/runtime

```text
main
  = production + governance + orchestration authority
  = canonical portfolio / policy / Champion / promotion / execution paths
              |
              | read-only production snapshots
              v
stock-trading-v2
  = research + evidence runtime
  = discovery / Trigger / Deep BELIEF proxy / outcomes / regret
  = Challenger Factory / replay / holdout evaluation
              |
              | exact bounded candidate evidence
              v
main: Stock Trading Component Promotion
  = sole Stock Trading production-promotion gate
```

Gałąź `stock-trading-v2` nie może bezpośrednio mutować `main`.

## Trigger / Relationship Engine

Default-branch schedulery zostały zrównane z runtime research branch. Scheduled `Stock Trading v2 Continuous Discovery` uruchamia teraz:

```text
Stage Zero
 -> Opportunity Frontier
 -> Market Relationship / Trigger
 -> max 6 attention slots
      max 4 trigger + 2 exploration
 -> max 2 Trigger-directed Deep BELIEF proxy
 -> broad Deep Evidence Champion
 -> Opportunity Engine
 -> immutable research state
```

Closed Learning Loop rozlicza prospective Trigger outcomes 1/3/5/20 sessions i łączy targeted deep research wyłącznie przez exact `trigger_observation_id`.

Trigger i targeted Deep BELIEF pozostają research/shadow i nie mają production decision authority.

## Promotion ownership

Usunięto bezpośredni `stock_trading_v2_auto_promote.py` z research branch.

Jedynym production promotion authority dla komponentów Stock Trading jest:

`main:.github/workflows/stock-trading-component-promotion.yml`

Ścieżka wymaga production-owned candidate intake, exact evidence binding, aktualnej rewizji Championa, health check i rollback.

Legacy Autonomous Policy Closed Loop nie ma już prawa materializować stockowych progów do produkcji. Jego runtime config ustawia `automatic_materialization_enabled=false`; metodologia PR35/PR36 pozostaje jako research/observatory lineage.

## Skorygowane fakty runtime

- Stock Trading v2: `PRODUCTION CHAMPION — FULL`.
- BRACE Portfolio: `PROBATIONARY_CONTROL`, paper-only.
- GSE: v1 pozostaje forecasting foundation, aktywny hourly research/learning runtime to GSE v2.
- WES: `NO_TRADE` jest pełnoprawnym stanem; `mandatory_monday_position=false`, `continuous_position_required=false`.
- ARIS: aktywny read-only `research_shadow` subsystem przy Belief Core.
- Learning Loop v2: współdzielona read-only warstwa diagnostyczna, bez production authority.

## Drift prevention

`scripts/validate_architecture_reconciliation.py` sprawdza semantycznie:

- production phase/status,
- branch authority,
- Trigger wiring,
- WES NO_TRADE,
- brak drugiego production writera,
- zgodność default-branch workflowów z research branch,
- obecność aktualnych faktów w mapach PL/EN.

`Architecture Bootstrap Guard` uruchamia ten test na PR, push do `main` i cyklicznie.

## Invariant

Architecture Map jest kanoniczną mapą nawigacji, ale runtime pozostaje implementacyjnym źródłem prawdy. Reconciliation ma wymuszać ich zgodność, nie zastępować inspekcji kodu.
