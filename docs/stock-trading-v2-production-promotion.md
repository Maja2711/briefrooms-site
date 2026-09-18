# Stock Trading v2 production promotion

## Status

**COMPLETED — Stock Trading v2 is the current production Champion.**

As of the current production policy:

- `champion_engine = v2`
- `challenger_engine = v1`
- `legacy_candidate_admission_enabled = false`
- the initial production phase is `CANARY`.

The previous architecture in which v1 remained Champion while v2 was shadow-only is historical and must not be used as the current system description.

## Current production contract

The v2 research/opportunity layer does not create historical fills.

Production admission occurs only through `scripts/stock_trading_v2_production_bridge.py`, which revalidates the current opportunity against:

- opportunity freshness,
- regular-session eligibility,
- execution-quote freshness,
- risk geometry,
- minimum reward/risk,
- current portfolio capacity.

The bridge searches the ranked candidate frontier until capacity is filled or eligible candidates are exhausted; it does not stop permanently because rank #1 failed one gate.

## Champion inversion

The completed transition is:

```text
historical state:
v1 Champion -> v2 Challenger/shadow

current state:
v2 Champion -> v1 Challenger
```

Former Daily GPW/US product paths are legacy compatibility/research/settlement paths. They cannot admit new production stock positions while `legacy_candidate_admission_enabled=false`.

## Canary and full phase

The current production config starts with one open position per market in CANARY and defines a full cap of three per market. Canary progression is governed by the production config and runtime state; it must not be inferred from documentation alone.

## Non-retroactive invariant

Promotion changes future routing only.

It never:

- rewrites historical v1 or v2 decisions,
- converts shadow/replay records into LIVE trades,
- fabricates earlier execution timestamps,
- changes closed trade history.

All future engine/challenger promotions must preserve the same point-in-time and NO RETROACTIVE constraints.
