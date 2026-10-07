# BriefRooms Provenance Contract v1

**Status:** P1 compatibility layer  
**Schema:** `briefrooms-provenance-contract-v1`  
**Authority:** metadata / lineage only — no decision, scoring, sizing, execution, promotion or policy-writeback authority.

## Purpose

BriefRooms previously used several valid but engine-specific provenance dialects. The v1 contract introduces one canonical `provenance` envelope while preserving existing engine payloads and legacy lineage fields.

Canonical covered families:

`Belief/L3-A -> WES -> Daily -> BRACE -> Stock Trading -> Shadow Engines`

The compatibility phase is deliberately read-only. Existing production artifacts are projected into the common contract as sidecars; they are not rewritten, resealed or backfilled.

## Canonical envelope

Every v1 envelope has exactly these semantic fields:

- `schema_version`
- `artifact_id`
- `artifact_type`
- `engine_id`
- `engine_version`
- `created_at`
- `parent_artifact_ids`
- `source_ids`
- `evidence_ids`
- `belief_ids`
- `decision_id`
- `forecast_id`
- `verification_id`
- `payload_hash`
- `authority`
- `prospective`
- `domain_provenance`

`payload_hash` is SHA-256 over the canonical source payload with only the new `provenance` field removed. Existing fields such as `execution_provenance`, WES seal metadata, L3-A attribution metadata and engine-specific lineage remain part of the hashed source artifact.

## Compatibility boundary

Implementation:

- `scripts/provenance_contract.py` — canonical envelope, deterministic hashing, validation and optional prospective attachment.
- `scripts/provenance_compat.py` — read-only compatibility adapters.

Adapters:

| Family | Existing lineage retained | v1 projection |
|---|---|---|
| Belief/L3-A | Evidence IDs, Belief IDs, question/intent/attribution, forecast and Verification IDs | `adapt_belief_l3a` |
| WES | frozen forecast, position leg, settlement and existing seal/hash-chain references | `adapt_wes` |
| Daily | Belief-first decision source, Epistemic source, used Beliefs/Evidence | `adapt_daily` |
| BRACE | entity activation lineage, belief definitions and source policy | `adapt_brace` |
| Stock Trading | existing `execution_provenance`, position/action IDs and source engine | `adapt_stock_trading` |
| Shadow Engines | engine/run/workflow/source observatory lineage | `adapt_shadow` |

## Non-regression guarantees

P1 must not:

1. change a direction, probability, score, ranking, threshold, entry, exit, sizing or settlement;
2. mutate an existing artifact in a compatibility adapter;
3. alter WES historical seal/hash-chain payloads;
4. fabricate a point-in-time timestamp when the source artifact has none;
5. convert a Shadow/measurement artifact into production authority;
6. perform retroactive provenance backfill that changes economic history.

If a legacy artifact lacks a timestamp required by the common contract, the adapter requires an explicit point-in-time timestamp from the caller and otherwise fails.

## Migration sequence

P1 is only the common contract plus compatibility adapters. Native write-time adoption should happen prospectively per engine in later changes:

1. emit the same v1 envelope at artifact creation time;
2. preserve the existing domain-specific metadata under its current fields and/or `domain_provenance`;
3. validate the common envelope in the engine's own CI;
4. only after prospective coverage is stable, consider stronger fail-closed publication gates.

Historical WES seals must never be rewritten merely to add v1 provenance.
