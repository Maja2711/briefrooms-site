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

1. The GitHub Models endpoint is current, but the required API-version header is
   absent in the Python request. Add a one-request preflight and a typed permanent
   error so 400/401/403/404/410/422 never recurse or retry.
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

- GitHub Models inference REST API: `https://docs.github.com/en/rest/models/inference`
- GitHub REST API versions: `https://docs.github.com/en/rest/about-the-rest-api/api-versions?apiVersion=2026-03-10`
- GitHub Models catalog API: `https://docs.github.com/en/rest/models/catalog`

The documented GitHub Models request uses
`https://models.github.ai/inference/chat/completions`, bearer authorization,
`Accept: application/vnd.github+json`, and `X-GitHub-Api-Version: 2026-03-10`.

## Verification ledger

This section is updated after implementation and the controlled production
merge. At audit-commit time, news, the BRACE-SPX public panel, weekly investment
publishing, and Hot X are not declared repaired.

