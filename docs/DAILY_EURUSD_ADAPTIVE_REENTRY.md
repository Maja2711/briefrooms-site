# Daily EURUSD: learning the criteria for re-entry after a loss

Owner: `TR-03`, local learner `LE-08`, existing `LE-11` contracts.
`daily_eurusd_reentry_learning.py` extends the existing native policy component.
It is not A/B/C, EURUSD X, FSE or the retired v1.8 contextual learner.

## Initial criteria and learning authority

The initial 30% D1 correction, D1 MA/support requirement and 60/40 score with
0.50 confidence are starting criteria. They are not permanent invariants.
A prospectively approved local policy may loosen or tighten them for future
post-loss admissions. The existing same-thesis improvement thresholds can also
change within this scope. Without an approved alternative, initial criteria apply.

Learnable fields are correction fraction, directional score margin, confidence,
aligned-belief count, definition of support/resistance, D1 MA periods, support
tolerance and entry distance, confirmation interval, minimum wait, requirement
for original trend evidence observed after the loss, and the requirement/thresholds
for material thesis improvement. Supported support definitions are held completed-D1
levels and a held, confirmed intraday price rejection. Neither permits future bars
or an unfinished confirmation.

Parameter domains enforce meaningful numeric/schema values, e.g. correction 0..1,
confidence 0..1, positive MA periods within available D1 history, valid price-zone
geometry and complete 1/5/15-minute confirmation bars. These are contract domains;
a candidate cannot escape them by supplying NaN, future facts or a different engine.

Direction still comes from Belief Core -> EpistemicState -> CF-07 -> native final
LONG/SHORT/FLAT. Authentic source times, available qualified native inputs, calendar
safety, verified EPE and frozen SL/TP/position risk remain enforced. Learning can
change the post-loss criteria; it cannot invent source freshness or a new direction.

## Prospective evidence for both stricter and looser admissions

Learning only from actual admitted trades would hide opportunities rejected by the
current criteria. On a native directional candidate, the writer evaluates the active
criteria, their parent and up to six frozen challengers using the same point-in-time
D1/1m context. Research candidates have no execution authority.

If only a challenger qualifies, the existing two-provider EPE may obtain verified
paper geometry. The canonical output remains FLAT. The hypothetical reference
position exists only inside the existing policy state's research case and never
enters canonical trade history, notification OPEN events or production performance.
A later complete forward path uses the same canonical lifecycle callback; its
outcome is labelled `PROSPECTIVE_COUNTERFACTUAL`, never broker fill or actual P&L.

If the active policy admits, that case waits for its matching canonical actual
close. A virtual gain cannot replace an unresolved or losing actual trade.
All policy votes, criteria/proof hashes, original loss ID, native manifest, runtime
hash and verified initial geometry are frozen before outcomes. Each loss contributes
at most one opportunity, with an additional 27-hour purge. Repeated monitor refreshes
cannot become independent observations. Unknown/missing paths remain incomplete.

This is a paired decision-component experiment: enter at this frozen opportunity
versus abstain at this opportunity. It does not simulate the complete alternative
day/portfolio or assume that abstaining would forbid every later entry. A rule that
changes waiting/confirmation is evaluated on the opportunities it actually qualifies;
an unobserved later signal or quote is never reconstructed as a live opportunity.

## Candidate generation, promotion and rollback

The factory creates coordinate/profile changes in both directions around the active
criteria. Initial research includes lower/higher retracement, lower/higher signal
strength, alternative support and MA periods; later generations explore other
criteria. Reflection may prioritize a hypothesis but contributes zero formal OOS
observations. Every candidate freezes its complete payload, code/evaluator cohort,
source reflection and test plan.

At most six candidates run simultaneously and at most 18 candidates may be tested
in one exact runtime/evaluator cohort. Parked/rejected candidates consume that
budget; changing the parent or returning to an old code cohort cannot restart it.
The family-wise confidence level is `1 - 0.05/18`.

Promotion requires the first 40 preselected resolved opportunities, minimum 15
distinct days, positive corrected day-block lower confidence bound, at least
+0.05 stressed mean net R, positive improvement in both temporal halves and
protected-slice safety (minimum five informative observations; maximum 0.05R
slice degradation). The shared evaluator charges 0.02R when admission votes differ.

A looser policy adds exposure where the baseline abstains. The predeclared test
allows at most 2R of incremental research-sample drawdown relative to the parent;
otherwise loosening could pass only with no losses at all. This is an experiment
acceptance limit, not a change to trade size, initial risk, SL/TP or broker orders.
It is fixed for the test and cannot be optimized after outcomes.

Among independently passing candidates, select highest lower CI, then stressed
mean improvement, then deterministic candidate ID. Materialize exactly that
criteria hash on the future activation boundary. The parent remains registered.

Subsequent fixed windows of 20 purged observations monitor the active policy against
its approved parent. Economic/CI deterioration, slice failure or excessive incremental
drawdown triggers automatic rollback. Three incomplete coverage observations spanning
two hours trigger data-quality rollback. Incompatible code epochs park research,
roll back promoted incompatible ancestors and censor unresolved virtual paths with
no fabricated outcome. Canonical actual cases remain available for actual closes.

## Ownership, migration and validation

`reentry_learning` is an additive field inside
`eurusd_production_policy_state.json`. The main Daily monitor remains the sole writer.
The old actual-only score-margin learner retains its separate contracts.
Every new native manifest includes both the margin policy and the active re-entry
criteria version/hash. The pre-persist validator reconciles that snapshot and all
reference manifests against the append-only decision ledger.

Initial migration preserves historical trades, manifests, reflection and old trials.
It starts a new prospective experiment, with zero imported historical outcomes.
Once introduced, case freezes, closed evidence, candidate samples, terminal gates,
production payloads, monitoring reviews and governance events are append-only.
Validation independently replays promotion/rollback and selection. Code pushes run
validation only. Invalid governance blocks admissions while existing-position
exit-only handling retains its established authority.

Tests include default preservation, bidirectional threshold changes, alternative
support, source/confirmation/thesis requirements, rejected-opportunity capture
without execution, EPE/calendar precedence, no hindsight, sample uniqueness/purge,
actual/counterfactual separation, incomplete data, a measured simulated 30% ->25%
promotion, post-promotion rollback, frozen-state integrity and manifest reconciliation.
A read-only CI rehearsal checks canonical-history compatibility and zero historical
sample import. These checks establish the mechanism, not profitable optimization.
