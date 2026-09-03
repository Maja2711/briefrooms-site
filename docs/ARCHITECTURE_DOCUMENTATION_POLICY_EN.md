# BriefRooms Architecture Documentation Policy — EN

## Rule

Every change that modifies BriefRooms architecture must be documented in both English and Polish in the same pull request.

An architecture change includes, at minimum: new or changed canonical contracts, authority boundaries, data-flow boundaries, runtime components, learning/verification loops, engine interfaces, persistence semantics, migration boundaries, or safety invariants.

## Required pairing

For an architecture document `docs/<NAME>_EN.md`, the same pull request must contain the semantically equivalent `docs/<NAME>_PL.md`, and vice versa.

The two versions do not need to be literal translations, but they must describe the same architecture, invariants, migration scope and authority boundaries.

## Pull-request requirement

An architecture PR is incomplete until both language versions exist. Runtime code is the implementation source of truth; the paired architecture documents are the human-readable design record.

## Scope

This policy applies prospectively from PR32A onward. It does not require backfilling every historical BriefRooms architecture document.
