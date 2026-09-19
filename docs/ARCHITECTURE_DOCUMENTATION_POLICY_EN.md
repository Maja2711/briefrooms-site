# BriefRooms Architecture Documentation Policy — EN

## Rule

Every change that modifies BriefRooms architecture must be documented in both English and Polish in the same pull request.

An architecture change includes, at minimum: new or changed canonical contracts, authority boundaries, data-flow boundaries, runtime components, learning/verification loops, engine interfaces, persistence semantics, migration boundaries, or safety invariants.

## Required pairing

For an architecture document `docs/<NAME>_EN.md`, the same pull request must contain the semantically equivalent `docs/<NAME>_PL.md`, and vice versa.

The two versions do not need to be literal translations, but they must describe the same architecture, invariants, migration scope and authority boundaries.

## Canonical Architecture Map requirement

`docs/ARCHITECTURE_MAP_EN.md` and `docs/ARCHITECTURE_MAP_PL.md` are the canonical navigation maps of the whole BriefRooms architecture and form one logical artifact.

Every pull request that changes architecture MUST update both map files in the same pull request. The relevant stable `module_id`, responsibility, authority boundary, inputs/outputs, state/learning mode, implementation references and safety invariants must remain accurate after the change.

A new subsystem must receive a stable `module_id`. A replaced or retired subsystem must remain traceable through an explicit migration/deprecation/status update rather than disappearing silently from the map.

If an architecture change does not require a top-level dependency change, the map must still be reviewed and its affected module/status/reference entry updated when needed. Architecture drift between runtime code and the map is treated as a defect.

## Mandatory AI / agent bootstrap

The repository-root `AGENTS.md` file is the mandatory entry point for AI and coding agents. Before designing or implementing a change that touches architecture, trading, learning, Belief/Epistemic, decision logic, risk, workflows, persistence, promotion/rollback, or execution, the agent must first open the canonical Architecture Map, identify the affected `module_id` set, read the referenced detailed documentation, and only then inspect the implementation.

An agent must not reconstruct BriefRooms architecture from conversational memory or start a new subsystem before checking whether equivalent or overlapping capability already exists. `README.md` keeps a visible pointer to this bootstrap, while `scripts/validate_architecture_bootstrap.py` and the `Architecture Bootstrap Guard` workflow protect its presence and PL/EN map-version synchronization.

## Semantic runtime conformance

From Architecture Reconciliation 1.5 onward, PL/EN version equality alone is insufficient. `scripts/validate_architecture_reconciliation.py` checks selected critical runtime facts, authority boundaries and wiring against the map. `Architecture Bootstrap Guard` also runs this check on a schedule to detect drift between `main` and the active research branch.

## Pull-request requirement

An architecture PR is incomplete until both language versions exist and both canonical Architecture Maps accurately describe the resulting system. Runtime code is the implementation source of truth; the paired architecture documents and Architecture Map are the human-readable design record.

## Scope

This policy applies prospectively from PR32A onward. It does not require backfilling every historical BriefRooms architecture document. The canonical Architecture Map requirement applies prospectively from Architecture Map v1.0 onward.
