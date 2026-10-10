# P2.1 — Shadow Watchdog and prospective settlement reliability

**Scope:** canonical Belief Core forecast → preregistered P2 challenger → immutable Shadow freeze → real market Verification → independent OOS gate. No execution, Belief writeback, retroactive freeze, synthetic settlement or Evidence mutation.

## Clock and ownership

The Belief collector owns new source forecasts, within US equity-session collection windows (America/New_York, regular scheduled slots **10:00 / 13:00 / 16:00**, local NY time). This is **not** a 24/7 BTC forecast scheduler even though BTC itself trades continuously. Outside the configured source schedule, the watchdog does not claim missing forecasts are a fault.

The **collector-side bridge** runs `hypothesis_challenger_engine.py --advance-only` directly after Belief forecast/Verification generation and before uploading the same cumulative artifact. This immediately freezes suitable future targets, settles newly arrived genuine market outcomes, and NEVER discovers/mutates challenger methodology in the source collector.

The hourly calibration workflow runs the full P2 engine, evaluates a persistent watchdog and, if an *open* forecast was eligible but uncommitted or a genuine Verification awaits P2 settlement, performs **one idempotent bridge recovery** and reassesses. Its terminal alarm step runs **after** persisting reports in the artifact.

A **separate scheduled watchdog workflow** runs at minute 07 and 37 each hour, with additional minute 02/12/17 checks at 20:00 and 21:00 UTC to cover the short closing collection phase in EDT and EST. It also checks after completed Belief/calibration events, including failed runs, and can be triggered manually. It restores the latest cumulative artifact without modifying it, evaluates health independently even if calibration itself stopped, and raises a failing GitHub Actions alarm and one deduplicated GitHub Issue. On verified recovery it closes the issue.

The existing source collector also has closing-phase attempts at minute 02/07/12/17 (and its usual 37) in both UTC hours. All attempts retain its NYSE calendar, source freshness and serialized writer gates. GitHub Actions scheduling is best effort; these extra attempts do not guarantee a hard SLA. Such a guarantee requires an independently operated timer invoking the same collector.

## Health contract

- `PASS`: no established fault, **not** a claim that the P2 challenger has passed the OOS gate. Look at `readiness`.
- `WARN`: noncritical calibration age outside active session. An expected NYSE collection slot without a confirmed market snapshot is **not** merely a warning once its source SLA expires.
- `FAIL`: a scheduled NYSE collection slot without a verified in-phase US snapshot **55 minutes after the slot**, even if collector heartbeat and FX-only observations are fresh; or unrefreshed collector during market, missing candidate baseline after a confirmed slot, missing freeze/settlement, stale in-session P2 report or integrity failure. Alerts stay active through market close, weekends and holidays until a valid original-session receipt is present.

No holiday is inferred from mere weekday. The official NYSE 2026–2028 calendar defines full-day holidays and early closes. Source-slot confirmation is taken from `scheduler.json:completed_slots`, but only a timezone-valid receipt from the genuine slot collection phase counts. Slot due time is planned NY time +55 minutes, independent of the collector heartbeat. For the final regular-session 16:00 slot the SLA matures at 16:55, **after** the 16:20 collection window; this is a legitimate failure to report, not an off-hours false positive.

The independent read-only watchdog uploads a **sanitized SLA checkpoint artifact** (`p2-shadow-source-sla-checkpoint`, 30-day retention) containing only unresolved source-slot keys and the checkpoint timestamp; it restores this on the next monitoring run. Thus missed current-session snapshots cannot silently clear at midnight or during weekend/holiday closures. This artifact is audit telemetry only: it never backdates a snapshot, mutates the canonical source or authorizes production trading. After-close/historical breaches remain `FAIL` for operator investigation. A lost/corrupt checkpoint causes watchdog failure rather than a false green outcome.

Recovery is independent of the +55 minute critical alarm: `source_sla.recoverable_missing_slot_keys` identifies unrecorded current-session slots from planned time +2 minutes until two minutes before the original collection phase ends. The regular closing slot can therefore request collection at 16:02/07/12/17 NY, while collection is still possible before 16:20. This field is dispatch telemetry only; it does not confirm a snapshot or change health/E2E proof. An existing invalid receipt remains an integrity problem for operator investigation and is never overwritten by recovery. Dispatch rechecks the actual clock, original phase and report freshness (five minutes).

The real Verification probe uses source-linked market settlement: `outcome_source="Yahoo Finance chart"` and a `yahoo:…:target=<target_at>` reference, with genuine forecast/target/verification ordering. An independent counter of real canonical Belief verifications is **explicitly not** a P2 OOS result.

`WAITING_FIRST_SHADOW_FREEZE` → `WAITING_REAL_SETTLEMENT` → `REAL_SETTLEMENT_VERIFIED` are separate readiness states.

## Irreversible failures and recovery

- `SHADOW_FREEZE_GAP_OPEN` and `SETTLEMENT_BACKLOG`: retry the collector bridge immediately if the underlying target is **still future** or a legitimate market Verification exists. A successful idempotent retry clears the alarm; no duplicate `event_id`.
- A live unrecorded slot, `COLLECTOR_STALE_DURING_MARKET`, `BASELINE_FORECAST_MISSING_AFTER_SLOT` or `SOURCE_FORECASTS_MISSING_AFTER_CONFIRMED_SLOT`: the independent monitor can dispatch only the existing `belief-core-shadow-live.yml` on `main`, during the current original collection phase. A queued/running collector suppresses duplicates; a completed attempt within the same phase imposes a five-minute retry cooldown. A completed heartbeat, failed or skipped run from a prior phase cannot block a new slot. An unavailable or malformed run inventory fails closed without dispatch. Historical `MARKET_SNAPSHOT_SLA_BREACHED` entries remain `FAIL` while a legitimate new slot can still be collected. Recovery cannot manipulate source prices, fabricate forecasts or overwrite a completed slot.
- `SHADOW_FREEZE_MISSED_IRRECOVERABLE`: **never** backdate the Shadow freeze or add a retrospective result into OOS; flag it for operator review and continue with legitimate future targets.
- Tampering, contradictory outcome or source mismatch: FAIL/HOLD, never promotion.
- Missing / stale P2 source also blocks Evolution Controller eligibility through its separate fail-closed gate.

## Artifacts and alerts

Private immutable-lineage state:
- `$STATE_DIR/state.json`, `scheduler.json`
- `$STATE_DIR/HYPOTHESIS_CHALLENGERS_STATE.json`
- `$STATE_DIR/P2_SHADOW_WATCHDOG.json`: health, source/candidate counters, verified settlement proof, alert codes and persistent append-only SHA-256 checkpoints.

Sanitized public:
- `data/investments/decision_lab_public.json:p2_shadow_watchdog` — counts, health, alert codes and market-window status; no source price, Evidence payload, private frozen probability or private verification record.
- BriefRooms LAB PL/EN `Wyniki i kalibracja / Results & calibration` → Evolution Controller shows P2 watchdog status with clear distinction between baseline Verification and P2 OOS.

GitHub workflow `.github/workflows/p2-shadow-watchdog.yml`: read-only independent monitor, half-hour schedule, one deduplicated Issue titled **P2.1 Shadow Watchdog: collection or settlement stall**. A critical alarm fails the monitor workflow. Issue API failure cannot mask a failing Actions alarm.

The monitor's GitHub Actions step summary explicitly shows the first `btc.volatility.benign` Shadow commitment after activation, including `forecast_id`, deterministic `shadow_forecast_id`, `event_id`, source hash and freeze/target chronology. It remains **PENDING** before the first real freeze; it never synthesizes proof from collector heartbeats. Only when the independent E2E gate is `PASS`, it additionally shows the real Yahoo-linked source Verification identifier and baseline/challenger Brier scores. These are audit metadata (no private probabilities, market prices, or authorization).

## Acceptance conditions

1. No false missing-source alarm before US session, during weekends or before planned slot+55min.
2. Missing candidate baseline in a completed actual source slot => FAIL.
3. Source exists, target future, no Shadow => FAIL and immediate safe bridge recovery.
4. Source exists, target expired without prior Shadow => permanent FAIL; no retrospective scoring.
5. Shadow freeze and Verification must link to the original immutable forecast and market outcome source; manual/fake verification cannot settle P2.
6. Two successive runs must leave frozen and settled entries append-only; corruption or deletion => FAIL.
7. Real canonical Belief Verification count is distinct from real *post-activation* P2 OOS settlement count.
8. An independent monitor must run even when the calibration workflow never triggers.
9. No P2 promotion, trading execution, Belief writeback or historical mutation.
10. Closing-slot recovery is possible before 16:20 NY without waiting for the 16:55 alarm; a delayed dispatch after the phase ends cannot run.
11. Prior-phase heartbeat/failure/skipped runs cannot veto a new slot; queued/running collection and the same-phase five-minute cooldown prevent duplicates. Calendar, EDT/EST, early-close and invalid-receipt cases remain fail-closed.
12. Execute the workflow recovery shell with a controlled GitHub CLI: dispatch only the existing collector for a live gap; never dispatch on inventory failure, an in-flight collector or an after-close breach; retain historical checkpoints unchanged.

Production acceptance **for an actual P2 OOS settlement** requires a genuine future target from an after-activation Shadow commitment and a real linked Verification. `REAL_SETTLEMENT_VERIFIED` can coexist with `Production E2E: BLOCKED` when a source SLA breach or another critical health fault remains unresolved. A synthetic fixture PASS can never replace production proof or satisfy the candidate's separate sample-size gate.

