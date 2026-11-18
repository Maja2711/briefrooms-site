# Daily EUR/USD production measurement and learning loop

Owner: `TR-03`; local learning component `LE-08`; existing `LE-11` contracts and statistical helpers. Runtime: `eurusd-daily-spot-v1.9.1`. Scope: production Daily EUR/USD paper engine only.

## Authority and implementation

The existing Belief Core -> EpistemicState -> CF-07 -> native final LONG/SHORT/FLAT path remains the sole direction authority. Calendar/re-entry safety and two-source EPE verification precede policy admission. The learner can abstain from an otherwise valid admission; it cannot reverse a signal, generate a fill, alter position sizing/SL/TP, write Belief state or change another instrument. A/B/C, FSE, EURUSD X and the legacy contextual learner have no authority in this loop.

| Owner | File | Contract |
|---|---|---|
| Main Daily monitor | `scripts/daily_eurusd_spot_v19.py` | Single production decision, policy and ledger writer |
| Decision persistence | `scripts/daily_eurusd_decision_record.py` | Frozen native decision, policy/code/data identity and geometry |
| Local policy component | `scripts/daily_eurusd_production_policy.py` | Candidate -> fixed gate -> exact version -> monitoring -> rollback |
| Shared publication guard | `scripts/validate_daily_eurusd_persistence.py` | Same authority/history checks before initial write, conflict recompute and rebase |
| Realtime lifecycle | `scripts/daily_eurusd_fast_lifecycle.py` | Existing-position exits only; no policy/ledger mutation |

The cycle watchdog consumes `daily_eurusd_decision_record.ENGINE_VERSION`, the same version contract as the canonical writer, without importing the trading runtime. With healthy upstream, a fresh current-version OPEN or NO_TRADE state must not raise a Daily SLA alarm or dispatch recovery. Obsolete, unknown or missing versions and real age breaches still fail closed. Watchdog PR validation checks the proposed revision; its production job remains pinned to `main`.

The main monitor atomically publishes output, history, policy state, decision ledger and notification transitions in one guarded Git commit. The realtime writer appends actual closes; the next full monitor binds them to the original admission. Watcher startup precedes Web Push, so a notification failure cannot prevent risk monitoring.

The re-entry safety check and the canonical position writer use the same `NATIVE_BELIEF_FIRST` thesis-family identity. A recent meaningful native loss, including `DYNAMIC_RISK_EXIT` or `STOP_LOSS`, must therefore be compared with the new native candidate before an older legacy loss can qualify it. An unchanged score/confidence cannot reuse improvement measured against an older trade to release the guard. Existing material-change thresholds and the 12-hour meaningful-loss window remain in force. They cannot bypass the additional post-loss market-location contract in [DAILY_EURUSD_POST_LOSS_REENTRY.md](DAILY_EURUSD_POST_LOSS_REENTRY.md): the initial 30% daily correction, held D1 support/resistance and strong post-loss Belief-first signal apply until the versioned local re-entry learner promotes a measured alternative; criteria can then tighten or loosen after every same-direction loss in the active D1 candle or its first-hour rollover window. Missing D1/1m data blocks new admission, while exits retain their current authority. The technical proof and source hash are frozen in execution admission; the new implementation is part of the runtime code cohort. These checks do not change final Belief direction or historical thesis identities.

## Strong-trend H1-primary entry location (2026-11-18)

Daily EUR/USD uses a **completed H1** Bollinger(30, 2.5) trend as the primary
technical admission. A directional signal still originates **only** from native
Belief/CF-07. In the previous contract, a D1 outer-band touch could veto the
same-direction position indefinitely until a 50% correction toward the moving
D1 middle band. This prevented a verified H1 continuation from being assessed
even while Belief, H1 and D1 all agreed.

`daily_eurusd_regime_policy.strong_h1_entry_priority()` now replaces this
specific D1 cooldown **only** for a verified H1 `STRONG_TREND` and matching
native direction, MTF strategy, completed H1 source provenance and coherent
completed D1 trend. Both H1 and MTF must agree on the same completed H1
timestamp (at most 90 minutes old), and the current D1 touch snapshot must
match the observed completed D1 (at most four days old). Data gaps, H1
exhaustion, transition/range, unaligned D1, or an unverified touch state
**never** release the D1 cooldown.

Even in a strong trend, the entry must not chase an outer H1 band extension.
For a SHORT, the H1 close must have returned to a **%B of at least 0.12**;
for a LONG, it must be **at most 0.88**. The symmetric location condition is
an explicitly predeclared, *not yet empirically calibrated* conservative
threshold. When H1 is still stretched, admission is blocked with
`h1_strong_trend_waiting_for_hourly_pullback` instead of demanding the
larger D1 50% pullback. `execution_admission.strong_h1_entry_priority`
records the decision, thresholds, timestamps and safe role boundaries.

The D1 touch is still updated as before, saved without alteration, and
becomes effective again if strong H1 proof expires. Calendar, same-thesis
re-entry, post-loss proof, policy, verified EPE, initial SL/TP and independent
position-exit risk remain unchanged. Research-only band reversal diagnostics
do not obtain directional or admission authority. This is prospective and
code-cohort-bound: prior trade history and prior decision manifests are
untouched, and push validation does not create positions.

## Multifactor admission and prospective entry-factor evidence (2026-11-18)

A Bollinger band is a **price-location tool**, not a sufficient trading thesis.
For a fresh verified H1 STRONG_TREND, native direction still originates only
from Belief Core -> EpistemicState -> CF-07 -> Daily native decision. Before
entry, `execution_admission.entry_multifactor` now records four *distinct
evidence domains*:

- **BELIEF_QUALITY**: score distance, confidence, coverage and number of
  qualified source-backed beliefs, evaluated as one grouped vote.
- **H1_RAW_PRICE_ACTION**: signed change across the last three *completed*
  hourly closes and count of aligned hourly close intervals. This raw price
  signal is different from the Bollinger moving-average slope.
- **HIGHER_TIMEFRAME**: raw signed change over five *completed D1 close intervals* and non-opposing verified W1 SMA trend; the D1 Bollinger trend is explicitly not a factor vote.
- **PROSPECTIVE_REWARD_RISK**: initial risk and target geometry, before EPE.

At least three of the four domains must confirm when all are verifiably
available for a STRONG_TREND. **BOLLINGER_H1_LOCATION** and independent
opposite-direction reversal research are recorded as supporting/warning
factors, but Bollinger cannot supply a missing vote. All thresholds are
fixed, uncalibrated starting safety choices; they are **not** empirically
optimized confidence or accuracy probabilities. Gaps in newly introduced
factor evidence cannot create an approval; pre-existing admission safety
remains in force. The native direction, calendar, re-entry, EPE, existing
open-position risk and hard SL/TP are unchanged.

The existing TR-03 single writer stores an optional
`entry_factor_study` field in its **existing** governed policy state, rather
than introducing another engine or writer. Once per three-hour
non-overlapping sample window it can freeze a genuine native directional
observation using a completed M1 market mid proxy available **before** the
decision. Exactly three hours later it labels a directional mid-price
change using the first completed M1 close within a predeclared five-minute
window. Missing endpoint evidence is labelled `DATA_GAP`, not a loss or
zero. There is no execution assumption, simulated fill, spread-adjusted
trade, broker P&L, or retrospective original decision.

`production_policy_loop.entry_factor_study` reports sampled opportunities,
future price-direction proxy outcomes, each factor's positive/negative
cohorts, and a strictly separated count/mean net R from **canonical actual
manifest-bound closures**. Comparisons are exploratory **associations**, not
estimates of a factor's causal profitability; W1/D1/H1 factor correlations
are possible. At least 40 prospective resolved observations, 15 distinct
days, and 10 observations per factor side are needed even to display a
descriptive directional-price contrast. No factor is declared proven best,
no weights adapt automatically and no statistical promotion is authorized
by this exploratory report. Any production adaptation requires a separate
predeclared, out-of-sample, cost- and multiple-testing-aware governed gate.

Existing OPEN trades retain their frozen historical entry geometry and
risk-exit contracts. All frozen cases, future outcomes and write authority
remain inside the existing provenance and NO RETROACTIVE boundaries.

## Immutable observations and measurement

`data/investments/eurusd_production_decisions.json` is an append-only record of new monitor decisions. Each manifest includes native provenance and decision time, policy version/hash, runtime source hash, market/Epistemic hashes, final decision, admission result, EPE verification and initial entry/stop/target. A position and its eventual closed trade retain the identical entry manifest. A new refresh decision is a separate record and never relabels the open position's policy.

Source Evidence timestamps survive the Epistemic projection. A delayed refresh cannot make pre-release data appear post-release. Inside the existing post-release window, admission requires original source lineage tied to the exact official event UID/time, the macro fast adapter's POST_RELEASE phase and observed actuals. Older projections without that proof fail closed during that window.

New EPE admissions carry `DAILY_EURUSD_BID_ASK_V1`. Entry uses the appropriate buffered ASK/BID; hard, dynamic, event and time exits use the opposite side under the frozen synthetic cost model. This is a conservative paper model, not broker fills, commissions or a slippage guarantee. Existing positions without this marker retain their entry-time accounting; past outcomes are not recalculated.

`production_performance` reports all canonical manifest-bound closed trades by the policy actually consumed. Net R is independently recomputed from actual exit, frozen entry and initial risk; declared `r_multiple` cannot authorize promotion. Legacy trades remain in history and are counted separately as lacking prospective manifests. Descriptive comparisons between different versions/market periods are not causal A/B results.

## Predeclared candidate and promotion gate

The margin component's only parameter is minimum directional score distance from 50. The separate adaptive re-entry component in the same state can also learn post-loss criteria, as specified in `DAILY_EURUSD_ADAPTIVE_REENTRY.md`. Baseline `3` preserves the existing narrowest native participation band; candidates test all stricter remaining floors from `4`, `5`, `6` in parallel. No candidate loosens admission below baseline. The initial deployment starts at baseline and collects new evidence; no past performance is imported to justify a promotion.

Each candidate freezes its policy, evaluator hash, runtime code cohort, activation boundary and test specification before outcomes exist:

- First 40 eligible, prospectively selected canonical actual admissions, at least 27h apart; minimum 15 independent days. Selection stops at the fixed sample. Partial results are progress only, with no early statistical testing.
- One evaluation position at a time. Actual outcomes bind only to the matching canonical trade and exact manifest; a virtual result can never replace an actual result.
- Compare admitting versus abstaining on the same frozen opportunity. Require at least +0.05 stressed mean net R and a strictly positive deterministic day-block bootstrap lower confidence bound. Apply a predeclared 0.02R model-uncertainty penalty when policies disagree.
- Confidence adjusts for at most three attempts per target floor across all parent versions in an exact code/evaluator cohort (nine tests total). A completed failed/rolled-back trial has a 30-day cooldown; no outcome-selected extension of the first 40 cases.
- Require positive improvement in both temporal halves, no drawdown increase, and no protected-slice degradation beyond 0.05R. Every observed informative direction/session/macro slice needs at least five observations; insufficient evidence blocks promotion.

The exact evaluated policy hash materializes as a `ProductionVersion` on a future admission boundary. Frozen `EvolutionCandidate`, `PromotionGate`, `ProductionVersion` and `RollbackEvent` contracts are reused from `briefrooms_evolution_contracts.py`; shared bootstrap/drawdown calculations come from `statistical_promotion_gate.py`. This does not give the generic Evolution Controller execution authority over Daily.

## Monitoring and rollback

After promotion, consecutive fixed windows of 20 purged observations compare the active policy with its approved parent, with at least 10 independent days for aggregate degradation/drawdown tests. Admitted opportunities use canonical actual outcomes. Skipped opportunities are frozen before their future path and explicitly marked `PROSPECTIVE_COUNTERFACTUAL`, with no execution authority. Their callback uses the same Daily lifecycle family on a complete forward 1m path; missing data is never a zero result.

Rollback restores the approved parent for future admissions when economic/CI, protected-slice or drawdown monitoring fails. Three distinct incomplete-coverage observations spanning at least two hours since last coverage trigger a data-quality rollback. A current incomplete case also holds an otherwise passing promotion. A runtime code change rolls back every incompatible promoted ancestor before admission, parks incomplete candidate cohorts and censors unresolved skipped cases from the old implementation without inventing an outcome. Canonical actual cases are never censored and can still bind to their real closes.

The validator independently recomputes gates and monitoring reviews, preserves selected case IDs/reviews append-only, and replays the promotion/rollback state machine. An old passing gate cannot reactivate a rolled-back policy. It also reconciles canonical actual proof, ledger rows, registered policy votes and exact manifest identity. A source change during Git rebase blocks publication of the old computation.

## Migration, failure handling and limits

The first scheduled v1.9.1 monitor creates `eurusd_production_policy_state.json` and the decision ledger only when no prior learning epoch exists. Losing either file, or losing both after a recorded admission, cannot silently restart learning. Invalid existing history raises rather than becoming empty history. Open legacy positions and all closed history stay unchanged.

Invalid learning state stops new admissions/promotions. An already persisted OPEN position may still be managed or closed in `DEGRADED_EXIT_ONLY`, provided policy/ledger bytes remain unchanged, entry facts/manifest remain frozen, no unrelated trade is added, and native provenance, history and notifications pass the shared publication guard. Repair of damaged governance is explicit; risk handling never resets it.

This is a paired **admission-component** test, not a complete alternative portfolio backtest. Skipping an entry can permit later actual entries that overlap the virtual reference; the single evaluation case does not sample those overlaps. Counterfactual monitoring follows the full monitor cadence and available 1m paths, while actual exits may use the faster watcher. Management context is applied only after it existed, which can add reference latency. No exact counterfactual fast-execution or portfolio-PnL equivalence is claimed.

Code pushes run validation only. Production mutation remains confined to scheduled or explicitly governed/manual runtime. No historic fills, policy labels or results are reconstructed. A code rollback must retain the cost/manifest reader compatibility needed by any existing new-contract OPEN trade; an older lifecycle cannot reinterpret that trade. Preserve canonical history, original manifests and policy/rollback records. The automatic rollback described above changes future policy admission and does not require rewriting or replacing the lifecycle.

## Recursive improvement and reflection

The existing local component runs in `AGGRESSIVE_BOUNDED` mode. Native actual closes feed append-only reflection, lessons and research hypotheses in the same policy state. Each admission freezes a review of similar known losses. Up to three stricter admission challengers share prospectively frozen observations; all retain the fixed gates above. Passing candidates are ranked by lower CI, stressed mean improvement and then the least restrictive floor. Failed gates and rollbacks feed the next iteration within the shared attempt budget. Historical discovery never supplies OOS observations. Post-loss correction/support/strength/source/wait criteria are starting parameters and may change through the separate prospective re-entry component; source truth, native direction, EPE and SL/TP risk retain their authority. See `DAILY_EURUSD_RECURSIVE_IMPROVEMENT.md` for authority, migration and validation.

## Admission activity liveness and H1 verification (2026-11-11)

A directional candidate cannot enter when completed H1 Bollinger proof or the
H1-primary multi-timeframe policy is missing, stale or unverified. This is a
new-entry-only fail-closed condition. Open positions retain their independent
hard/soft/fast exit lifecycle.

Each full canonical Daily monitor now persists an additive audit-only
`metadata.decision_activity`: directional veto reasons, uninterrupted same-gate
duration, and consecutive cycle count. The watchdog reads these facts, warns on
a directional veto lasting six hours, and treats a prior-day ECB/Fed veto as a
decision-liveness recovery incident rather than a false 15-minute publication
SLA breach. It does not cancel real same-day vetoes or independently open trades.

