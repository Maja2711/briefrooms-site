# Stock Trading v2 learning persistence and progress

Modules: `TR-04`, `LE-01`, `LE-02`, `LE-03`, `LE-05`, `LE-06`, `LE-08`, `LE-09`.

The research branch owns evidence only. These changes do not write production positions or replace a Champion. Persistence and health monitoring read local evidence; the existing outcome collector fetches market history only for unresolved source/horizon pairs.

## Actual HOLD/EXIT decisions

`stock_trading_v2_position_experience.py` retains the existing immutable decision schema and file paths. Its equality contract excludes exactly `recorded_at`, `event_sha256` and the collection-level `portfolio_updated_at`. It compares every decision field, reason and complete frozen position state. A different SL, target, mark, thesis, risk review or decision still raises `ImmutablePositionExperienceConflict`.

An existing decision is read and hash-validated, then left byte-identical. Later reads are written to sibling `data/investments/stock_trading_v2_position_observations/`. An observation binds the original event ID/hash to the observed portfolio update timestamp. Its deterministic ID excludes collection wall-clock time, so repeating the same read creates no duplicate. Original read records are never overwritten. `--verify` checks both ledgers and the source ID/hash binding. A non-default `--root` gets an equally isolated sibling observation directory; `--observation-root` can select one explicitly.

This deliberately preserves the initial metadata inside historical decision files. It does not rehash, migrate or normalize historical decisions.

## Prospective Champion admission

Legacy v1 admission rows remain readable and byte-identical; they cannot qualify as a new formal baseline. `is_formal_observation()` validates an observation and returns true only for the new v2 prospective contract. `iter_observations(root, formal_only=True)` exposes those baselines to new formal consumers.

Before discovery, run `stock_trading_v2_admission_ledger.py --activate-epoch --policy-snapshot <production-policy.json>`. This creates only the immutable `stock_trading_v2_admission_epochs/prospective-v2.json` with the actual activation time and never reads or freezes source events. Retrying preserves its original bytes and activation. A formal event must have decision time and source recording time after that activation. Its admission must be captured no later than 900 seconds after the decision, with `activation <= decision <= source recording <= admission recording`. No retroactive activation argument is exposed by the CLI. If activation was omitted, normal admission freezing creates the boundary at its current time and quarantines earlier source events rather than backdating it.

Each v2 event must freeze `candidate_state.decision_context` with the complete policy, opportunity configuration, portfolio and opportunity snapshots and their SHA-256 hashes. Market and source decision timestamps must agree. A formal admission freezes the complete source event and original policy snapshot, verifies that policy's `minimum_cash_edge_points`, the candidate's portfolio utility, CASH utility 50, availability of a slot, eligibility and absence of an existing holding. Rotation is explicitly excluded from the CASH-margin experiment.

Admission identity depends on the source event, not the current policy. Re-running after a policy change preserves the existing baseline. A later policy cannot create another Champion admission for the same event. Source policy hashes, source event hashes and the deterministic Champion decision are independently revalidated from the frozen proof.

An old event without a baseline, a missing decision context or an admission recorded too late produces only a `RETROSPECTIVE_DIAGNOSTIC` in sibling `stock_trading_v2_admission_retrospective/`. It declares `prospective_before_outcome=false` and `formal_holdout_eligible=false`. These files never enter the formal admission directory. They are diagnostic exclusions, not reconstructed historical trades.

CLI: `--policy` and its alias `--policy-snapshot` select the available production policy snapshot. Formal v2 evaluation always binds to the source event's own policy snapshot; today's policy is never substituted. Epoch and retrospective directories must be staged alongside research evidence by the research workflow.

## Terminal outcome reuse

`stock_trading_v2_outcome_replay.py` validates all existing terminal evidence and its source event ID/hash, decision, market, symbol and session before fetching history. A settled `(source_event_id, horizon_sessions)` is authoritative in that ledger root. It is skipped before any provider call or replay, even if the provider revises OHLC, the Champion snapshot changes or a later config uses another `replay.version`. Only unresolved pairs may mature and be appended. Duplicate terminal answers for one source/horizon, corrupt result hashes and changed source bindings fail closed before fetching.

The original outcome schema, ID algorithm and settled bytes remain intact. A deliberately different methodology requires a separate output root and cannot create a second answer inside the historical ledger. This counterfactual horizon ledger remains distinct from the production-native lifecycle and NAV replay used by formal CASH-edge proof.

## Independent learning progress

`stock_trading_v2_learning_health.py` reads experience, outcomes, regret and Challenger evaluation files and writes only its isolated health report, by default `stock_trading_v2_learning/learning_health.json`.

The three separate watermarks are:

| Stage | Progress evidence |
|---|---|
| Outcomes | Newly observed source event + horizon pairs |
| Regret | Newly compared source groups + horizons |
| Evaluations | Newly evaluated source samples per Challenger |

Regenerating a report, rewriting `settled_at`, changing a PASS flag or removing old samples does not count as progress. Each stage preserves `last_progress_observed_at` unless new evidence identities appear. The first monitoring run is explicitly `BASELINE_OBSERVED`, with no claimed new progress and no invented last-progress time. Staleness is then measured from that baseline until new evidence arrives. The report retains `latest_source_decision_at` separately, so old source coverage remains visible on the first monitoring run.

Missing or mismatched source events and duplicate source/horizon outcomes are rejected before their identities can appear as progress.

Pending horizons and mature unresolved horizons are reported separately. `--session-calendar` can supply a JSON object mapping `GPW` and `US` to completed session dates. Without an authoritative calendar the monitor uses only completed session dates actually observed in outcome evidence. Its `mature_unresolved` count is therefore an explicit lower bound; the remainder is `pending_or_maturity_unknown`. It does not invent exchange holidays or count weekends as market sessions.

The CLI accepts `--report`/`--output`, `--workflow-status success|failure|cancelled|unknown`, `--experience-root`, `--outcome-root`, `--regret`, `--evaluations`, `--config` and `--stale-after-hours` (default 72). An always-run workflow step can record an earlier failure without claiming learning success. Attempts to place the health output over an input ledger or configuration are rejected.

Evaluation reports are read only from their evaluator-owned top-level JSON files. Reserved `.cohorts/` enrollment ledgers are not evaluation reports. Invalid actual reports still fail validation. When an input cannot be read or verified, the monitor persists a hashed `source_integrity=FAILED` diagnostic, returns a failing exit code and marks all stages `UNAVAILABLE`. It retains the last validated watermarks and credits no new progress. A first successful observation after an initial failure establishes a baseline only.

Continuous v2 trials expose preterminal collection coverage separately: registration wait, frozen anchors, provider close coverage, fixed endpoint and calendar identity. This is not formal evaluation progress. While a preregistered tape is still collecting, the evaluation stage reports `COLLECTING_PROSPECTIVE_TAPE` instead of treating the required 60-session wait as a stalled evaluation. Actual evaluated windows use their stable `block_id` together with the source anchor; repeated timestamps do not increase the watermark.

Before persistence the learning workflow compares every previously tracked experience, admission, epoch, retrospective record, position decision/read observation and terminal outcome against the checkout's `HEAD`. Any modified or removed historical file blocks the research commit. New append-only files remain allowed. The always-run health step records earlier failures, and an Actions artifact retains the health report plus the tracked immutable-history Git blob manifest even if branch persistence fails. Repository pushes still run validation only; runtime settlement remains scheduled or explicitly dispatched and writes only `stock-trading-v2`.

## Verification and rollback

Regression tests copy actual historical position files into temporary storage and verify every original byte across repeated metadata-only reads. The full frozen snapshot regression preserves all 1,059 experience files and 2,078 admission files through two freezes with a changed current policy. Another copies 1,499 actual terminal outcomes, supplies revised synthetic OHLC and a new replay version, settles only previously unresolved horizons, then verifies that a second fully settled run performs no history fetch and changes no bytes. Tests also reject actual decision mutations and forged observation bindings, freeze one source-policy baseline, reject stale/future/retrospective formal admissions and verify independent progress under regenerated report timestamps.

Rollback is code-only: historical ledgers do not need rewriting. Preserve new append-only read observations, epochs and diagnostic exclusions for audit. Returning to an older reader may omit the new monitoring and prospective schema; do not let an older writer reinterpret v2 admissions or promote them through a legacy evaluator.
