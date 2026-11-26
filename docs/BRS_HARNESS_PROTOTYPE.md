# BRs Harness prototype (not activated)

This branch is a non-runtime experiment, not a canonical architecture change.
Affected integration candidates: TR-03, TR-05, EP-05, EP-06, CF-07, LE-11.
Existing owners remain authoritative. No workflow dispatch, state mutation,
promotion, credentials, or production deployment is implemented.

The planner accepts normalized health observations supplied by a caller.
Missing evidence is UNKNOWN, unhealthy dependencies block downstream planning,
and independent WES monitoring is not blocked by the Daily dependency chain.
The graph is deliberately partial; it is not a complete runtime DAG.

Run from repository root:
`python -m unittest discover -s tests -p test_briefrooms_harness_core.py`

Before activation:
1. Inventory actual workflow readers/writers, locks and recovery routes.
2. Reuse existing watchdogs and observatory as evidence adapters; distinguish
   valid NO_TRADE and unqualified evidence from runtime failure.
3. Add timestamp/provenance validation, durable task identity, leases,
   bounded retries, independent verification and audit.
4. Validate in shadow; no dispatch until explicit allowlisted authority,
   deduplication and concurrency tests are approved.
5. Update both canonical architecture maps together before runtime integration.
6. Keep LE-11 promotion and all engine-owned execution paths unchanged.

Rollback: discard the experimental branch. No production state migration.
