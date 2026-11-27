# BRs Engineering Harness — Self-Healing Runtime v1

## Authority and activation

Engineering maintenance in CT-01 / IN-01 only; extends the state machine
`scripts/brs_engineering_loop.py` from PR #1128. The News Editorial Guardian
and its tests from PR #1129 are prerequisites. This is a runtime actuator, not
another news publisher, investment engine, or general release authority.

`.github/workflows/brs-self-healing.yml` runs hourly at minute 51 UTC after it
is on main, plus manual dispatch on main. GitHub cron is best effort. PR events
perform read-only validation, never model calls or writes. No ChatGPT session
is needed for the scheduled runtime.

Required runtime configuration:
- existing `GEMINI_API_KEY` and approved Gemini adapter/models;
- `BR_SELF_HEAL_TOKEN`: dedicated, expiring repository-scoped fine-grained
  bot credential, Contents/Pull requests/Issues read-write for candidate refs,
  no Actions/admin/workflows permission. Keep main protected from this actor.
  A normal GITHUB_TOKEN-generated push/PR may suppress required CI events;
  use the dedicated credential to trigger normal PR validation.
- GitHub-hosted Docker runner; Python 3.11 image and existing adapter
  dependency requests. No new provider endpoint or production data writer.

Do not store credentials in repository files. Missing configuration fails
closed; do not claim that the runtime is operational merely because CI passes.

## Cycle

1. DETECT: run the immutable scoped regression suite (including retained
   generated tests); inspect current marker-matched PL/EN public Guardian
   findings. No action on a clean or previously tracked incident.
2. DIAGNOSE: use the existing governed Gemini JSON adapter on bounded public
   code and evidence; never send raw CI logs, private records or credentials.
3. REPAIR: allow exact, unique text replacements in four explicitly listed
   deduplication modules and one new incident-specific unittest file. Existing
   tests, workflows, risk/policy and data are not editable. No shell from model.
4. VERIFY: new test must fail by assertion on the unchanged base; candidate
   must pass the scoped suite including the new test. Run both in a read-only
   container without network, credentials, runner home or Git metadata.
5. LEARN: persist checkpoint and content fingerprint in a run artifact; keep the
   new regression test in the repair PR. At most two proposals per incident;
   exhausted incidents become deduplicated architect-review issues.
6. PUBLISH CANDIDATE: isolated writer revalidates paths, exact base, staged
   content hash and checkpoint. Create a unique branch and PR; never push
   main, force-push, or execute candidate code with write credentials.

A passed candidate is not a release. Independent exact-commit tests, audit,
architect review and existing branch protection remain mandatory. The
content fingerprint is not an authenticated release attestation. The engine
does not auto-approve, auto-merge, deploy, reset timestamps or dispatch trading.
Existing newsroom publisher/recovery owns operational publication.

## Limits / rollback

Initial adapter repairs reproducible errors in deterministic news duplicate
logic; other failures (publisher orchestration, freshness, missing source,
language quality, investment engines) are not silently treated as repairable.
Unknown semantic errors and model false positives remain possible; independent
review must establish the factual regression, not merely a green test.
Model/infrastructure errors fail visibly; no candidate is written on failure.
A closed repair PR/incident prevents blind retry of the same fingerprint.
Artifacts retained 30 days; PR/issue and committed regression provide durable
lineage. Do not commit raw model logs.

Disable workflow or revoke its dedicated credential to stop automated writes;
close or revert an affected repair PR using ordinary reviewed GitHub process.
No runtime data migration. Before enabling on main, independently verify
negative cases, actual sandbox red/green reproduction and normal CI triggering.
