# Stock Trading CASH edge learning

This change extends the existing TR-04 / LE-01..09 / LE-11 research and promotion paths. `main` owns production policy, trial registration, source receipt verification and promotion. The research branch owns candidate discovery, immutable prospective cohort membership and evaluation reports. A research PASS grants no production authority.

## The parameter being learned

The only mutable parameter is `stock_trading_policy.markets.MARKET.minimum_cash_edge_points`, separately for US and GPW. Its initial baseline is 5 points over the frozen CASH utility of 50. Each generation declares the bounded alternatives baseline −1 and baseline +1, within [1, 15]. The patch changes exactly one market and one path in `stock_trading_policy`; it does not change risk, liquidity, execution freshness, sizing, ranking, holding or replacement rules.

Opportunity snapshots use the actual frozen production policy and preserve policy, manifest, portfolio, opportunity configuration, bridge configuration, evidence, source hash and decision time. Experience records distinguish a genuine top eligible new candidate blocked only by CASH margin from an already held candidate, a lower ranked alternative, a full book or a hard gate failure. Legacy GPW entry score 72 versus production score 0 is not an executable v2 CASH experiment.

New experiences carrying the frozen decision context retain each distinct source opportunity, even for the same symbol, selection state and day. A later near-margin decision therefore survives an earlier non-margin scan. Reprocessing the exact source is idempotent and leaves original bytes intact. Contextless historical experiences retain their original daily deduplication behavior; they are neither rewritten nor upgraded into formal evidence. Formal independence is enforced later by the 168-hour cohort rule, rather than by deleting intraday source observations.

## Discovery and the first experiment

Historical next-session-open / frozen-geometry outcomes remain diagnostic research. Regret can explain false positive and false negative margin decisions, but no historical result becomes a formal member. Factory initialization declares both deltas for both markets at both required production horizons (1 and 2 regular-session closes), even when native historical tapes are unavailable. These eight preregistered tests avoid requiring completed exact outcomes before the first trial can collect future inputs. They use eight of the shared lifetime maximum of 24 tests; a code or Champion change does not reset that budget.

The replay tournament may report descriptive native portfolio replay results. It does not select a CASH variant into production. Every formal CASH experiment still needs its own future cohort and main-owned registration receipt.

## Registration and immutable cohort membership

The research append-only `.test-budget` reservation binds a Challenger, protocol and lifetime index. Production separately retains the authoritative lifetime registry, including failed and abandoned trials. Research receives a read-only `stock-trading-component-trials-v1` snapshot through `--trial-receipts`.

Without a matching receipt the evaluator waits in `COLLECTING_FRESH_HOLDOUT` and writes no members. Registration never backdates a decision. Only decisions at or after both `validation_start_at` and production `registered_at` can enroll. Main also records its first observation of each member and independently checks that the member was visible before the completed episode outcome.

An eligible research scan alone cannot enroll: its exact source opportunity must first appear in a valid main-owned ADMISSION input frame. Unobserved research cycles wait without consuming or replacing a member. An earlier native-observed eligible source with missing research/admission files blocks later enrollment. Main reconstructs first-eligible membership from its complete native stream, so deleting an earlier research file cannot improve the cohort. A missing production trial registry fails closed; only explicit initial bootstrap creates the empty lifetime registry.

After formal admissions are frozen, continuous discovery calls the exact evaluator with `--freeze-cohorts-only --trial-receipts PATH` **before** post-session outcome settlement. This mode only appends cohort contracts and members; it performs no replay, statistical look or evaluation write. The normal later exact-evaluation run receives the same trial snapshot plus `--replay-frame-root PATH`.

Each trial enrolls the first 30 chronologically eligible episodes. Decisions are separated by at least 168 hours, use unique source groups, match the frozen policy and manifest, and are enrolled within 24 hours before any known result. Membership is immutable and never outcome ranked. A known result or late enrollment censors the original slot; a later winner cannot replace it. Missing results wait until the fixed 180-day deadline, then terminate the trial. A terminal PASS or FAIL report is byte immutable on every later run.

## Actual production portfolio replay

Formal CASH replay uses audited, byte-matching copies of main's portfolio, bridge, cost analytics, quote handling and native replay runner. `CASH_FINGERPRINT_FILES` also binds the opportunity/admission, cohort, statistics, Factory and learner sources. Main imports its reviewed local copies; it never executes research source code during admission or promotion.

An episode starts from the production-owned admission input frame whose original opportunity hash matches the experience source. Main's native quotes, source bars, initial portfolio, runtime phase, bridge configuration and benchmark are replayed through the same production bridge and position lifecycle for Champion and Challenger. The comparison is scoped to the parameter's market. It preserves occupied slots, future admissions, actual quote/session eligibility, aggregate and sector risk, stops, thesis exits, profit-runner ratchets, costs and open-position NAV. A session horizon measures the first one or two actual provider-sourced regular closes after the decision, using recorded post-close receipts. Elapsed calendar days, a research mark or a fixed TP proxy cannot satisfy the horizon. A runner may remain open and is marked at the evaluation boundary; the boundary does not force an exit.

Missing native quotes, source bars, benchmark marks or close receipts stay pending. The report makes a counterfactual production-policy claim, not a broker-fill claim.

## Formal gate and production boundary

The immutable protocol fixes 30 paired independent episodes, 20,000 bootstrap draws, a positive mean and lower bootstrap bound, an exact one-sided paired sign test at alpha 0.05/24, at least five symbols, at least ten days, positive time halves, contribution concentration at most 50%, benchmark availability and no portfolio drawdown increase. No trial can spend more alpha by collecting another look, changing generation or removing a failed member.

The formal proof binds the exact deployment, Champion policy, protocol, production registration, source event/admission hashes, complete cohort, audited code fingerprints, complete paired native replay samples and recomputable statistics. The generic diagnostic evaluator refuses CASH formal verdicts. Main independently loads source files and its own native frames, recomputes the paired lifecycle replay and statistics, and enforces the persistent trial/member receipts before allowing its existing bounded promotion path. The same deployment must meet the existing 1-session and 2-session requirements. Legacy zero-sample or hash-only PASS reports are insufficient.

Both intake and the final core writer require the complete current effective policy and manifest baseline. A previously approved deployment specification cannot substitute for new independently verified proofs for both horizons. Cohort freeze and main first-seen receipt must precede the provider's sourced regular close, not the later time at which the close was collected. Research also censors enrollment when the native horizon close is already known before descriptive settlement.

No migration rewrites old experiences, admissions, closed positions, fills, outcome files or previous verdicts. Missing historical context remains historical research. New evidence and policies apply prospectively.

## Verification

`tests/test_stock_trading_v2_cash_edge_learning.py` covers the one-path mutation, v2 versus legacy attribution, frozen context, immutable membership and terminal verdicts, missing/late/mutated production receipts, 168-hour separation, lifetime alpha reservations, startup without outcomes, freeze-only behavior, real close receipts and statistical rejection cases. Production verifier tests exercise independent native replay and reject incomplete or reconstructed proof.
