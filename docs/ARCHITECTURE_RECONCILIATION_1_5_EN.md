# Architecture Reconciliation 1.5 — conformance record

## Purpose

Reconciliation 1.5 closes drift between the canonical Architecture Map, `main`, GitHub Actions runtime and the `stock-trading-v2` research branch.

Production reference point: `main` from `331314840c1f1ddeccd814b46df688ccf9971d2b`.
Research-branch reference after phase 1: `4690ecd1597956b16549913258d8ab57380b70ee`.

## Resolved branch/runtime topology

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

The `stock-trading-v2` branch may not directly mutate `main`.

## Trigger / Relationship Engine

Default-branch schedulers are synchronized with the research runtime. Scheduled `Stock Trading v2 Continuous Discovery` now executes:

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

The Closed Learning Loop settles prospective Trigger outcomes at 1/3/5/20 sessions and joins targeted deep research only through exact `trigger_observation_id` lineage.

Trigger and targeted Deep BELIEF remain research/shadow with zero production decision authority.

## Promotion ownership

The direct `stock_trading_v2_auto_promote.py` path was removed from the research branch.

The sole Stock Trading component production-promotion authority is:

`main:.github/workflows/stock-trading-component-promotion.yml`

It requires production-owned candidate intake, exact evidence binding, current Champion revision checks, health checks and rollback.

The legacy Autonomous Policy Closed Loop no longer materializes stock thresholds into production. Its runtime config sets `automatic_materialization_enabled=false`; the PR35/PR36 methodology remains as research/observatory lineage.

## Corrected runtime facts

- Stock Trading v2: `PRODUCTION CHAMPION — FULL`.
- BRACE Portfolio: `PROBATIONARY_CONTROL`, paper-only.
- GSE: v1 remains the forecasting foundation; active hourly research/learning runtime is GSE v2.
- WES: `NO_TRADE` is first-class; `mandatory_monday_position=false`, `continuous_position_required=false`.
- ARIS: active read-only `research_shadow` subsystem adjacent to Belief Core.
- Learning Loop v2: shared read-only diagnostics layer with no production authority.

## Drift prevention

`scripts/validate_architecture_reconciliation.py` semantically verifies runtime status, branch authority, Trigger wiring, WES NO_TRADE, single-writer promotion ownership, default/research workflow synchronization and current facts in both Architecture Maps.

`Architecture Bootstrap Guard` runs it on pull requests, pushes to `main` and on a schedule.

## Invariant

The Architecture Map is the canonical navigation map; runtime remains the implementation source of truth. Reconciliation enforces alignment rather than replacing code inspection.
