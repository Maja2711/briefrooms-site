# P2.1 — Shadow Watchdog and prospective settlement reliability

**Scope:** canonical Belief Core forecast → preregistered P2 challenger → immutable Shadow freeze → real market Verification → independent OOS gate. No execution, Belief writeback, retroactive freeze, synthetic settlement or Evidence mutation.

## Clock and ownership

The Belief collector owns new source forecasts, within US equity-session collection windows (America/New_York, regular scheduled slots **10:00 / 13:00 / 16:00**, local NY time). This is **not** a 24/7 BTC forecast scheduler even though BTC itself trades continuously. Outside the configured source schedule, the watchdog does not claim missing forecasts are a fault.

The **collector-side bridge** runs `hypothesis_challenger_engine.py --advance-only` directly after Belief forecast/Verification generation and before uploading the same cumulative artifact. This immediately freezes suitable future targets, settles newly arrived genuine market outcomes, and NEVER discovers/mutates challenger methodology in the source collector.

The hourly calibration workflow runs the full P2 engine, evaluates a persistent watchdog and, if an *open* forecast was eligible but uncommitted or a genuine Verification awaits P2 settlement, performs **one idempotent bridge recovery** and reassesses. Its terminal alarm step runs **after** persisting reports in the artifact.

A **separate scheduled watchdog workflow** runs at minute 07 and 37 each hour; it also checks after successful Belief/calibration events and can be triggered manually. It restores the latest cumulative artifact without modifying it, evaluates health independently even if calibration itself stopped, and raises a failing GitHub Actions alarm and one deduplicated GitHub Issue. On verified recovery it closes the issue.

## Health contract

- `PASS`: no established fault, **not** a claim that the P2 challenger has passed the OOS gate. Look at `readiness`.
- `WARN`: no confirmed successful market snapshot despite an expected US weekday slot, or prolonged P2 age outside session. No invented holiday calendar.
- `FAIL`: unrefreshed collector during market, missing candidate baseline after a confirmed successful market slot, eligible Shadow forecast not frozen, expired baseline missed irrecoverably, real verified settlement backlog, stale P2 report, source/transform fingerprint conflict, tampering of previously committed Shadow/settlement, or mature Verification overdue.

No holiday is inferred from mere weekday. Source-slot confirmation is taken from `scheduler.json:completed_slots` (with 55-minute slot completion grace), which limits false `missing forecast` claims to windows the collector actually processed.

The real Verification probe uses source-linked market settlement: `outcome_source="Yahoo Finance chart"` and a `yahoo:…:target=<target_at>` reference, with genuine forecast/target/verification ordering. An independent counter of real canonical Belief verifications is **explicitly not** a P2 OOS result.

`WAITING_FIRST_SHADOW_FREEZE` → `WAITING_REAL_SETTLEMENT` → `REAL_SETTLEMENT_VERIFIED` are separate readiness states.

## Irreversible failures and recovery

- `SHADOW_FREEZE_GAP_OPEN` and `SETTLEMENT_BACKLOG`: retry the collector bridge immediately if the underlying target is **still future** or a legitimate market Verification exists. A successful idempotent retry clears the alarm; no duplicate `event_id`.
- `COLLECTOR_STALE_DURING_MARKET`, `BASELINE_FORECAST_MISSING_AFTER_SLOT` and `SOURCE_FORECASTS_MISSING_AFTER_CONFIRMED_SLOT`: the independent monitor can dispatch `belief-core-shadow-live.yml`; suppress duplicate dispatches if any collector run occurred within 90 minutes. This is a best-effort recovery attempt: a slot already recorded completed may require operator investigation and must never be backdated or fabricated. This recovery must not manipulate source prices or falsely create forecasts.
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

Production acceptance **for an actual P2 OOS settlement** remains pending until a genuine future target from an after-activation Shadow commitment receives a real linked Verification. A synthetic fixture PASS can never replace that production proof.
