# Stock Trading CASH edge learning

This change extends the existing TR-04 / LE-01..09 / LE-11 research and promotion paths. `main` owns production policy, trial registration, source receipt verification and promotion. The research branch owns candidate discovery, immutable prospective cohort membership and evaluation reports. A research PASS grants no production authority.

## Current prospective protocol v2

New CASH trials declare `stock-trading-cash-edge-protocol-v2`. Existing v1 trials, member files, reservations and terminal verdicts retain their original contract; they are never converted, deleted or restarted. The v1 description below remains applicable to those trials. Both versions spend the **same lifetime maximum of 24 tests**, at alpha 0.05/24 per trial. A new code, policy or protocol identity cannot reset that budget. The only learned parameter remains the bounded, single-market CASH edge, baseline ±1 within [1, 15].

V2 freezes one first-eligible main-native admission anchor, after main trial registration and before the **first** future regular close. Main must receive that immutable anchor before that close, also for a two-session trial. The anchor commits the whole future calendar and input tape; no later enrollment can select convenient winners. Missing earlier research source/admission files block enrollment. An anchor more than 24 hours late is permanently censored; absent inputs wait up to the fixed 180-day deadline and then fail without replacements.

The anchor's hashed main frame must contain a prospectively received `stock-trading-session-calendar-v1` receipt: market, timezone, source and URL, covered interval, and every scheduled session with opening and closing times. The endpoint is exactly the **60th scheduled regular close after the decision**. It cannot move after results. Each scheduled session additionally needs matching native provider close metadata and a receipt at or after its sourced close, within 24 hours and before the next scheduled opening. Unknown calendars, exceptional schedule disagreements, missing closes and late repairs remain PENDING; a later observed close cannot replace an omitted session. Sixty ordinary sessions usually span roughly twelve weeks, but this is a coverage target, not a promised completion date or a guaranteed PASS.

The audited production runner executes **one continuous Champion book and one continuous Challenger book** on every native frame from the anchor to the fixed endpoint. Only the first canonical snapshot initializes them. Later canonical snapshots never reset either book; stops, positions, runners, cash, runtime phase, costs, admissions and re-entry state continue throughout. No boundary forces an exit. Horizon 1 reports 60 consecutive session windows; horizon 2 reports 30 consecutive two-session windows. Both production horizon gates remain required, and are explicitly **correlated checks of the same strategy**, not independent replications.

The forward valuation of inherited positions uses their frozen opening marks and quantities, with the prospectively declared liquidation stress-cost model. Shared pre-trial gains and unknown historical entry costs do not become trial alpha. This is a counterfactual future-cost comparison, not a reconstruction of broker fill costs. Canonical historic positions remain unchanged. Missing starting marks, quantities or an explicit future-cost basis remain unscorable. New entries retain their complete frozen entry/exit cost treatment.

Every sourced session close additionally requires an explicit native close-valuation receipt, including a raw bar ending exactly at the regular close for every still-open position in either virtual book. Incidental closed-session metadata in an unrelated admission frame cannot replace that receipt. The runner updates only its valuation mark from that bar; it does not trade, review a thesis, move stops or force an exit after the regular session. A stale pre-close mark or an extended-hours price cannot satisfy this NAV gate. Missing virtual runner bars leave evidence PENDING with native replay diagnostics. New future collection watches retain receipts for at most 180 days, with at most 40 active watches and 40 new names per market/week. Original 168-hour/two-new-name watch receipts remain immutable; capacity cannot be filled with reconstructed prices. The data-collection budget grants no additional trading, position or risk authority and does not promise complete universe coverage.

### Dependence and the formal statistic

V2 aggregates the **whole portfolio's paired net NAV increments by local calendar week**, including partial and zero-result weeks. Tickers and simultaneous positions are not counted as independent observations. Shared position exposure across weeks is reported. The sole formal statistic is a predeclared betting score:

`x_w = clip(weekly net NAV difference in percentage points / 1 pp, -1, 1)`

`E = product_w (1 + 0.9 * x_w)`; required `E >= 1 / alpha = 480`.

Its declared null is **E[x_w | the past] ≤ 0 for every observed week**. Each nonnegative factor has conditional expectation at most one under that null, so the product is a test supermartingale; Markov/Ville controls rejection at the stated alpha. Independence of weeks is unnecessary for this conditional null. It does **not** test every possible unconditional mean null, prove future profitability, or estimate an exact probability that this strategy is better. The bounded score, 1 pp scale and 0.9 fraction are fixed before data, not chosen from observed results. The one formal verdict remains at the fixed endpoint.

Separate, unclipped economic gates require positive cumulative net improvement, positive improvement in both time halves, at least five symbols with actual differential contribution, at least ten calendar-week groups and sixty calendar days, at most 50% of positive benefit from one symbol or one week, sourced benchmark coverage, and no increase in the full continuous portfolio drawdown. A large clipped score cannot hide a large raw loss or one winning runner. All costs and the continuous lifecycle coverage must pass. A deterministic 20,000-draw two-week moving-block bootstrap is **descriptive sensitivity only**; it grants no formal confidence or promotion authority.

Main independently reconstructs the first eligible anchor, validates its original policy and member receipt, derives the exact native calendar endpoint, replays every frame and recomputes all samples and gates. A submitted PASS, altered metric, missing source frame or newly labeled execution semantic cannot substitute for that proof. V2's execution label is accepted only with the exact approved, hashed preregistered protocol.

Method references: [Waudby-Smith and Ramdas, bounded means by betting](https://arxiv.org/abs/2010.09686) supplies the conditional-mean capital-process framework; [Howard et al., nonparametric confidence sequences](https://arxiv.org/abs/1810.08240) gives the supermartingale/Ville context. [Politis and Romano, stationary bootstrap](https://statistics.stanford.edu/technical-reports/stationary-bootstrap) motivates preserving consecutive dependence in descriptive resampling, whose finite-sample reliability is not claimed here. [Bailey et al., probability of backtest overfitting](https://escholarship.org/uc/item/4w1110bb) motivates immutable preregistration and retaining the lifetime search budget. These sources do not establish the profitability of this engine.

## The parameter being learned

The only mutable parameter is `stock_trading_policy.markets.MARKET.minimum_cash_edge_points`, separately for US and GPW. Its initial baseline is 5 points over the frozen CASH utility of 50. Each generation declares the bounded alternatives baseline −1 and baseline +1, within [1, 15]. The patch changes exactly one market and one path in `stock_trading_policy`; it does not change risk, liquidity, execution freshness, sizing, ranking, holding or replacement rules.

Opportunity snapshots use the actual frozen production policy and preserve policy, manifest, portfolio, opportunity configuration, bridge configuration, evidence, source hash and decision time. Experience records distinguish a genuine top eligible new candidate blocked only by CASH margin from an already held candidate, a lower ranked alternative, a full book or a hard gate failure. Legacy GPW entry score 72 versus production score 0 is not an executable v2 CASH experiment.

New experiences carrying the frozen decision context retain each distinct source opportunity, even for the same symbol, selection state and day. A later near-margin decision therefore survives an earlier non-margin scan. Reprocessing the exact source is idempotent and leaves original bytes intact. Contextless historical experiences retain their original daily deduplication behavior; they are neither rewritten nor upgraded into formal evidence. Formal independence is enforced later by the 168-hour cohort rule, rather than by deleting intraday source observations.

## Discovery and the first experiment

Historical next-session-open / frozen-geometry outcomes remain diagnostic research. Regret can explain false positive and false negative margin decisions, but no historical result becomes a formal member. Factory initialization declares both deltas for both markets at both required production horizons (1 and 2 regular-session closes), even when native historical tapes are unavailable. These eight preregistered tests avoid requiring completed exact outcomes before the first trial can collect future inputs. They use eight of the shared lifetime maximum of 24 tests; a code or Champion change does not reset that budget.

The replay tournament may report descriptive native portfolio replay results. It does not select a CASH variant into production. Every formal CASH experiment still needs its own future cohort and main-owned registration receipt.

## Legacy v1 registration and immutable cohort membership

The research append-only `.test-budget` reservation binds a Challenger, protocol and lifetime index. Production separately retains the authoritative lifetime registry, including failed and abandoned trials. Research receives a read-only `stock-trading-component-trials-v1` snapshot through `--trial-receipts`.

Without a matching receipt the evaluator waits in `COLLECTING_FRESH_HOLDOUT` and writes no members. Registration never backdates a decision. Only decisions at or after both `validation_start_at` and production `registered_at` can enroll. Main also records its first observation of each member and independently checks that the member was visible before the completed episode outcome.

An eligible research scan alone cannot enroll: its exact source opportunity must first appear in a valid main-owned ADMISSION input frame. Unobserved research cycles wait without consuming or replacing a member. An earlier native-observed eligible source with missing research/admission files blocks later enrollment. Main reconstructs first-eligible membership from its complete native stream, so deleting an earlier research file cannot improve the cohort. A missing production trial registry fails closed; only explicit initial bootstrap creates the empty lifetime registry.

After formal admissions are frozen, continuous discovery calls the exact evaluator with `--freeze-cohorts-only --trial-receipts PATH` **before** post-session outcome settlement. This mode only appends cohort contracts and members; it performs no replay, statistical look or evaluation write. The normal later exact-evaluation run receives the same trial snapshot plus `--replay-frame-root PATH`.

Each trial enrolls the first 30 chronologically eligible episodes. Decisions are separated by at least 168 hours, use unique source groups, match the frozen policy and manifest, and are enrolled within 24 hours before any known result. Membership is immutable and never outcome ranked. A known result or late enrollment censors the original slot; a later winner cannot replace it. Missing results wait until the fixed 180-day deadline, then terminate the trial. A terminal PASS or FAIL report is byte immutable on every later run.

## Legacy v1 production portfolio episodes

Formal CASH replay uses audited, byte-matching copies of main's portfolio, bridge, cost analytics, quote handling and native replay runner. `CASH_FINGERPRINT_FILES` also binds the opportunity/admission, cohort, statistics, Factory and learner sources. Main imports its reviewed local copies; it never executes research source code during admission or promotion.

An episode starts from the production-owned admission input frame whose original opportunity hash matches the experience source. Main's native quotes, source bars, initial portfolio, runtime phase, bridge configuration and benchmark are replayed through the same production bridge and position lifecycle for Champion and Challenger. The comparison is scoped to the parameter's market. It preserves occupied slots, future admissions, actual quote/session eligibility, aggregate and sector risk, stops, thesis exits, profit-runner ratchets, costs and open-position NAV. A session horizon measures the first one or two actual provider-sourced regular closes after the decision, using recorded post-close receipts. Elapsed calendar days, a research mark or a fixed TP proxy cannot satisfy the horizon. A runner may remain open and is marked at the evaluation boundary; the boundary does not force an exit.

Missing native quotes, source bars, benchmark marks or close receipts stay pending. The report makes a counterfactual production-policy claim, not a broker-fill claim.

## Legacy v1 formal gate and production boundary

The immutable protocol fixes 30 paired independent episodes, 20,000 bootstrap draws, a positive mean and lower bootstrap bound, an exact one-sided paired sign test at alpha 0.05/24, at least five symbols, at least ten days, positive time halves, contribution concentration at most 50%, benchmark availability and no portfolio drawdown increase. No trial can spend more alpha by collecting another look, changing generation or removing a failed member.

The formal proof binds the exact deployment, Champion policy, protocol, production registration, source event/admission hashes, complete cohort, audited code fingerprints, complete paired native replay samples and recomputable statistics. The generic diagnostic evaluator refuses CASH formal verdicts. Main independently loads source files and its own native frames, recomputes the paired lifecycle replay and statistics, and enforces the persistent trial/member receipts before allowing its existing bounded promotion path. The same deployment must meet the existing 1-session and 2-session requirements. Legacy zero-sample or hash-only PASS reports are insufficient.

Both intake and the final core writer require the complete current effective policy and manifest baseline. A previously approved deployment specification cannot substitute for new independently verified proofs for both horizons. Cohort freeze and main first-seen receipt must precede the provider's sourced regular close, not the later time at which the close was collected. Research also censors enrollment when the native horizon close is already known before descriptive settlement.

No migration rewrites old experiences, admissions, closed positions, fills, outcome files or previous verdicts. Missing historical context remains historical research. New evidence and policies apply prospectively.

## Verification

`tests/test_stock_trading_v2_cash_edge_learning.py` covers the one-path mutation, v2 versus legacy attribution, frozen context, immutable membership and terminal verdicts, missing/late/mutated production receipts, 168-hour separation, lifetime alpha reservations, startup without outcomes, freeze-only behavior, real close receipts and statistical rejection cases. Production verifier tests exercise independent native replay and reject incomplete or reconstructed proof.

`tests/test_stock_trading_continuous_protocol.py` additionally covers new-versus-old identities, fixed 60-session calendars at both horizons, continuous actual production books, absence/late receipt/future-input gates, missing first sources and closes, main pre-first-close receipts, independently rejected metric forgery, timeout censoring, immutable terminal reports and protocol-bound execution semantics.
