# Stock Trading v2 Research Branch Bootstrap

This branch is a **research/evidence runtime branch**, not a production authority.

Before changing anything here:

1. Read the canonical repository bootstrap and architecture from `main`:
   - `git show origin/main:AGENTS.md`
   - `git show origin/main:docs/ARCHITECTURE_MAP_PL.md`
   - `git show origin/main:docs/ARCHITECTURE_MAP_EN.md`
2. Identify the affected `module_id` in the canonical map.
3. Read `docs/stock-trading-v2-architecture.md` on this branch.
4. Inspect relevant readers, writers, workflows, state and tests before editing.
5. Search for overlapping capabilities before adding a subsystem.

## Authority boundary

- `main` owns production state, production governance, execution/paper-control authority, Champion manifests and production promotion.
- `stock-trading-v2` owns research discovery, Trigger/Relationship research, immutable research evidence, prospective outcomes, Challenger generation and holdout evaluation.
- This branch may **never push a production mutation directly to `main`**.
- A research Challenger may only nominate an exact bounded deployment. Production admission is owned by the main-branch `Stock Trading Component Promotion` path.
- Arbitrary source-code promotion is forbidden.
- If this branch and the canonical map disagree, stop architecture-affecting work and reconcile against `main` first.

Do not reconstruct architecture from conversational memory.
