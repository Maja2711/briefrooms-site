# PR-C — Canonical DecisionEnvelope + per-engine RiskPolicy

## Roadmap status

- **P0.3 Canonical DecisionEnvelope:** `DONE — current scope`
- **P0.4 per-engine RiskPolicy:** `DONE — current scope`

These architecture items are closed for the current runtime scope after PR #492.
Remaining engine canonicalization is tracked as **rollout/migration work** and does
not reopen P0.3 or P0.4.

### Decision contract rollout / migration

- GPW Daily final decisions: `CANONICALIZED`
- US Daily new-entry and FLAT decisions: `CANONICALIZED`
- US post-entry HOLD/CLOSE marks: `PARTIAL` — post-entry mark snapshots are not yet P0.2 canonical
- EURUSD: `PARTIAL`
- WES: `PARTIAL`
- BRACE-SPX: `NOT_YET_CANONICALIZED`

Migration is incremental and should be completed when the corresponding engine
path is touched. It is an integration rollout, not a redesign of the canonical
DecisionEnvelope or per-engine RiskPolicy architecture.

## Purpose

PR-C standardizes the **decision record**, not the decision logic.

Every supported economic decision is bound prospectively to:

1. the source engine and engine version,
2. the exact `decision_at` timestamp,
3. the exact canonical action (`LONG`, `SHORT`, `HOLD`, `FLAT`),
4. the P0.2 `CanonicalMarketSnapshot` used by the decision when an economic exposure exists,
5. the P0.2 data-quality policy that admitted that snapshot,
6. the source engine's own RiskPolicy assessment,
7. the frozen position plan and optional EpistemicState lineage.

The envelope does not rank instruments, size trades, set thresholds, execute
orders or promote policies.

## Canonical contracts

### `briefrooms-decision-envelope-v1`

A DecisionEnvelope contains deterministic `envelope_id` and SHA-256 hash plus:

- `engine_id`
- `engine_version`
- `decision_at`
- `native_decision`
- canonical `action`
- `instrument_id` when applicable
- `market_snapshot_id` / `market_snapshot_hash` when applicable
- market-data policy and quality status
- `risk_assessment_id` / `risk_assessment_hash`
- `risk_policy_id` / `risk_policy_version`
- optional `epistemic_state_id`
- horizon, confidence and frozen position plan
- authority declaration proving that the source engine, not the envelope, owns the decision

For `LONG`, `SHORT` and `HOLD`, the contract fails closed unless the canonical
MarketSnapshot hash is valid, the market-data assessment is `OK`, and the
snapshot is still admissible at `decision_at` under the engine-specific P0.2
freshness policy.

`FLAT` decisions deliberately do not fabricate an instrument or snapshot.

### `briefrooms-risk-assessment-v1`

The shared module standardizes only the assessment shape:

- deterministic risk assessment ID/hash
- engine ID
- policy ID/version/fingerprint
- assessment timestamp
- action
- normalized checks
- status: `APPROVED`, `BLOCKED`, `NO_POSITION_RISK`

It contains **no shared trading thresholds**.

## RiskPolicy ownership

Risk values remain per-engine.

### GPW Daily

`gpw_daily_risk_policy.py` reads the existing GPW configuration and evaluates
only existing constraints such as:

- positive reference price,
- ordered entry zone,
- SL below reference price,
- TP above reference price,
- minimum configured R:R,
- maximum configured position-risk fraction,
- optional `skip_above` consistency,
- bounded 1–2 session horizon metadata.

The mandatory GPW policy can tighten only decisions explicitly tagged
`selection_mode=MANDATORY_DAILY_FINAL`. It is not applied to the primary GPW
path and is not a global BriefRooms risk policy.

### US Daily Stock

`us_daily_stock_risk_policy.py` owns its independent US limits from
`us_daily_stock_config.json` and emits the same normalized assessment contract.
The common contract does not copy GPW limits into US or vice versa.

## Persistence boundary

The producer integrations are fail-closed at persistence:

- `gpw_market_data.py` installs the GPW guard on `gpw.atomic_json`; both the
  primary and mandatory GPW paths import this module, so an invalid economic
  decision cannot be written to the canonical GPW public/history files.
- `daily_stock_us_adapter.py` installs the US guard on `us.atomic_json`; new
  US Daily entry/FLAT states are enveloped before canonical persistence.

The guard mutates only lineage/audit fields. It does not change score, selected
symbol, entry, SL, TP or R:R.

## Explicit coverage

PR-C does not pretend unfinished consumers are canonical. The authoritative
rollout status is the **Decision contract rollout / migration** section above.

A copied entry envelope, snapshot ID and instrument ID are explicitly removed
from a US HOLD/CLOSE publication rather than being misrepresented as current
HOLD lineage.

## Learning lineage boundary

The canonical GPW/US producer records now carry the new IDs prospectively.
The existing Learning Outcome Loop still materializes its own immutable decision
event payload. PR-C does **not** rewrite old ledger events and does not fabricate
lineage for Experience Store rows that predate this contract.

A later narrow migration may copy already-frozen producer IDs into newly-created
Learning Ledger decision events. That migration must preserve the permanent
activation boundary and same-cycle anti-hindsight rules. Until then, producer
persistence is canonical while downstream Experience Store IDs remain optional.

## Legacy policy

PR-C is prospective:

- no Learning Ledger reset,
- no Experience Store reset,
- no historical backfill,
- no fabricated `decision_envelope_id`,
- no fabricated `risk_assessment_id`,
- legacy records remain auditable as legacy/non-canonical.

## Out of scope

- centralized risk thresholds,
- Execution Simulation,
- broker execution,
- AlfaX/RL,
- automatic promotion/tuning changes,
- retroactive envelope generation,
- migration of EURUSD/WES/BRACE market snapshots,
- retroactive Learning Ledger / Experience Store enrichment.
