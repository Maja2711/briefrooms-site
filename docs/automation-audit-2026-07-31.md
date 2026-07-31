# BriefRooms automation audit - 2026-07-31

## Scope and evidence

This audit covers every workflow in `.github/workflows/*.yml` on `main` at
`5d7c918fd6f58bd8918ef45bae1e39bfc3ee43fe`, public GitHub Actions history from
2026-07-24 through 2026-07-31 15:12 UTC, the first failing step logs for the
production-critical runs, the generated public data, and the frontend readers.

The repository produced 2,973 workflow runs in this seven-day window. This is
not evidence of resilience: several scheduled jobs failed on every invocation,
while recovery workflows repeatedly dispatched the same permanent failure.

Status vocabulary in this report is limited to:

- `VERIFIED_WORKING`
- `VERIFIED_FAILED`
- `NOT_VERIFIED`
- `BLOCKED_BY_MISSING_SECRET`
- `BLOCKED_BY_EXTERNAL_PROVIDER`

## Production baseline

| Domain | Evidence at audit start | Status |
| --- | --- | --- |
| PL+EN news | Last success run `30535162845` at 2026-07-30 10:34 UTC; publication `30535162845-1`; latest run `30640736386` failed | `VERIFIED_FAILED` |
| Daily Market Alert | Run `30584219905` succeeded; JSON updated 2026-07-30 22:44:33 +02:00 | `VERIFIED_WORKING` |
| Portfolio 10K prices | Run `30638913429` succeeded; portfolio updated 2026-07-31 16:31:21 +02:00 | `VERIFIED_WORKING` |
| BRACE Portfolio | Run `30635878500` succeeded; baseline remains active and BRACE remains `RECOMMEND_ONLY` | `VERIFIED_WORKING` |
| BRACE-SPX research | Run `30639633355` succeeded | `VERIFIED_WORKING` |
| BRACE-SPX public panel | Run `30620619873` failed; no success in the seven-day window | `VERIFIED_FAILED` |
| Hot X | Run `30619270673` failed; X API returned HTTP 402 | `BLOCKED_BY_EXTERNAL_PROVIDER` |
| Weekly investment pages/positions | Multiple schedulers failed continuously | `VERIFIED_FAILED` |

## Workflow inventory

Abbreviations: `S` schedule, `D` workflow dispatch, `P` push paths, `C` reusable
workflow call, `W` writes contents, `R` reads contents, `M` reads GitHub Models,
`I` writes issues. A dash means the property is absent. `main` in the Publish
column means the workflow commits and pushes directly to `main`; `artifact`
means it uploads a run artifact. Secrets shown as `GITHUB_TOKEN` are the scoped
Actions token, not a repository secret.

| Workflow file | Trigger | Concurrency / cancel | Timeout | Permissions / external API | Owned outputs and publish mode | Last run / last success in window | Status |
| --- | --- | --- | ---: | --- | --- | --- | --- |
| `apply-investments-v140.yml` | D,P | none | default | W | methodology and investment scripts; main | no run found | `NOT_VERIFIED` |
| `brace-historical-accelerator.yml` | S,D,P | per ref / false | 55 | W; market data | BRACE history/memory; main | not present in seven-day run names | `NOT_VERIFIED` |
| `brace-portfolio-daily.yml` | S,D | `portfolio-10k-automation` / false | 30 | W; market data | `data/portfolio10k`; main + artifact | `Review BRACE Challenger` `30546740457` success | `VERIFIED_WORKING` |
| `brace-portfolio-monitor.yml` | S,D | `portfolio-10k-automation` / false | 15 | W; market data | `data/portfolio10k`; main + artifact | included in BRACE runs; exact mapping unavailable | `NOT_VERIFIED` |
| `brace-spx-generation3-panel-install.yml` | D | generation3 panel / false | 10 | W | Gen3 panel status/pages; main | `30619971844` skipped; `30448387012` success | `VERIFIED_WORKING` |
| `brace-spx-generation4-engine.yml` | D | generation4 research / false | 120 | W; market data | research branch + public JSON on main | malformed run `30617592259`; later named run `30617764219` success | `VERIFIED_WORKING` |
| `brace-spx-generation5-engine.yml` | D,P | generation5 research / false | 120 | W; market data | research branch + public JSON on main | `30620122981` success | `VERIFIED_WORKING` |
| `brace-spx-public-panel.yml` | D,P | public panel / false | 10 | W | two SPX pages + sitemap; main | `30620619873` failed; no success | `VERIFIED_FAILED` |
| `brace-spx-recovery-engine.yml` | S,D | hourly sealed research / false | 120 | W; market data | research branch + artifact | `30639633355` success; 39 runs, 35 success | `VERIFIED_WORKING` |
| `content-update-watchdog.yml` | S,D | content watchdog / false | default | W,M,I; Actions API | dispatches news recovery, artifact | `30633373653` failed; `30563650532` success | `VERIFIED_FAILED` |
| `daily-market-alert.yml` | S,D,P | daily alert / true | 45 | W,M; Yahoo, RSS, AI | alert JSON/history and UI; main | `30584219905` success | `VERIFIED_WORKING` |
| `daily-market-alert-watchdog.yml` | S,D,P | alert watchdog / false | 45 | W,M,I; Yahoo, RSS, AI | same alert JSON/history; main | `30588958704` success | `VERIFIED_WORKING` |
| `deploy-production.yml` | S,D,P | Pages / conditional | 15 | R; Pages | GitHub Pages artifact/deploy | `30641734825` skipped; `30640735685` success | `VERIFIED_WORKING` |
| `fix-investments-v140-syntax.yml` | D,P | none | default | W | investment scripts; main | no run found | `NOT_VERIFIED` |
| `hot-x-topics.yml` | S,D,P | `content-publishing` / false | 20 | W; X API and RSS | `data/hot_tweets.json`; main | `30619270673` failed; no success | `BLOCKED_BY_EXTERNAL_PROVIDER` |
| `investment-room-quotes.yml` | S,D,P | quote domain / false | 10 | W; Yahoo | live prices and room quotes; main | `30639464558` success | `VERIFIED_WORKING` |
| `investments-exposure-watch.yml` | S,D,P | paper publishing / false | 15 | W; Yahoo | weekly data/pages; main | `30641080583` failed; no success | `VERIFIED_FAILED` |
| `investments-friendly.yml` | S,D | none | default | W | weekly pages/data; main | `30641262429` failed; no success | `VERIFIED_FAILED` |
| `investments-friendly-min.yml` | S,D | weekly pages / false | 20 | W | weekly pages/data; main | `30639630876` failed; no success | `VERIFIED_FAILED` |
| `investments-monitor.yml` | S,D | none | default | W; Yahoo | weekly pages/data; main | `30638678421` success | `VERIFIED_WORKING` |
| `investments-weekly.yml` | S,D,P | paper publishing / false | 30 | W; Yahoo | weekly pages/data; main | `30630239124` failed; no success | `VERIFIED_FAILED` |
| `investments-weekly-exposure-watchdog.yml` | S,D,P | paper publishing / false | 12 | W; Yahoo | same weekly pages/data; main | `30639785160` failed; no success | `VERIFIED_FAILED` |
| `news-en-hook.yml` | D | none | default | W | dispatch bridge only | no run found | `NOT_VERIFIED` |
| `pages-source-diagnostic.yml` | D | none | default | W; Pages API | diagnostics JSON; main | `30619971906` skipped; no success | `NOT_VERIFIED` |
| `portfolio-10k-brace.yml` | S,D,P | `portfolio-10k-automation` / false | 45 | W; market data | `data/portfolio10k`; main + artifact | `30635878500` success | `VERIFIED_WORKING` |
| `portfolio-10k-brace-bootstrap.yml` | D,P | per event/ref / false | 55 | W; market data | BRACE learning data; main + artifact | no mapped run found | `NOT_VERIFIED` |
| `portfolio-10k-entry-diagnostic.yml` | D,P | diagnostic / false | 15 | W; market data | entry diagnostic JSON; main | no mapped run found | `NOT_VERIFIED` |
| `portfolio-10k-hourly-prices.yml` | S,D,P | `portfolio-10k-automation` / false | 12 | W; Yahoo | portfolio and investment data via broad `git add -A`; main | `30638913429` success | `VERIFIED_WORKING` |
| `portfolio-10k-live-entry.yml` | S,D,P | `portfolio-10k-automation` / false | 15 | W; Yahoo | portfolio JSON/material reports; main | `30634075829` failed; no success | `VERIFIED_FAILED` |
| `portfolio-10k-weekly.yml` | S,D,P | `portfolio-10k-automation` / false | 15 | W; Yahoo, RSS | portfolio JSON/material reports; main | `30635879544` failed; no success | `VERIFIED_FAILED` |
| `publish-en-youtube-recommendations.yml` | D,P | EN YouTube / true | default | W | EN health/science/geopolitics pages; main | `30634942012` success | `VERIFIED_WORKING` |
| `publish-news.yml` | S,D,P,C | news publication / false | 45 | W,M; RSS, article sites, AI | PL/EN home, news, briefs, manifest/reports; main + artifact | `30640736386` failed; `30535162845` success | `VERIFIED_FAILED` |
| `publish-news-recovery-now.yml` | S,D | recovery dispatch / false | default | R; Actions API | dispatches `publish-news.yml` only | `30632506391` failed; no success | `VERIFIED_FAILED` |

## First-failure evidence

| Run / job | First failing step and exact evidence | Root cause | Production impact | Status |
| --- | --- | --- | --- | --- |
| News `30640736386` / `91192169579` | `Prepare one isolated PL and EN publication`: repeated `410 Client Error: Gone for url: https://models.github.ai/inference/chat/completions`; final `each section requires at least 6 approved photo items; zdrowie=5` | Workflow defines `GITHUB_MODELS_API_VERSION=2026-03-10`, but the HTTP client never sends `X-GitHub-Api-Version`. Permanent 410 is then treated as a splittable batch failure. | No atomic PL+EN publication; `last_success_at` remains 2026-07-30. | `VERIFIED_FAILED` |
| News `30635837788` / `91178087574` | Same prepare step and 410, followed by `zdrowie=5` after about 30 minutes | Recursive batch splitting amplified a permanent provider error into dozens of requests. | Slow failure, stale PL and EN. | `VERIFIED_FAILED` |
| Friendly pages `30641262429` / `91191485468` | `Build user-friendly investments pages`: `python: can't open file .../scripts/investments_user_friendly.py: [Errno 2] No such file or directory` | Scheduled workflow references a removed script. | Hourly failure and duplicate ownership of weekly pages. | `VERIFIED_FAILED` |
| Friendly-min `30639630876` / `91185951466` | Missing `scenario_grade_engine.py`/`grade_engine.py`, then `fatal: pathspec 'data/investments/scenario_grades.json' did not match any files` | Obsolete workflow expects removed optional outputs but stages them as mandatory. | Four failures per hour and duplicate page ownership. | `VERIFIED_FAILED` |
| Exposure `30641080583` / `91190875981` | `Persist risk exits immediately`: `error: cannot pull with rebase: You have unstaged changes.` | Generator modifies more owned files than the workflow stages before rebase. | Risk review result is committed locally but never reaches main. | `VERIFIED_FAILED` |
| Exposure watchdog `30639785160` / `91186476805` | `cannot pull with rebase: You have unstaged changes` | Same incomplete staging; watchdog repeats the defect every five minutes. | No recovery; queue and run noise. | `VERIFIED_FAILED` |
| 10K live entry `30634075829` / `91167148705` | `cannot pull with rebase: You have unstaged changes` | Two partial commits leave generated files outside the index before rebase. | Validated portfolio entry/material changes are not pushed. | `VERIFIED_FAILED` |
| 10K weekly `30635879544` / `91173351690` | Validation: `position[zprv].entry_fx_to_pln: differs from staged execution` and `entry_value_pln: differs from staged execution` | Run started from a transiently inconsistent portfolio/staged-entry pair. Current main later converged, but the scheduled path lacks a clean atomic boundary. | Weekly model update rejected. | `VERIFIED_FAILED` |
| Hot X `30619270673` / `91119661362` | X searches: `HTTP Error 402: Payment Required`; final `Hot X update rejected: editorial pins were not preserved at the top` | External X access is not paid/available; malformed RSS fallbacks and an ordering bug then invalidate fallback output. | Hot X remains stale. | `BLOCKED_BY_EXTERNAL_PROVIDER` |
| BRACE-SPX panel `30620619873` / `91124055247` | Test `test_tab_installer_is_idempotent`: `TypeError: install_page() missing 1 required positional argument: 'generation_block'` | Test/call contract does not match the installer signature. | Public panel cannot publish. | `VERIFIED_FAILED` |
| Content watchdog `30633373653` / `91164916046` | Recovery validation: `AssertionError: ['publish-news.yml'] != ['publish-en-youtube-recommendations.yml', 'publish-news.yml']` | EN YouTube workflow is a second writer of pages classified as shared news outputs. | Watchdog fails before making a valid recovery decision. | `VERIFIED_FAILED` |
| Weekly forecasts `30630239124` / `91154644080` | Test `test_v5_policy_does_not_force_continuous_exposure` expects false while current governed policy is `mandatory_monday_position=true` | Test encodes an older policy; production configuration and test contract diverged. | Hourly weekly forecast job stops before generation. | `VERIFIED_FAILED` |

## File ownership and collision map

| Shared output | Current writers | Risk | Required owner/model |
| --- | --- | --- | --- |
| `pl/index.html`, `en/index.html`, `pl/aktualnosci.html`, `en/news.html`, home feeds, brief directories | `publish-news.yml`; EN workflow also contains markers interpreted as ownership | News recovery cannot pass its own ownership gate. | `publish-news.yml` is sole owner. |
| `en/health.html`, `en/science.html`, `en/geopolitics.html` | EN YouTube workflow and public feature workflows | Rebase can overwrite generated sections. | EN YouTube owns only a delimited recommendation fragment or is retired. |
| `data/investments/daily_market_alert.json` and history | alert workflow + alert watchdog | Two generators can race and rewrite the same session. | `daily-market-alert.yml` is sole writer; health audit observes only. |
| weekly investment data and PL/EN weekly pages | weekly, monitor, exposure, watchdog, friendly, friendly-min | Six writers, incomplete staging, continuous conflicts. | One governed weekly publisher plus one risk updater sharing one domain queue and complete staging. Obsolete builders are retired. |
| `data/investments/portfolio_10k.json` | hourly prices, live entry, weekly model | Legitimate shared state, but broad staging and separate partial commits can lose work. | Shared `portfolio-market-data` queue; one atomic commit per run; scoped staging. |
| `data/portfolio10k/*` | BRACE daily, monitor, promotion, bootstrap, historical accelerator | Research data shares one namespace with long jobs. | Shared `brace-portfolio-research` queue; no queue coupling to market-price updates. |
| BRACE-SPX public JSON/pages/sitemap | generation panel installers and public panel | Manual installers can overlap. | Shared `brace-spx-research`/public queue and scoped atomic commits. |
| `data/hot_tweets.json` | Hot X | No internal collision; external provider and fallback validity are the issue. | `social-content` queue; preserve last-good data on provider block. |

## Root causes and repair decisions

1. The original client omitted the API-version header, but the controlled
   production deployment proved that this was no longer the terminal cause:
   GitHub retired GitHub Models entirely on 2026-07-30. The corrected request
   still returns HTTP 410. Production therefore uses OpenAI only, fails before
   publication when `OPENAI_API_KEY` is absent, and never retries the retired
   provider.
2. The PL health source pool contains dead legacy Reuters, malformed AP/WHO, and
   retired NHS feeds. Add working medical/science and FDA feeds while retaining
   the six-item quality gate and 72-hour accepted-item reserve.
3. Recovery workflows are acting as duplicate writers. Replace retrying watchdogs
   with one hourly observer that records attempt/success separately and opens an
   issue only for a new incident fingerprint.
4. Weekly investment publishing has too many owners. Retire obsolete duplicate
   schedules and make the remaining generators stage their complete owned output
   before a normal rebase.
5. Portfolio prices and BRACE research use one global queue despite separate
   outputs. Split them into `portfolio-market-data` and
   `brace-portfolio-research`; leave BRACE in SHADOW/RECOMMEND_ONLY and preserve
   historical entries and the ACTIVE_BASELINE methodology.
6. Public status is inferred from timestamps scattered across data files. Add a
   sanitized `data/system/automation_status.json` with separate
   `last_attempt_at` and `last_success_at`; a failed attempt never advances the
   latter.

## External documentation used

- GitHub Models retirement notice: `https://docs.github.com/en/github-models`
- GitHub REST API versions: `https://docs.github.com/en/rest/about-the-rest-api/api-versions?apiVersion=2026-03-10`

GitHub's current documentation states that the playground, catalog, inference
API and BYOK were fully retired on 2026-07-30. A repository `GITHUB_TOKEN` is no
longer an AI-provider credential. The production quality gate now requires the
repository secret `OPENAI_API_KEY` and reports a configuration blocker when it
is absent.

## Verification ledger

### Implemented on the audit branch

| Repair | Evidence before merge | Status |
| --- | --- | --- |
| AI-provider preflight and fail-fast classification | Retired GitHub Models is not selected; missing `OPENAI_API_KEY` stops before network publication; legacy 410 classification and bounded 429/5xx behavior remain tested | `BLOCKED_BY_MISSING_SECRET` |
| PL health reserve | Six-item minimum retained; dead feeds removed; Medical Xpress, ScienceDaily and two FDA feeds added | `NOT_VERIFIED` |
| Atomic PL+EN news ownership | EN YouTube no longer writes `en/index.html`; publisher remains the only shared news-output owner | `NOT_VERIFIED` |
| Daily Market Alert | One writer; `alert_id` and `updated_at` are validated and exposed by the frontend; duplicate watchdog retired | `NOT_VERIFIED` |
| Portfolio 10K | Price/live-entry/weekly writers share `portfolio-market-data`, use scoped staging and preserve complete generated state | `NOT_VERIFIED` |
| BRACE Portfolio | Research workflows use a separate queue; methodology remains `ACTIVE_BASELINE` plus BRACE `SHADOW`/`RECOMMEND_ONLY`; no entry history changed | `NOT_VERIFIED` |
| BRACE-SPX panel | Installer test now matches the real signature; health combines research and public-panel components and recognizes a completed sealed snapshot | `NOT_VERIFIED` |
| Hot X | The last-good direct-post feed is retained without a fabricated timestamp when X yields no new verified post; the obsolete pin-order postcheck no longer rejects that valid fallback | `BLOCKED_BY_EXTERNAL_PROVIDER` |
| Weekly investments | Obsolete duplicate schedulers retired; governed publisher and exposure monitor share one queue and complete staging; test contract matches the current continuous paper-exposure policy | `NOT_VERIFIED` |
| Central health state | `data/system/automation_status.json` separates attempt, success and data timestamps; stable incident fingerprints prevent duplicate issues; observer never dispatches a failed publisher | `NOT_VERIFIED` |

Retired duplicate/retry workflows:

- `content-update-watchdog.yml`
- `publish-news-recovery-now.yml`
- `daily-market-alert-watchdog.yml`
- `investments-friendly.yml`
- `investments-friendly-min.yml`
- `investments-monitor.yml`
- `investments-weekly-exposure-watchdog.yml`

### Current production evidence before merge

The registry was populated from the public Actions API at 2026-07-31 17:02 UTC.
These runs predate the controlled merge, so they are evidence of the production
baseline, not proof that the branch repair is deployed.

| Domain | Latest attempt | Last success | Published data | Pre-merge status |
| --- | --- | --- | --- | --- |
| PL+EN news | `30643347899`, failed | 2026-07-30 10:48 UTC | 2026-07-30 10:34 UTC | `VERIFIED_FAILED` |
| Daily Market Alert | `30648424480`, success | 2026-07-31 16:46 UTC | 2026-07-31 15:52 UTC | `VERIFIED_WORKING` |
| Portfolio prices | `30648875952`, success | 2026-07-31 16:52 UTC | 2026-07-31 16:52 UTC | `VERIFIED_WORKING` |
| BRACE Portfolio | `30635878500`, success | 2026-07-31 13:48 UTC | 2026-07-31 16:18 UTC | `VERIFIED_WORKING` |
| BRACE-SPX research | `30649330748`, success | 2026-07-31 16:59 UTC | completed sealed result from 2026-07-29 | `VERIFIED_WORKING` |
| BRACE-SPX public panel | `30620619873`, failed | no success in window | pages not republished | `VERIFIED_FAILED` |
| Hot X | `30619270673`, failed | 2026-07-22 08:43 UTC | last-good feed 2026-07-30 16:25 UTC | `BLOCKED_BY_EXTERNAL_PROVIDER` |

### Local verification

- Python: 391 tests passed plus 52 subtests in 67.74 seconds.
- JavaScript: 13 tests passed, including PL/EN home feeds, Hot X, weekly
  investments, Portfolio 10K material reports and the shared header.
- Central-health focused suite: 28 tests passed.
- Current Alert JSON passed version/timestamp validation.
- Current Hot X last-good feed contains eight unique, bilingual direct posts.
- `git diff --check`, Python compilation and Node syntax checks passed.

### Repair commits

1. `ab9e509f` - audit evidence and collision map.
2. `bdb2687f` - GitHub Models preflight and permanent-error handling.
3. `00c2f258` - health-source reserve and news ownership.
4. `7a9c68d5` - versioned, atomic Daily Market Alert.
5. `78af9e65` - domain queues, complete staging and duplicate schedule retirement.
6. `60856442` - central health registry, incident deduplication and truthful Portfolio 10K status.
7. `a818f3f2` - workflow tests aligned with the governed production state.
8. `6cfddc89` - terminal BRACE-SPX and Hot X last-good handling.

This was the pre-deployment verification marker. The authoritative production
results after all controlled merges are recorded in the final section below.

## Controlled deployment evidence

Pull request `119` was merged once to `main` as
`3854b2fe800b739f5c44d64608d5ed7fe4771588`. The resulting production runs
exposed three additional conditions that could not be established from the
pre-merge source alone:

| Domain | Run / job / first failing step | Evidence | Status after deployment 1 |
| --- | --- | --- | --- |
| PL+EN news | run `30651132926`, job `91224363294`, `Check AI provider once before publication` | Corrected one-request preflight returned HTTP 410. GitHub's current documentation confirms full GitHub Models retirement on 2026-07-30; `OPENAI_API_KEY` was empty. | `BLOCKED_BY_MISSING_SECRET` |
| Weekly investments | run `30651132934`, job `91224362940`, `Commit validated weekly updates` | Generator rendered `pl/inwestycje.html`, `pl/inwestycje/pozycje-tygodniowe.html`, `en/investing.html` and `en/investing/open-weekly-positions.html` outside the staged owner set. | `VERIFIED_FAILED` |
| Portfolio 10K weekly | run `30651132928`, job `91224363103`, `Validate portfolio execution and accounting` | Migration compared active execution `2.2-staged-reconciled` with legacy constant `2.0`, reset positions to pending in the runner and then failed frozen-entry validation. The failed run never committed those changes. | `VERIFIED_FAILED` |
| Daily Market Alert | run `30651132927` completed successfully and published commit `97f5b1ac`. | Versioned alert publication and atomic writer worked after the merge. | `VERIFIED_WORKING` |
| Portfolio 10K prices | run `30651132975` completed successfully and published commit `6512ff5b`. | Fresh prices published without blocking the other domains. | `VERIFIED_WORKING` |
| BRACE Portfolio | run `30651132972` completed successfully. | ACTIVE_BASELINE remained unchanged; BRACE remained SHADOW/RECOMMEND_ONLY. | `VERIFIED_WORKING` |
| BRACE-SPX public panel | run `30651132953` completed successfully. | The corrected installer contract published successfully. | `VERIFIED_WORKING` |
| Hot X | run `30651132937` completed successfully. | Last-good direct-post data was retained without a fabricated freshness timestamp. External X HTTP 402 still prevents genuinely new X retrieval. | `BLOCKED_BY_EXTERNAL_PROVIDER` |

Follow-up repairs stage every rendered weekly page and prohibit migration of any
versioned active Portfolio 10K execution. Local validation preserved a
`2.2-staged-reconciled` active entry unchanged and the complete current
Portfolio 10K state validator passed. Their post-deployment run IDs are recorded
after the follow-up controlled merge.

## Final production verification

The following evidence was observed from GitHub Actions and from
`https://briefrooms.com` after the controlled production merges. The functional
production checkout verified end to end was
`8031339cdf04bf733692a22a1c388b3ffd45980f`.

| Domain | Production evidence | Public evidence | Final status |
| --- | --- | --- | --- |
| PL+EN news and AI comments | Run `30654620293`, job `91235859468`, stopped at provider preflight before publication. Diagnostic: `missing_provider_credentials`; required secret: `OPENAI_API_KEY`; GitHub Models retired 2026-07-30. | Last-good publication remains `30535162845-1`, generated `2026-07-30T10:34:24Z`; no false success timestamp was written. | `BLOCKED_BY_MISSING_SECRET` |
| Daily Market Alert | Run `30657050694` completed successfully. | `alert_id=2026-07-31-open-20260731T181859Z`, `updated_at=2026-07-31T20:18:59+02:00`; PL and EN investment pages both rendered the same 20:18 alert. | `VERIFIED_WORKING` |
| Portfolio 10K prices and valuation | Hourly run `30656111107`, staged-entry run `30659475434` / job `91251955482`, and weekly model run `30657598018` all completed successfully. The staged-entry run passed 67 Python tests, the material-report UI test and the full state validator. | `last_updated_at=2026-07-31T20:41:21+02:00`, `last_market_session=2026-07-31`, PLN value `9912.51`, USD value `10068.23`, eight active positions, 57 snapshots and no `last_run_error`. | `VERIFIED_WORKING` |
| Weekly investments and risk exits | Governed weekly run `30654620290` succeeded. Initial exposure run `30655085373`, job `91237387500`, exposed two unstaged generated files. After the ownership repair, run `30659986660`, job `91253648868`, completed every step, including both atomic commits. | Weekly PL/EN pages and v5 exposure state were published by commits `64c8cb35` and `ae754085`. | `VERIFIED_WORKING` |
| BRACE Portfolio | Run `30651132972` completed successfully. | Methodology registry still contains `portfolio-10k-baseline` as `ACTIVE_BASELINE` and `brace-portfolio-engine` as `SHADOW`; historical entries were not rewritten. | `VERIFIED_WORKING` |
| BRACE-SPX | Research run `30656558671` and public-panel run `30651132953` completed successfully. Irrelevant `issues` events that conditionally skip jobs no longer replace the meaningful success in health state. | Public registry reports BRACE-SPX healthy and retains the completed sealed-holdout freshness exemption. | `VERIFIED_WORKING` |
| Hot X | Run `30651132937` completed while deliberately retaining the last-good direct-post feed. Provider evidence remains X HTTP 402. | The feed timestamp remains `2026-07-30T16:25:00Z`; the public registry truthfully reports it stale instead of fabricating freshness. | `BLOCKED_BY_EXTERNAL_PROVIDER` |
| EN YouTube recommendations | Run `30651132933` completed successfully after homepage ownership was removed from this workflow. | EN recommendation pages remain separate from the PL+EN news homepage owner. | `VERIFIED_WORKING` |
| Central automation health | Final run `30660339004`, job `91254798911`, read Actions, validated the public registry and committed sanitized state successfully. | Public `automation_status.json` was generated at `2026-07-31T19:46:06Z`: alert, portfolio, BRACE Portfolio and BRACE-SPX healthy; news missing-secret; Hot X stale. | `VERIFIED_WORKING` |
| Production deployment | Final run `30660363872`, job `91254882200`, validated homepage contracts, confirmed Pages source `main:/`, queued a build and compared production files with the current checkout. | Production matched checkout `8031339cdf04bf733692a22a1c388b3ffd45980f`. | `VERIFIED_WORKING` |

### Additional production-derived root causes

1. GitHub Actions commits made with `GITHUB_TOKEN` do not emit another `push`
   workflow event. `deploy-production.yml` now listens for successful completion
   of every canonical publisher and always deploys the newest `main`.
2. Staged-entry reconciliation reused historical entry dates and regressed
   `last_market_session` from 2026-07-31 to 2026-07-20. Session selection is now
   monotonic across the existing portfolio session, current position quote dates
   and staged executions. No entry price or historical decision changed.
3. The immediate weekly risk-exit commit omitted two files generated by the
   exposure engine. Both state and report are now part of the immediate and final
   atomic owner sets.
4. Unrelated GitHub issues created conditionally skipped BRACE-SPX runs. The
   health registry now ignores only `event=issues` plus `conclusion=skipped`;
   scheduled, push and real failure conclusions remain visible.

### Final repair and deployment commits

- `562e4f9b` - OpenAI-only news provider and production-derived follow-ups.
- `1f0bc5e0` - missing Yahoo earnings-date handling.
- `68df830f` - last-good material reports during Yahoo rate limiting.
- `49363968` - current-main checkout after the Portfolio 10K queue.
- `dc4c390f` - health verification trigger and Portfolio 10K trigger ownership.
- `60b54082` - deployment after action-owned publisher commits.
- `6ad9ac3e` - monotonic Portfolio 10K market-session metadata.
- `07701819` - complete atomic weekly risk-exit publication.
- `8d04f288` - meaningful-run filtering in automation health.

### Final test evidence

- Original audit suite: 391 Python tests plus 52 subtests; 13 JavaScript tests;
  28 focused central-health tests.
- Final ownership and health regression suite: 25 tests passed locally.
- Final Portfolio 10K production run: 67 Python tests and the material-report UI
  test passed; state and material-report validators passed.
- Final deploy run: 13 homepage/redirect tests passed and production parity was
  verified for both homepages, both investment pages, sitemap, robots, build
  version, home briefs and Hot X renderer.
- `git diff --check` and Python compilation passed for every final repair.

### Required owner actions

- Configure repository secret `OPENAI_API_KEY` to resume new PL+EN news and AI
  comment publication. Until then the correct status is
  `BLOCKED_BY_MISSING_SECRET`.
- Restore paid/authorized X API access, or replace the provider with another
  source that legally supplies verifiable direct-post URLs. Until then the
  correct status is `BLOCKED_BY_EXTERNAL_PROVIDER`.
