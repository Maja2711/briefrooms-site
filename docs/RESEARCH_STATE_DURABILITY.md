# Research State Durability v1

## Purpose

BriefRooms research layers are prospective, append-oriented systems. Their state is part of the epistemic record: activation boundaries, evidence cursors, frozen forecasts, verification history, World State snapshots, WITH/WITHOUT pairings and epistemic graph snapshots must not silently disappear and restart as a new first run.

Before this contract, cumulative research state lived only in GitHub Actions artifacts. Artifact retention is finite, so an expired or missing artifact could make a producer behave like a first run. That is unacceptable for anti-hindsight research.

Research State Durability v1 makes state continuity explicit and fail-closed.

## Scope

The canonical durability registry covers:

- PR10 Broad-Market Belief
- PR11 Sector-Factor Belief
- PR12 Company-Entity Framework
- PR13 Primary-Source Evidence
- PR14 Evidence Interpretation
- PR15 Entity Belief / Forecast
- PR16 Calibration Diagnostics
- PR16.1 Investment Semantics World State
- PR17 Entity Belief WITH/WITHOUT Bridge
- PR19 Epistemic / Causal Graph

Trading, execution, Portfolio 10K operational state and future PR20 causal testing are outside this contract.

## Privacy boundary

The BriefRooms repository is public. Raw research state must therefore never be committed to a Git branch or repository path.

Durability v1 uses private GitHub Actions artifacts only. The durability tooling has `contents: read` and `actions: read`; it does not require repository-content write permission and does not run `git push`.

A future external backend may be considered only if it is private by construction and receives a separate secrets, access-control and retention review.

## Storage model

Each research layer keeps two private artifact leases:

1. **Primary compatibility artifact** — the existing artifact name consumed by current workflows.
2. **Independent durability checkpoint** — `research-state-durability-<layer>`.

`Research State Durability Heartbeat` runs weekly and refreshes both leases. The heartbeat restores a valid producer artifact when one exists; otherwise it can renew from the previous independent durability checkpoint. It does not recompute beliefs, forecasts, outcomes or graph state.

This is a renewable private lease, not a claim that GitHub Actions artifacts are permanent archival storage.

## Integrity manifest

Every producer run seals its state before packaging. The state contains:

- `RESEARCH_STATE_DURABILITY_MANIFEST.json`
- `RESEARCH_STATE_CHECKPOINT_HISTORY.jsonl`

The manifest records:

- schema and layer identity,
- checkpoint timestamp,
- parent checkpoint ID,
- producer workflow, run ID, attempt and head SHA,
- SHA-256 and byte size for every research payload file,
- aggregate payload hash,
- explicit durability governance flags.

The checkpoint ID is content-derived from the manifest body. The previous checkpoint is retained as an append-only compact lineage row before the next checkpoint is sealed.

The manifest and history are durability metadata; they do not change belief probability, confidence, forecast content, decision output or economic attribution.

## Restore contract

A producer first performs its legacy restore for backward compatibility, then the durability guard becomes authoritative.

The guard searches multiple candidates rather than trusting the newest artifact blindly. A primary candidate is accepted only when it comes from a successful `main` run of the canonical producer workflow. A durability checkpoint is accepted only when it comes from a successful `main` run of `Research State Durability Heartbeat`.

For sealed state, hashes, required files, checkpoint identity and parent lineage must verify before the state is copied into the producer working directory.

Legacy pre-durability producer artifacts can be restored once as `legacy_unsealed` and are sealed after the next successful producer run. The first durability manifest marks that it migrated from legacy state without pretending that a cryptographic parent existed before activation of this contract.

## Fail-closed policy

If neither a valid canonical producer artifact nor a valid independent durability checkpoint can be restored, the producer fails before changing research state.

It must not silently interpret missing history as:

- a new activation boundary,
- an empty evidence cursor,
- no prior forecasts,
- no prior World State snapshots,
- no prior WITH/WITHOUT pairs,
- no prior epistemic graph snapshots.

This is deliberately stricter than availability. Preserving epistemic integrity is more important than producing a fresh-looking research report from a broken lineage.

## Heartbeat semantics

The heartbeat:

- restores existing research state,
- verifies it,
- seals only an unsealed legacy artifact during migration,
- repackages the same research payload,
- refreshes the compatibility artifact,
- refreshes the independent checkpoint.

It does not run any research engine and does not create new observations, evidence, beliefs, forecasts, verifications, decisions, pairings or causal claims.

## Anti-hindsight invariants

Research State Durability v1 does not authorize:

- historical backfill,
- forecast rewriting,
- verification rewriting,
- retroactive World State binding,
- retroactive epistemic contract binding,
- P&L tuning,
- automatic promotion,
- causal proof,
- decision influence.

Its only responsibility is continuity, provenance and integrity of state that the existing research layers already produced.

## Operational limitation

The weekly heartbeat is designed to keep finite-retention private artifacts continuously renewed. If GitHub Actions or the repository is unavailable or disabled for longer than the artifact-retention window, GitHub may still delete every copy.

Durability v1 does not pretend otherwise. In that situation the correct behavior is `FAIL_CLOSED`, not reconstruction from hindsight.

A true off-platform archival backend can be added later if the risk warrants it, but only as a separate reviewed architecture change.
