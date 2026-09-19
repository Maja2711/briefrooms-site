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

## Bootstrap integrity

`scripts/validate_architecture_bootstrap.py` and the `Architecture Bootstrap Guard` workflow protect this bootstrap from silent removal or PL/EN map-version drift. They do not replace architectural review.
