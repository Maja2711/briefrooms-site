# Belief Epistemic State v1

## Purpose

`belief-epistemic-state-v1` closes the loop between Belief Core's provenance-aware evidence graph and downstream reasoning clients.

The layer is a **read-only epistemic compression**. It is intentionally not a second belief engine and does not recalculate or overwrite Belief Core. Its role is to expose a compact authoritative state while keeping every compressed conclusion traversable back to the evidence chain that produced it.

Canonical path:

```text
Epistemic State
  -> Belief
  -> Evidence
  -> Observation
  -> Source
```

No compressed conclusion may become a provenance dead end.

## Aggregate Authority Principle

The authoritative reasoning input is the aggregate `EpistemicState`, not whichever individual source happens to be most persuasive to an LLM.

Reasoning clients may:

- inspect;
- explain;
- challenge evidence;
- request a Belief Core recalculation.

Reasoning clients may not:

- override probability;
- override confidence;
- override source reliability;
- ignore the aggregate because one source appears persuasive;
- write into Belief Core;
- auto-tune policy.

If inspection reveals a bad or conflicting source, the correct control path is:

```text
LLM inspection
  -> structured challenge / recalculation request
  -> Belief Core recomputation
  -> new authoritative Epistemic State
  -> downstream reasoning
```

The LLM never privately substitutes its own posterior for the system posterior.

## Contribution scores

For each representative Evidence item the state computes a leave-one-out marginal probability contribution:

```text
contribution(E_i) = P(all representative evidence) - P(all except E_i)
```

This preserves the sign and approximate marginal importance of each representative evidence item without changing the Belief Core algorithm.

The score is diagnostic attribution, not causal proof.

## Delta state

The runtime keeps append-only `epistemic_state_history.jsonl` and compares the current probability with the newest prior projection for the same Belief.

This makes `delta_probability` a first-class field and supports questions such as:

- what changed?
- how much did the belief move?
- which evidence contributed most strongly to the move?

## Bounded drill-down

Drill-down is deterministic and capped. Default triggers are:

- confidence below `0.50`;
- contradiction above `0.55`;
- absolute probability delta above `0.15`;
- high-impact decision;
- audit status other than clean/ok.

Default hard bounds:

- maximum depth: `4`;
- maximum evidence per side: `3`;
- maximum sources: `6`;
- maximum reinspection cycles: `1`.

Maximum path:

```text
State
  -> Belief
  -> top supporting/opposing Evidence
  -> Observation / Source
  -> STOP
```

A client cannot request a depth greater than the contract maximum.

## Runtime files

The projection writes only private runtime artifacts:

- `epistemic_state.json` — latest compact state;
- `epistemic_state_history.jsonl` — append-only state history / delta basis;
- `epistemic_drilldown_index.json` — compact provenance navigation index.

No runtime state is committed to the public repository.

## Operational integration

`.github/workflows/belief-epistemic-state.yml` runs after every successful `Belief Core Live Shadow Collection` workflow, restores the exact Belief Core artifact produced by that run, builds the Epistemic State projection and stores a separate private `belief-epistemic-state` artifact.

This ordering guarantees that the epistemic projection is derived from an already-computed Belief Core state rather than racing or creating an alternative information set.

## Relationship to Investment World State v1

PR16.1 `investment-world-state-v1` remains the prospective investment-context snapshot/binding foundation. `belief-epistemic-state-v1` does not replace it.

The new layer solves a different problem: reversible compression and controlled reasoning access to Belief Core's internal provenance graph.

Future consumers may combine the two contracts, but neither contract silently changes the other's semantics or authority.

## Safety invariants

All remain false:

```text
decision_writeback_enabled = false
belief_core_writeback_enabled = false
llm_override_enabled = false
automatic_tuning_enabled = false
```

The state is an authoritative **read interface**, not an action or policy interface.
