# BriefRooms AI / Agent Bootstrap

This file is the mandatory entry point for any AI or coding agent working in this repository.

## Canonical navigation authority

Before proposing, designing, or implementing any change that touches architecture, trading, learning, Belief/Epistemic, decision logic, risk, workflows, persistence, promotion/rollback, or execution:

1. Read `docs/ARCHITECTURE_MAP_PL.md` first.
2. Use `docs/ARCHITECTURE_MAP_EN.md` as the synchronized English counterpart.
3. Identify the affected stable `module_id` or `module_id` set.
4. Read the detailed module documentation referenced by the map.
5. Inspect the actual runtime implementation, including relevant readers, writers, workflows, state files, and tests.
6. Search for existing or overlapping capability before creating a new subsystem, engine, bridge, ledger, learning loop, or authority path.
7. Establish the change-impact surface before editing code: contracts, authority, state ownership, upstream/downstream consumers, migration, rollback, and required tests.
8. Preserve all authority and safety invariants from the canonical Architecture Map.
9. If architecture changes, update BOTH `docs/ARCHITECTURE_MAP_PL.md` and `docs/ARCHITECTURE_MAP_EN.md` in the same change.

## Non-negotiable working rule

Do not reconstruct BriefRooms architecture from conversational memory.

The Architecture Map is the canonical navigation source. Runtime code remains the implementation source of truth. Detailed module documents define local design contracts. Tests and CI verify that the resulting implementation still satisfies those contracts.

Before creating a new subsystem, first demonstrate that equivalent or overlapping capability does not already exist in the mapped architecture.

If the Architecture Map cannot be read, do not make an architecture-affecting change until it is available.

## Instrument-scoped change isolation

When a task is explicitly scoped to one instrument or to presentation/quote freshness for one instrument, do not mutate decision state, pending entries, risk plans, results, or lifecycle state for any other instrument.

Repository pushes are validation events only for Weekly/WES/Risk execution workflows. Position mutation is allowed only from scheduled runtime, explicit manual execution, or an explicitly governed workflow-to-workflow execution path. UI, renderer, test, documentation, and live-quote changes must never obtain trade-state write authority as a side effect.

## Branch authority

For Stock Trading v2, branch authority is explicit:

- `main` is the sole production/governance authority. It owns canonical production state, production policy, Champion manifests, production promotion, execution/paper-control paths and architecture documentation.
- `stock-trading-v2` is a research/evidence runtime branch. It may own discovery state, Trigger/Relationship research, targeted Deep BELIEF proxy research, prospective outcomes, regret, Challenger generation and holdout evaluation.
- A workflow running research code may read frozen production snapshots from `main`, but it must not directly push a production mutation to `main`.
- Production promotion of Stock Trading components is owned by the main-branch `Stock Trading Component Promotion` workflow.
- Before changing `stock-trading-v2`, read that branch's `AGENTS.md` and the canonical map on `main`.

## Bootstrap integrity

`scripts/validate_architecture_bootstrap.py` and the `Architecture Bootstrap Guard` workflow protect this bootstrap from silent removal or PL/EN map-version drift. They do not replace architectural review.
