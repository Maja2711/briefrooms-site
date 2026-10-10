# History Integrity Guard — independent history verification (staged)

Status: **CODE + TESTS ONLY; EXTERNAL IMMUTABLE ANCHOR NOT CONFIGURED**.
Authority: read-only verification of historical file bytes. No execution, calibration, outcomes, promotion, or rollback authority.

## Trust model
The repository and GitHub Actions are **not** an independent trust domain. A manifest stored only beside the ledger or in the same repository is not proof against an actor who can edit both. The SHA-256 checksum catches accidental damage but does NOT authenticate who created the manifest. Independent protection requires a separately administered WORM/Object Lock store with retention, write-once policy, versioning, restricted deletion/bypass, independently verifiable creation time, and an audit trail.

## Usage (operator)
1. Select the **real frozen ledger and settled-outcome files**. Do not anchor a rolling public projection and call it frozen history. Write their paths into a newline-delimited scope file.
2. After the source run closes a point-in-time slice, run:
   `python scripts/history_integrity_guard.py snapshot --root . --files-from scope.txt --output /tmp/unique-history-snapshot.json`
3. **Externally** upload the manifest **and the source file bytes** as immutable uniquely named objects into a separately permissioned store. Record externally generated object version IDs, trusted store timestamp and retention. The uploader must not have deletion / retention-bypass permissions. GitHub Actions alone must not be allowed to change the archive's policy.
4. Retrieve the independently anchored manifest, confirm its authenticity using provider metadata / independent identity, and run:
   `python scripts/history_integrity_guard.py verify --root . --manifest /tmp/trusted-history-snapshot.json`
5. Any missing object, invalid seal, changed byte, unavailable trusted anchor, or ambiguous history means **NO INTEGRITY PASS**, alert, and block use of the affected historical evidence for promotion. Do not fabricate a historical proof or retroactively freeze.
6. Run `python -m unittest tests.test_history_integrity_guard -v` before integration.

## Important limitations
- This module operates on explicitly selected existing files; it does not claim whole-repository or whole-ledger completeness.
- For volatile files, capture the exact historical snapshot object at a stable freeze boundary; verifying a newer rolling file against yesterday's manifest should fail by design.
- This implementation DOES NOT provision cloud storage, sign manifests, create independent timestamps, alert an external operator, or enforce downstream promotion blocking. Those require an independently owned store and configured consumer gate, and must be verified end-to-end before claiming production protection.
- Tests cover tampering, missing files, unsafe paths and manifest corruption, not external WORM enforcement.
