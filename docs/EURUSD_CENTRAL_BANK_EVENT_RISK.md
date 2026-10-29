# EURUSD Fed / ECB wire and prospective position-risk assessment

Scope: `AD-06`, `AD-07`, `TR-03` Daily v1.9.1, `TR-05` EURUSD WES only. This extends existing macro adapters, fast lifecycle and WES execution; it does not replace Belief Core, CF-07, EPE, the canonical SL/TP monitor or the event-intelligence bridge.

## Authority and rollout

The committed policy is `eurusd-cb-risk-policy-v1`, version `1.0.0`. Monitoring is enabled; `mode=SHADOW`, `automatic_close_enabled=false`, `stop_upgrade_enabled=false`, `entry_guard_enabled=false`. This PR prepares the entry guard behind its own flag, alongside first prospective source/decision collection. It does not deploy or activate trading protection. A missing/invalid policy fails closed for new admission, while existing hard exits remain independent.

Changing mode to PRODUCTION or enabling close/stop flags raises an error. There is no environment-variable bypass, automatic promotion, generated event-exit request, synthetic stop or fabricated fill. `PROTECT_PROFIT` and `CLOSE_BEFORE_EVENT` are proposals with `execution_authorized=false`, `executed_at=null`. The existing independent macro-risk exits retain their previous authority; the new wire does not feed their directional score. A future production-close implementation requires another reviewed change, lifecycle/EPE proof, prospective comparison and an explicit operator decision. Merely changing the JSON cannot activate it.

After source-health and entry-isolation validation, a separately reviewed policy change may enable only `entry_guard_enabled`. It blocks both pending/recovered and fresh WES fills, plus Daily admission, in high-impact windows or with critical missing/stale wire data. It cannot create direction, reverse or increase exposure. New entry remains subject to WES governance or Daily Belief-first/CF-07, and EPE. Exit-only management remains available. Turning the flag back off rolls back the new admission veto without altering existing positions/history.

## One canonical wire

`scripts/eurusd_central_bank_wire.py` collects four official feeds:

- <https://www.federalreserve.gov/feeds/press_monetary.xml>
- <https://www.federalreserve.gov/feeds/speeches.xml>
- <https://www.federalreserve.gov/feeds/speeches_and_testimony.xml>
- <https://www.ecb.europa.eu/rss/press.html>

Official feed documentation: [Fed RSS](https://www.federalreserve.gov/feeds/feeds.htm), [ECB RSS](https://www.ecb.europa.eu/home/html/rss.en.html). RSS is periodic publication delivery, not a transcript stream.

Only these official domains may supply statements and speeches. Document links come from allowed feed entries; redirects are checked before fetching. The transport bounds bytes, item/document counts, timeouts and retries, uses TLS verification (installed certifi bundle where available), honors conditional HTTP cache headers and rejects DTD/entity/NUL/oversized XML. A valid 304 means a successful source check; it does not fabricate a new publication or first receipt.

`eurusd_central_bank_news_interpreter.py` deterministically extracts the Fed target range or the three separately named ECB rates, with short document evidence. Unparsed facts, previous rates, consensus, surprises and guidance remain null. No chair roster is hardcoded. Official document attribution remains separate from `LIVE_SPEECH_UNVERIFIED`; later transcripts cannot become earlier knowledge. Interpretations have `authority=CONTEXT_ONLY` and cannot manufacture LONG/SHORT.

An optional Google News RSS adapter reuses the existing source-quality registry and exposes only attributed `SECONDARY_HEADLINE` / `HEADLINE_ONLY` context. It never fetches publisher articles or paywalls, verifies speakers or rates, or independently closes a trade. It is disabled by default (`--secondary` is explicit): [Google terms](https://policies.google.com/terms) preserve third-party rights and automated-access restrictions; no blanket publisher licence is claimed. Review applicable feed/publisher terms before enabling this optional source.

The same collector uses the existing canonical parsers for the official [FOMC calendar](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) and [ECB calendar](https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html), with the bounded transport. Receipt times and source health are retained. A date-only calendar's conventional time remains labelled `date_only`, not an exact official schedule. A matched verified official decision subsequently establishes the real publication time and can verify the released event prospectively. Missing critical calendar coverage cannot produce FRESH wire health.

## Contracts, clocks and immutable evidence

`data/investments/eurusd_central_bank_wire.json` uses `eurusd-central-bank-wire-v1`. It includes sources, immutable publication versions, calendar evidence, the event window, health, producer SHA and snapshot SHA-256. Times are aware UTC; canonical calendar parsers use IANA New York/European zones across DST.

Each event preserves `published_at`, original `first_seen_at`, `version_first_seen_at`, `ingested_at`, canonical URL, content hash, dedup key and correction lineage. A correction creates a new version and cannot replace the original event. At decision time every publication/version/receipt must already exist. Snapshot receipts in `eurusd_cb_wire_journal/<snapshot_sha256>.json` are exclusive-create and never overwritten. The bounded current snapshot is a window of up to 1,000 publication versions; older snapshots remain in the journal. Retention changes must archive evidence rather than rewrite decisions.

`publish_eurusd_cb_wire.py` atomically publishes the wire, immutable receipt and optional Daily SHADOW observation with a Git Data API compare-and-swap commit. It has explicit path authority only for these EURUSD files. It never force-pushes, dispatches a trading workflow or writes prices/positions. A raced writer reloads the head; an older/equal snapshot cannot replace a newer one. Previously retained event versions must remain byte-equivalent. A Daily observation is omitted if its source position changed during collection.

Consumers preserve the producer snapshot and hash. Consumption time and decision time are separate audit fields, not updates to original publications. The wire's `consumers.last_seen_at` placeholders stay null in immutable producer snapshots; actual consumption receipts live in each engine's assessments.

## Position economics and uncertainty

`eurusd_central_bank_event_risk.py` exposes pure `assess_pre_event`, `assess_during_event`, `assess_post_event` functions and frozen `EurusdPreEventDecision`. Both adapters normalize their own position geometry and quote facts. Weekly never imports Daily stops or targets; Daily never imports WES risk limits.

The assessor calculates entry-to-initial-stop R, LONG exit at BID / SHORT exit at ASK, explicit net close cost, current net R, stop/target distance, total giveback to the frozen stop, available MFE/giveback and remaining reward. Validated executable quotes require source time, BID/ASK, price/spread bounds and acceptable age/spread spike. OHLC/MID remains `paper_proxy_uncertain`, never a real BID/ASK or brokerage execution. Unknown costs prevent a net-R execution claim. Gaps can exceed a frozen stop; no exit-at-stop promise is made.

The synthetic SHORT test at entry `1.11869`, ASK `1.11369`, SL `1.12487` yields 50 pips profit and 111.8 pips of possible retreat to SL, approximately 0.809R current profit. This fixture exists only in tests. No production trade or historical price is changed.

All three alternatives are explicit. An uncalibrated bounded gate may propose close when continuation reward is small relative to full-stop giveback, or propose profit protection, always in SHADOW. A limited-policy HOLD carries its calculations and uncertainty. Lack of verified quote/event data yields `NO_ACTION_UNVERIFIED` / `PROTECTION_PENDING`, rather than a false executed close. The optional `eurusd_cb_event_distribution.py` reads the single `eurusd_cb_event_distribution.json` contract when present. It requires official event provenance, source BID/ASK paths, explicit incremental costs/slippage and completed +1/+5/+15/+30/+60/+120-minute windows ingested before the decision. It deduplicates events, excludes overlapping samples and matches bank/type/direction and a sourced current regime. At least 40 independent events over 90 days are required. Forecast probabilities, expected net R and ES95 stay null with `RISK_MODEL_UNCALIBRATED` when these inputs are missing. Qualified data may supply expected net R, observed sample-tail ES and observed giveback/SL frequencies as `EMPIRICAL_UNVALIDATED`; predictive probability fields remain null and execution authority remains false. Both adapters accept an explicit sourced current regime; without it they abstain from estimates. No historical dataset is invented or installed in this PR. Deterministic scenarios are not presented as calibrated probabilities.

The window starts at T-120; the five-minute collector/Weekly path covers T-120/T-60/T-30/T-15/T-5 and price/source updates. A calendar timestamp does not prove a statement was released or a conference ended. An unverified conference remains guarded beyond 30 minutes. Confirmed end requires dated same-event official evidence and cooldown. Timeout produces manual-review/recovery status, not a silent entry reopening. Unresolved event windows survive the next day and restart until a sourced end and cooldown. Resolved proof is retained for seven days so a fresh UNKNOWN calendar cannot relatch the guard; a bounded window overflow makes source health PARTIAL and requires manual recovery. Stale/missing critical wire continues to recommend an admission block. Unexpected verified rate statements create a sourced release window even without a calendar prediction.

## Weekly and Daily paths

Weekly fast executor: current quotes -> EURUSD-only canonical hard-SL/TP pass -> central-bank assessment -> governed entry, followed by existing all-instrument safety. The optional audit filter leaves default BTC/SP500 behavior unchanged. `ensure_all` checks admission before recovered pending touches and again at the actual fill time, with point-in-time wire evidence. WES still owns direction, frozen entry plan and EPE. Observations are separate `eurusd_weekly_cb_event_risk.json` and `eurusd_cb_audit/weekly/<immutable_id>.json`; original week/position/history is not enriched or rewritten. The EURUSD quote watchdog preserves the last number while separating stale display from execution freshness and emitting recovery diagnostics.

Daily regular output stores `metadata.central_bank_event_risk` outside the original position manifest. The fast probe evaluates the same policy after the existing hard lifecycle. It never converts a new SHADOW recommendation into an exit. Optional `--cb-audit-dir` exclusively appends content-addressed assessments; a storage failure reports recovery without hiding an old hard-exit trigger. The realtime workflow retains these receipts as a 90-day artifact. The five-minute shared workflow separately produces `eurusd_daily_cb_event_risk.json` and `eurusd_cb_audit/daily/` observations without running trading engines. Original version v1.9.1 and historical manifests remain unchanged; new source files participate in the future admission code fingerprint.

Daily Macro Fast consumes the same wire for official FOMC/ECB actuals, with publication/receipt/source metadata. It does not assign BLS month periods to rate decisions. After the event, a missing statement is `AWAITING_OFFICIAL_DECISION`; verified actuals produce `POST_RELEASE`, `actuals_status=READY`, `post_release_ready=true` even when optional LLM is unavailable. An unrelated consensus cannot become a Fed/ECB surprise. The new CB macro score has zero direct direction authority; Belief Core/CF-07 remains authoritative.

## Operations, UI and alerts

`.github/workflows/eurusd-central-bank-wire.yml` validates on PR/push, collects only on main scheduled/manual runs, every five minutes throughout weekdays. A push cannot execute either trading engine. Macro Fast's context-only schedule covers the whole weekday so conferences/DST gaps are included. Other trading schedulers are unchanged. [GitHub documents delayed/dropped scheduled runs under load](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule); five-minute cron is not a hard latency SLA.

Risk diagnostics include bank/window, source health/age, verified-document link, deduplicated headline count, speaker verification, quote age, profit-at-risk, action/mode, reasons, immutable decision ID, policy/source hashes and latency components. Alert codes include stale sources, missing executable quote, profit at risk, unverified live speech and recovery. They are saved/displayed as operational observations. Existing commit-bound position notifications remain based solely on canonical OPEN/CLOSED transitions; no SHADOW proposal emits a fabricated CLOSE notification.

`eurusd-cb-risk-public.js` adds a small PL/EN source/risk panel only beside EURUSD Daily and the selected EURUSD Weekly card. It shows SHADOW, freshness, price uncertainty and an allowlisted official link, never a headline as a live quote. Missing/stale data is visible. BTC/SP500 cards and history remain unchanged. Current assessment is not attached to another historical week.

The optional dedicated `workers/eurusd-cb-wire` cache and `eurusd_cb_poll_service.py` prepare a separate lower-latency deployment using the same Python collector and an authenticated EURUSD-only cache. They have no trading/workflow authority. No service, namespace, endpoint, secret or Worker was provisioned by this PR. A configured 60-second interval is not a measured deployed latency guarantee. Existing shared Workers are untouched.

## Validation and activation evidence

Offline tests mock HTTP and explicitly label synthetic data. They cover Fed/ECB facts, correction/dedup, future/late publication, secondary headlines, changing speaker attribution, missing consensus, malformed/unsafe XML and redirects, rate-limit/retries/304, official-calendar receipts, DST, +50-pip economics, spread spike, stale quotes, outage, guard/conference timing, both lifecycles, serialization/immutable manifests and isolated non-EURUSD state.

`eurusd_cb_shadow_replay.py` accepts only captured point-in-time episodes and a preregistered chronological sample, separates training/test and rejects overlap/look-ahead. It compares the frozen baseline with hypothetical policy after executable quote costs and stress slippage, with no ex-post favourable fills or fictitious stop upgrades. No available real eligible episodes means insufficient evidence, not zero returns or a successful backtest. Any report remains SHADOW and cannot activate production.

Before an automatic close/stop rollout: obtain representative real Fed/ECB event regimes and sufficient independent held-out events, demonstrate original lifecycle parity plus net/drawdown/tail-loss/cost/latency sensitivity, inspect unnecessary exits/lost upside, complete prospective SHADOW monitoring, then review an explicit execution implementation and operator approval. The initial policy's 40 independent-event minimum is preregistered and is not proof of adequacy on its own.

Observed live probe on 2026-10-28: four official RSS checks and both canonical calendar sources succeeded; 12 real publication versions, one verified FOMC decision, valid snapshot hash; one collector round took 2.08 seconds. These are source receipts, not replay performance or a production deployment. No p50/p95/p99 latency claim is made from one sample.


### Retention budget

Five-minute weekday polling produces approximately 75,000 immutable receipts per year. The observed initial full snapshot was about 52 KB; raw repeated snapshot bytes therefore grow by roughly 3.9 GB/year even before a larger publication window. Git packing may reduce this, but is not a retention policy. Budget and monitor repository/DO growth during initial SHADOW collection; an approved archive must preserve hashes, publication versions, original decision inputs and closed evaluations. Never delete or rewrite existing history to reduce size. Realtime audit artifacts retain 90 days; that is an operational recovery window, not permanent proof for automatic-close certification. The optional DO has no deletion/compaction authority in this PR.

The EURUSD-only Fed calendar wrapper bounds the inherited parser at the next year heading, whether years are ascending or descending. This prevents previous-year dates becoming invented current-year decisions, without changing other calendar consumers. Per-bank document reserves and within-feed decision/conference priority prevent Fed context volume from starving a current ECB decision.
