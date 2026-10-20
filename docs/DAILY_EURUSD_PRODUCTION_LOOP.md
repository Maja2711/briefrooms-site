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

The re-entry safety check and the canonical position writer use the same `NATIVE_BELIEF_FIRST` thesis-family identity. A recent meaningful native loss, including `DYNAMIC_RISK_EXIT` or `STOP_LOSS`, must therefore be compared with the new native candidate before an older legacy loss can qualify it. An unchanged score/confidence cannot reuse improvement measured against an older trade to release the guard. Existing material-change thresholds and the 12-hour meaningful-loss window remain in force. They cannot bypass the additional post-loss market-location contract in [DAILY_EURUSD_POST_LOSS_REENTRY.md](DAILY_EURUSD_POST_LOSS_REENTRY.md): 30% daily correction, held D1 support/resistance and a strong post-loss Belief-first signal are jointly mandatory after every same-direction loss in the active D1 candle or its first-hour rollover window. Missing D1/1m data blocks new admission, while exits retain their current authority. The technical proof and source hash are frozen in execution admission; the new implementation is part of the runtime code cohort. These checks do not change final Belief direction or historical thesis identities.

## Immutable observations and measurement

`data/investments/eurusd_production_decisions.json` is an append-only record of new monitor decisions. Each manifest includes native provenance and decision time, policy version/hash, runtime source hash, market/Epistemic hashes, final decision, admission result, EPE verification and initial entry/stop/target. A position and its eventual closed trade retain the identical entry manifest. A new refresh decision is a separate record and never relabels the open position's policy.

Source Evidence timestamps survive the Epistemic projection. A delayed refresh cannot make pre-release data appear post-release. Inside the existing post-release window, admission requires original source lineage tied to the exact official event UID/time, the macro fast adapter's POST_RELEASE phase and observed actuals. Older projections without that proof fail closed during that window.

New EPE admissions carry `DAILY_EURUSD_BID_ASK_V1`. Entry uses the appropriate buffered ASK/BID; hard, dynamic, event and time exits use the opposite side under the frozen synthetic cost model. This is a conservative paper model, not broker fills, commissions or a slippage guarantee. Existing positions without this marker retain their entry-time accounting; past outcomes are not recalculated.

`production_performance` reports all canonical manifest-bound closed trades by the policy actually consumed. Net R is independently recomputed from actual exit, frozen entry and initial risk; declared `r_multiple` cannot authorize promotion. Legacy trades remain in history and are counted separately as lacking prospective manifests. Descriptive comparisons between different versions/market periods are not causal A/B results.

## Predeclared candidate and promotion gate

The component's only parameter is minimum directional score distance from 50. Baseline `3` preserves the existing narrowest native participation band; candidates advance one bounded step through `4`, `5`, `6`. No candidate loosens admission below baseline. The initial deployment starts at baseline and collects new evidence; no past performance is imported to justify a promotion.

Each candidate freezes its policy, evaluator hash, runtime code cohort, activation boundary and test specification before outcomes exist:

- First 40 eligible, prospectively selected canonical actual admissions, at least 27h apart; minimum 15 independent days. Selection stops at the fixed sample. Partial results are progress only, with no early statistical testing.
- One evaluation position at a time. Actual outcomes bind only to the matching canonical trade and exact manifest; a virtual result can never replace an actual result.
- Compare admitting versus abstaining on the same frozen opportunity. Require at least +0.05 stressed mean net R and a strictly positive deterministic day-block bootstrap lower confidence bound. Apply a predeclared 0.02R model-uncertainty penalty when policies disagree.
- Confidence adjusts for at most three attempts at each of three transitions. A completed failed/rolled-back trial has a 30-day cooldown; no outcome-selected extension of the first 40 cases.
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
