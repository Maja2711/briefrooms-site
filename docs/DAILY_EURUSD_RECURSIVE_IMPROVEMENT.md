# Daily EURUSD: recursive improvement and trader reflection

Scope: `TR-03`, local learner `LE-08`, existing `LE-11` contracts. This extends
`daily_eurusd_production_policy.py`; it does not create another trading engine,
state writer, generic Evolution Controller adapter or execution authority.

## Operating mode

`AGGRESSIVE_BOUNDED` runs at every completed canonical Daily monitor cycle.
Actual closures, including realtime-watcher closures, enter persistent reflection
on the next full monitor. Every candidate entry consumes a time-causal memory
review. Each newly known outcome or changed candidate/gate/rollback produces a
hash-linked improvement iteration. An unchanged refresh creates no fictitious
learning event.

The loop is: actual entry and outcome -> observed failure context -> tentative
lesson -> bounded hypotheses -> parallel prospective tests -> exact winning
policy -> post-promotion monitoring -> rollback or next research generation.
Failed tests, protected-slice blockers and rollback IDs feed the next iteration.
The mechanism learns a bounded admission policy; it does not rewrite its own
source code or claim unrestricted model training.

## Persistent experience and pre-entry reflection

Memory is `recursive_improvement` inside the existing
`eurusd_production_policy_state.json`. The main Daily monitor remains its sole
writer. No new ledger or scheduled writer is introduced. The existing post-trade
audit and exit research keep their diagnostic ownership.

Each native EURUSD closed trade freezes its canonical trade hash, unchanged
entry-manifest hash, actual close, initial entry/risk, independently recomputed
net R, original evidence fingerprint, entry confidence/coverage and observed
context. Examples: narrow directional margin; low Belief confidence; re-entry
within one hour of a same-direction loss; unchanged original source evidence.
Unknown evidence is recorded as unknown. An outcome does not prove causation,
and a losing high-quality setup is not automatically called a bad decision.

Winning trades are retained in every relevant context. Research hypotheses
count both avoided losses and lost winners. A single example remains
`HYPOTHESIS_ONLY`; repeated association is still not promotion proof.
Historical trades may seed discovery only. Old runtime cohorts never become
current-code OOS evidence. Legacy trades without native entry manifests are
counted separately and never reconstructed.

Before entry, `execution_admission.agent_reflection` records similar known
actual trades, mean net R, recent loss IDs, relevant lessons, source-evidence
change and the memory iteration hash. The immutable admission manifest freezes
that review. It is explanatory memory; approved policy is the decision mechanism
that may abstain. Reflection cannot bypass a failed price, evidence or risk gate.

## Parallel research with bounded production authority

Instead of waiting for sequential `3 -> 4 -> 5 -> 6` promotions, the factory
tests every stricter remaining minimum score distance from 50 in parallel.
A baseline of 3 can test 4, 5 and 6 on the same prospectively frozen opportunities.
A candidate can only increase the active floor, never exceed 6 or reduce it.
The approved parent remains available for governed rollback.

All votes and sample membership are frozen before future outcomes. Every target
floor has at most three attempts per exact runtime/evaluator cohort, shared
across parent-policy changes. Three target floors times three attempts bounds
the entire statistical family at nine. The existing family-wise confidence
adjustment, 30-day retry cooldown and sample requirements remain intact.

Each candidate needs the first 40 canonical actual admissions, purged by 27h,
minimum 15 independent days, positive multiplicity-adjusted day-block lower CI,
at least +0.05 stressed mean net R, temporal stability, protected slices and no
drawdown increase. Counterfactual, historical-discovery and partial-sample
results cannot authorize promotion.

When several independently passing candidates compete, select the highest lower
CI, then highest stressed mean improvement, then the least restrictive floor.
Each sibling keeps its own complete gate; only one exact evaluated hash is
materialized. A failed candidate is never promoted because discovery preferred
it. An unselected sibling is terminal with an explicit parent-replacement reason.
The validator independently replays selection and the governed version timeline.

Fixed 20-observation monitoring windows, actual/counterfactual separation,
automatic rollback, code-cohort parking/censoring and exit-only handling of
invalid governance remain in force. After rejection or rollback, later generations
can be created within the same attempt budget and cooldown; unsuccessful evidence
cannot be discarded to restart an easier test.

## Hard boundaries

The final direction remains Belief Core -> EpistemicState -> CF-07 -> native
LONG/SHORT/FLAT. Research cannot change direction, position size, SL/TP, execution
costs, other instruments or source facts. It cannot loosen the user's post-loss
requirements: >=30% D1 correction, tested strong support/resistance, and a fresh
strong native signal. Calendar, same-thesis memory, technical safety, current
policy and verified EPE all remain mandatory.

The reflection implementation joins the runtime code hash and evaluator identity.
An upgrade parks incompatible research cohorts and uses the existing future-only
rollback rules. The optional memory field migrates additively on the next scheduled
monitor. Existing history, manifests and outcomes are unchanged. Once introduced,
memory records and recursive iterations are append-only; disappearance fails
closed for new admissions. Existing OPEN positions retain risk-exit handling.

## Validation and meaning of success

Deterministic tests cover actual R reconciliation, LONG/SHORT loss symmetry,
first-hour repetition, genuine original-source changes, unknown data, wins,
no future leakage, cohort isolation, memory integrity, recursive feedback,
parallel ex-ante votes, fixed-sample gating, selection, attempt budgets and
hard-gate precedence. A read-only CI rehearsal also reflects the repository's
actual canonical history and reports its findings without persisting trading
state. Code pushes and pull requests remain validation-only.

Passing CI verifies the mechanism and its boundaries. It does not establish
improved profitability. Production learning starts collecting new-code
prospective evidence after deployment; a genuinely passing promotion and its
subsequent monitored results are separate operational evidence.
