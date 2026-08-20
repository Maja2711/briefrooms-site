# Entity Semantic Eligibility — PR19.1

## Purpose

PR19.1 prevents sector classification from being treated as a business-model contract. In particular, membership in the `Financials` sector is not sufficient to activate bank-specific Entity Beliefs.

The semantic layer is research-shadow governance. It does not create trading authority, alter BRACE scores, tune probabilities to PnL, or retroactively rewrite historical research records.

## Canonical archetype source

The v1 contract uses the already-governed `exposure_key` carried by the canonical Entity universe. It does not infer business model from ticker, issuer name, price behaviour, sector alone, or post-hoc outcome information.

Reviewed mappings in v1 include:

- `diversified_banking`, `commercial_banking`, `retail_banking`, `universal_banking` → `bank`
- `payments_network` → `payment_network`
- `financial_data_ratings` → `financial_data_ratings`

An explicitly reviewed `entity_archetype` can override the registry. An unresolved Financials issuer remains `financials_unresolved` and fails closed.

## Bank-specific dimensions

The following dimensions require `entity_archetype == bank`:

- `net_interest_income_durability`
- `credit_quality`
- `deposit_funding`
- `capital_strength`

Common Entity dimensions remain available where their own evidence contracts permit them.

## Eligibility states

### Eligible

A resolved bank archetype can materialize and prospectively update bank-specific dimensions.

### Unresolved fail-closed

A Financials issuer without a resolved business archetype cannot create new bank-specific Beliefs, Evidence, forecasts, calibration observations, bridge inputs, or active causal-graph membership. This state is deliberately reversible: unresolved metadata is not evidence that the issuer is non-bank, so no permanent semantic deprecation is written.

### Resolved semantic mismatch

When a bank-specific Belief already exists but the governed archetype resolves to a non-bank business model, PR19.1 appends `DEPRECATED_SEMANTIC_MISMATCH` lineage.

Historical records are preserved. The migration must not delete or rewrite prior definitions, Evidence, frozen forecasts, verifications, graph snapshots, or their timestamps. Future use is disabled instead.

## Append-only migration boundary

For a resolved mismatch, downstream layers preserve historical provenance while blocking prospective authority:

- PR14 preserves old interpretations/Evidence and appends semantic deprecation lineage.
- PR15 preserves Belief Core definitions, Evidence, forecasts and verifications; an open affected forecast receives a terminal `semantic_deprecated` closure without mutating the frozen forecast.
- PR16 excludes deprecated records from new calibration inclusion.
- PR16.1 does not create new World State bindings for deprecated forecasts.
- PR17 does not use deprecated Entity forecasts in WITH/WITHOUT shadow decisions.
- PR19 preserves all prior epistemic graph snapshots and creates later snapshots without currently deprecated Beliefs.

A historical graph snapshot that once contained a now-deprecated Belief remains an immutable historical fact. PR19.1 records the semantic deprecation separately rather than editing the old graph.

## Anti-hindsight rules

PR19.1 must preserve these invariants:

1. no retroactive deletion or payload rewrite;
2. no post-hoc archetype inference from forecast or PnL outcome;
3. unresolved metadata cannot be converted into a permanent mismatch;
4. old graph snapshots remain immutable;
5. future eligibility is determined from the current governed semantic profile;
6. semantic deprecation has zero decision authority and zero promotion authority by itself.

## Runtime bootstrap

`BRACE Company-Entity Framework Shadow` remains the root of the Entity research pipeline. Its `push.paths` includes the shared semantic contract and all semantic downstream entrypoints. Therefore a semantic code change on `main` re-runs PR12; the existing `workflow_run` dependency chain then rebuilds later Entity research layers in order.

The separate `Entity Semantic Eligibility Validation` workflow is read-only CI. It compiles the semantic surface and runs the focused migration suite plus all affected layer regressions on pushes and pull requests that touch this contract.

## Reference examples

Expected v1 classification from the canonical taxonomy:

- JPM / `diversified_banking` → bank-specific dimensions eligible.
- Visa / `payments_network` → bank-specific dimensions ineligible; any historical bank-specific Belief is append-only deprecated.
- SPGI / `financial_data_ratings` → bank-specific dimensions ineligible; any historical bank-specific Belief is append-only deprecated.

These examples illustrate archetype semantics only. They do not imply any investment conclusion, forecast correctness, economic value, or causal validity.
