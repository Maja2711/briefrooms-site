# Independent Repair Verifier — CT-01 / IN-01

This extends the existing Self-Healing Runtime in PR #1132, not a second
repair writer or a replacement for the dispatcher in PR #1133.

The main-owned workflow runs hourly at minute 16 UTC or via manual dispatch.
Pull requests only run verifier unit tests. The scheduled job has read-only
contents and pull-request permissions and selects one open scoped repair PR
per cycle. It fetches the exact candidate from the public repository without
a write credential, checks its single parent and allowlisted changed files,
exports base and candidate independently, repeats the assertion failure on
the parent with the new regression and the full scoped suite on the candidate
in the existing no-network, read-only Docker sandbox. It checks that the
PR head and current base did not move while testing. Reports are retained as
workflow artifacts for 30 days. Failures, stale snapshots and errors are
visible as failed runs. No repair writes or automated merge are performed.

VERIFIED_CANDIDATE means only that the independent technical reproduction
and scoped tests passed for the observed SHA and base snapshot. It is not
proof of semantic correctness, required CI, independent human/agent approval
or branch protection compliance. A deliberately misleading regression can
still pass red/green; independent exact-SHA audit remains mandatory.

Activation requires #1132 and this extension merged through normal review,
working Docker/GitHub runner access, a successful live dry-run and a real
repair PR verification. Existing writer needs BR_SELF_HEAL_TOKEN and the
approved Gemini runtime; the verifier receives neither. GitHub schedule is
best effort, not an uninterrupted 24/7 availability SLA. A separate private
runner and durable orchestrator (#1133) are not activated by this change.

Stop the verifier by disabling its workflow. Stop the writer separately by
disabling brs-self-healing.yml or revoking its scoped credential. No migration
or trading/publication state changes. Tests use synthetic API fixtures; they
do not replace real sandbox verification.
