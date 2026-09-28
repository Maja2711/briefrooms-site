# BriefRooms Evolution Controller — architecture and governance

**Module ID:** LE-11  
**Runtime:** scripts/briefrooms_evolution_controller.py  
**Contracts:** scripts/briefrooms_evolution_contracts.py  
**Workflow:** .github/workflows/briefrooms-evolution-controller.yml  
**Public projection:** data/investments/evolution_controller_public.json  
**Canonical state:** data/investments/evolution_controller_state.json  
**Audit:** data/investments/evolution_controller_audit.jsonl

## 1. Purpose

The BriefRooms Evolution Controller closes the shared learning loop above the existing Shared Learning / Evolution Fabric. It does not replace Experience Store, Learning Ledger, local challengers, replay, OOS, or engine-owned production writers. It normalizes their outputs into one lifecycle:

Experience / forecast / decision → outcome / verification → failure, regret or pattern → hypothesis → EvolutionCandidate → prospective OOS / holdout → PromotionGate → ProductionVersion or reject → monitoring → RollbackEvent / retirement → next hypothesis.

The Controller is a governance and orchestration layer. It is not a signal model, trading engine, or execution engine.

## 2. Canonical contracts

EvolutionCandidate records a frozen proposed change with an activation boundary, source lineage, challenger version, target module, evaluator profile and promotion route.

PromotionGate records a prospective validation decision: minimum sample, observed sample, metrics, protected-slice checks and blockers. PASS below the minimum sample is forbidden.

ProductionVersion is the versioned lineage of an activated component and never grants trade-execution authority.

RollbackEvent records a version reversal with its reason and metrics without rewriting historical decisions or outcomes.

## 3. Implemented stages

### Stage 1 — canonical contracts
Implemented in scripts/briefrooms_evolution_contracts.py.

### Stage 2 — Belief Calibration as the first client
belief_closed_loop.py remains the local research loop. It detects a problem, freezes a calibration challenger and computes prospective OOS, but no longer writes production. A locally passing challenger is handed off as ready_for_evolution_controller.

The Evolution Controller applies the central gate again: prospective N >= 50, Brier relative improvement >= 5%, better Log Loss, ECE degradation no worse than 1 pp, accuracy degradation no worse than 3 pp, improvement in at least 3 of 4 stability blocks, and no material degradation in protected instrument/horizon slices.

Only a PASS may materialize a versioned calibration overlay in belief_core_production_overrides.json. Raw probability remains preserved as Control.

### Stage 3 — Belief v3 and Evidence Patterns
Belief v3 is compared with its declared control belief on matched prospective 24H outcomes. Discovery requires at least 50 resolved matched observations. A new OOS boundary is then frozen. Production activation requires 50 further OOS matched observations, at least 5% Brier improvement, better Log Loss and bounded ECE degradation.

Activation is recorded in belief_v3_production_registry.json. The registry does not grant trade execution; decision-engine influence requires an explicit consumer bridge.

Evidence Patterns remain ASSOCIATION_ONLY. A replicated/OOS pattern cannot directly alter production. The Controller converts it into a research hypothesis/challenger intent that must pass a further prospective test.

### Stage 4 — Experience Store → automatic hypothesis creation
The Controller consumes the canonical Experience Store derived from the immutable Learning Ledger. Repeated, sufficiently supported adverse experiences may automatically create a cross-domain research hypothesis. An outcome never mutates policy in the same cycle.

The existing lesson_hypothesis_registry.py retains its specialized PR35/PR36 contract. The Controller does not force semantically incompatible Belief/Evidence hypotheses into that schema; it keeps a shared hypothesis queue and delegates to the appropriate engine compiler/writer.

### Stage 5 — trading outcomes / P&L / regret
The Controller consumes Stock Trading v2 opportunity-regret hypotheses. When the research branch marks one eligible for challenger holdout, the Controller creates a trading_component_replacement EvolutionCandidate.

The Controller does not mutate Stock Trading production. It creates a delegated action for the main-owned Stock Trading Component Promotion, which remains the sole production component writer.

### Stage 6 — retirement and component replacement
For components materialized by the Controller, monitoring continues after promotion. Belief calibration overlays automatically roll back after at least 30 new outcomes when Brier relative degradation reaches 5% or ECE degradation reaches 5 pp. Belief v3 has analogous retirement versus matched Control. Stock Trading replacement/rollback remains owned by Stock Trading Component Promotion.

## 4. Authority

The Controller may register candidates and hypotheses, evaluate prospectively, apply central promotion gates and segment-safety checks, version components, materialize bounded Belief calibration overlays and the v3 activation registry, and delegate changes to engine-owned writers.

The Controller may not execute trades, set position sizing, weaken risk limits, rewrite frozen history, mutate Evidence/source provenance, or directly mutate Stock Trading production.

## 5. Anti-hindsight

Every challenger has an activation_boundary. Pre-boundary data may support discovery, but formal OOS/promotion evidence must occur after that boundary. Promotion cannot be earned through retrospective backfill.

## 6. Production routing

- Belief calibration → belief_core_probability_overlay.
- Belief v3 → belief_v3_production_registry; consumer influence requires an explicit bridge.
- Evidence Patterns → hypothesis/challenger only.
- Experience Store → hypothesis/challenger only.
- Stock Trading regret → Stock Trading Component Promotion.
- Other engines retain their own writers until an explicit Evolution Controller adapter exists.

## 7. Frontend

Decision LAB may render evolution_controller_public.json: candidates, OOS, gates, active versions, rollbacks, retirement, hypotheses and delegated actions. The frontend has no authority and never initiates promotion.
