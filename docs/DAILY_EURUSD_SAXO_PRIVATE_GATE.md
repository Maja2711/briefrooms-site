# BriefRooms EURUSD Daily — private Saxo SIM paper Bid/Ask, public Yahoo

## Purpose / licensing boundary

- **Private EURUSD Daily execution**: entry price from Saxo SIM ASK for LONG / BID for SHORT; close from Saxo SIM BID for LONG / ASK for SHORT, retrieved at close decision time. This is a paper simulation (no Saxo order and NO claim that a brokerage fill occurred).
- **Public BriefRooms website**: its existing Yahoo-based display/monitor and separately sourced public paper engine remain unaffected. **Never publish** exact Saxo opening/closing prices, derived P/L, fills, order history or raw response without explicit rights. Saxo's `redistributable:false` flag is honored.
- **Technical signal for exit**: currently comes from the existing Yahoo 1m OHLC bars and from macro/Belief/R_PACE risk logic. Only the **private close price** is taken from Saxo API, as of Saxo's recent timestamp, and not from old Yahoo stop/target/bar close.
- Public use of Yahoo quotes is also governed by Yahoo/third-party terms and is not itself a blanket redistribution license.

## Runtime modes

- Public GitHub Actions Daily `main` — `SAXO_PRIVATE_PRIMARY` unset: no Saxo fetch, existing public prices unchanged.
- Private standalone Daily — `BRIEFROOMS_PRIVATE_RUNTIME=1`, `SAXO_PRIVATE_PRIMARY=1`, `BRIEFROOMS_PRIVATE_DATA_DIR` absolute and outside public repo: private entry, open-position state and history live outside repository and public website. Files MUST NOT be committed/uploaded/published.
- Private five-second fast lifecycle watcher uses the **same v18 exit evaluator** as Daily; it refuses to write Saxo-backed positions to public files. No Saxo token, bid/ask or P/L is sent to the public trading-push Worker.

## Pricing and safety

1. Native Belief Core remains owner of direction. H1 Bollinger range guard, D1/H1 analysis, R_PACE, stop/target priorities and context checks remain in their existing code.
2. New entry first requires **two fresh mutually consistent independent public quotes**, then a fresh, non-delayed Saxo SIM EURUSD quote. Difference of Saxo MID versus reference >2.5 pips blocks entry.
3. Saxo QUOTE is validated for market Open, real-time, up to 90s old (at decision), coherent MID/BID/ASK, `trading_enabled:false`, `redistributable:false`.
4. Private paper entry: LONG at Saxo ASK, SHORT at Saxo BID. No artificial 1.5-pip buffer added on top of Saxo spread. Frozen synthetic spread in execution-cost contract remains a **risk-estimation proxy for analytical Yahoo OHLC**; never reinterpret it as a real broker execution.
5. Exit triggers come from existing SL/TP/dynamic/macro event evaluator using Yahoo 1m bars and decision stack. If it requests closure, obtain **another Saxo quote** no older than 15 seconds. LONG exits at Saxo BID, SHORT at Saxo ASK. Close timestamp is the actual Saxo quote timestamp; the original Yahoo trigger is kept solely for private diagnostic context.
6. **Fail closed:** if Saxo is unavailable/stale or the private runtime flag is missing, do not record a fabricated Yahoo-priced Saxo closure. The position remains OPEN and must be supervised. Since this is paper trading, this is **not** guaranteed protective live SL execution; outage and quote gaps can lead to materially worse paper exit.
7. A dedicated private D1 scheduler exists but starts only after explicit activation and successful D1 initialization. Public Daily cannot write Saxo prices/history to GitHub. A successful code merge alone does not prove live automation.

## Required private authentication

The Cloudflare Worker `briefrooms-saxo-eurusd` has working Saxo SIM OAuth and its own D1 token storage. The Python adapter currently expects HTTP Basic (`SAXO_PRIVATE_BASIC_USER`, `SAXO_PRIVATE_BASIC_PASSWORD` = password set as Worker's `SAXO_ADMIN_PASSWORD`). Browser access may be authorized by a cookie instead of Basic; this needs a separate real machine-to-machine test **before** enabling automatic trading simulation. Never paste API client secrets or the Saxo account password into chat, code or public Actions logs.

## Diagnosing authenticated HTTP 401/403 (read-only)

GitHub Actions workflow `saxo-private-connection-diagnostic.yml` runs manually and on a scoped
push that changes its diagnostic workflow. Run it from the default `main` branch after
setting `SAXO_PRIVATE_BASIC_PASSWORD`. A stored secret proves nothing about
the downstream Worker auth contract.

- `BLOCKED_MISSING_GITHUB_SECRET`: secret unavailable to this run; check repo/environment scope.
- `STATUS_ENDPOINT=HTTP_401` or `HTTP_403`: GitHub reached an HTTP denial.
  It does **not** prove the password is wrong. Inspect the Worker's authentication
  handler and any Cloudflare Access / WAF policies for `/status`.
- `AUTH_STATUS_CHALLENGE_SCHEME` and `AUTH_STATUS_CF_CHALLENGE` report only
  safe header classifications. `ANONYMOUS_STATUS_ENDPOINT` and
  `SAME_HTTP_STATUS_WITHOUT_BASIC` compare HTTP codes with/without Basic.
  Identical HTTP codes **do not** prove the same authentication path.
- Only `STATUS_ENDPOINT=AUTHORIZED_SIM_CONNECTED` and
  `QUOTE_ENDPOINT=FRESH_BID_ASK_VALIDATED` with
  `PASS_AUTHENTICATED_PRIVATE_SAXO_SIM_READ` establish a successful
  independent, private machine-to-machine read.

The test must never print Basic passwords, OAuth credentials, raw response
bodies, Saxo BID/ASK or private P&L. Do not disable the Worker's access
checks to make the test pass. If Cloudflare Access fronts the Worker,
configure an appropriately scoped service-to-service policy/credential
instead of relying on an interactive browser cookie.

A passing read test is **not** a private scheduler or live paper position
lifecycle. Continuous isolated runtime/storage, token refresh, read-side
freshness, and controlled paper open/close must still be verified independently.

## Private standalone Windows example

The `SAXO_PRIVATE_PRIMARY` env flag alone does **not** switch public GitHub Daily to Saxo. Keep Saxo-derived state and history strictly in a private directory:

```powershell
$env:PYTHONPATH = "scripts"
$env:BRIEFROOMS_PRIVATE_RUNTIME = "1"
$env:SAXO_PRIVATE_PRIMARY = "1"
$env:SAXO_PRIVATE_QUALITY_GATE = "0"
$env:BRIEFROOMS_PRIVATE_DATA_DIR = Join-Path $HOME "BRsPrivateSaxoEURUSD"
$env:SAXO_PRIVATE_BASIC_USER = "admin"
New-Item -ItemType Directory -Force -Path $env:BRIEFROOMS_PRIVATE_DATA_DIR | Out-Null
$sec = Read-Host "Cloudflare Worker private HTTP password (NOT Saxo account password)" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
try { $env:SAXO_PRIVATE_BASIC_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
python scripts/daily_eurusd_spot_v19.py `
  --output "$env:BRIEFROOMS_PRIVATE_DATA_DIR/eurusd_daily_spot.json" `
  --history "$env:BRIEFROOMS_PRIVATE_DATA_DIR/eurusd_daily_history.json"
Remove-Item Env:SAXO_PRIVATE_BASIC_PASSWORD
```

The private history is separate from existing public learning policy and Daily state. It does not claim or emulate historical Saxo fills. For an existing private OPEN Saxo-backed trade, fast exit-only code can run with the same private env and files:

```powershell
python scripts/daily_eurusd_fast_lifecycle.py `
  --output "$env:BRIEFROOMS_PRIVATE_DATA_DIR/eurusd_daily_spot.json" `
  --history "$env:BRIEFROOMS_PRIVATE_DATA_DIR/eurusd_daily_history.json" --apply
```

Before production-like continuous private use, design private durable storage and scheduling with credentials isolated from public GitHub and public website, plus explicit alerting on stale Saxo/missing close.

## Regression

```powershell
$env:PYTHONPATH = "scripts"
python -m unittest tests.test_saxo_private_feed tests.test_saxo_private_primary tests.test_saxo_private_execution_exit -v
```

The merger never auto-enables private mode or copies Cloudflare credentials to GitHub. A complete live validation requires: authenticated Worker fetch from an independent process (no browser cookie), stable token renewal, a real private entry + a real private exit in a controlled simulated trade, and confirmation that public artifacts continue to use only independently permitted public display data.


## Automatic Saxo EURUSD Daily — D1 Free, EURUSD only

**R2 was superseded.** Do not activate an R2 paid subscription or configure
R2 keys for this strategy. The old R2 Saxo scheduler has been removed; its
unused runner source remains only for offline snapshot encoding and the
private v1.9 lifecycle helper.

The correct workflow is
`.github/workflows/saxo-private-eurusd-daily-d1-cycle.yml`.
It is an isolated five-minute **paper** EURUSD cycle (not live broker orders).
The existing public Yahoo Daily and every other instrument remain unchanged.

### One private D1 database

Create exactly one separate Cloudflare D1 database,
`brs-saxo-eurusd-private`. Do **not** write this state into the existing
`briefrooms-saxo-oauth` D1 database, which stores Saxo OAuth tokens.
A separate database isolates both privileges and backup/restore operations.
On the Cloudflare Workers Free plan, the database limit is 500 MB, the
account-wide D1 storage allowance is 5 GB, and exceeding daily usage limits
causes query failures rather than automatic overage charges. Keep the
account on Workers Free; do not activate the Workers Paid plan.

The database stores one **compressed, checksum-protected** state row only:
private EURUSD Daily position, append-only completed-trade history, policy
state and decision ledger. There is no archival write for every five-minute
market quote. A single database row's capacity is limited to 2 MB by D1;
the runner stops (fail-closed) before an encoded state exceeds 1.5 MB, so
long-lived history may eventually need an archival design. Do not treat D1
as infinite storage.

Cloudflare dashboard:
1. Storage & databases → D1 SQL Database → Create database.
2. Name it `brs-saxo-eurusd-private` and retain its UUID/database ID.
3. Create a scoped Cloudflare API token with D1 **Write** permissions
   for this account. Store token securely; do not use the Saxo account
   password or the OAuth client secret.

GitHub → Settings → Secrets and variables → Actions:

- Repository **variable** `SAXO_PRIVATE_D1_DATABASE_ID` = database UUID.
- Repository **secret** `SAXO_PRIVATE_D1_API_TOKEN` = scoped D1 API token.
- Existing `CLOUDFLARE_ACCOUNT_ID`, `SAXO_PRIVATE_BASIC_PASSWORD` remain.
- Repository **variable** `SAXO_PRIVATE_AUTO_ENABLED` = `false` initially.

### Bootstrap (manual once) and activation

The D1 journal is initialized **only by an explicit operator action**. Open
the GitHub Actions workflow `Private Saxo SIM EURUSD Daily cycle (isolated
D1 Free)`, select `Run workflow → main → operation: initialize`.
This creates one empty genesis state and never rewrites an existing state.
The private cycle is still disabled while `SAXO_PRIVATE_AUTO_ENABLED=false`.

After successful initialization and review, set the same GitHub variable to
`true`, then choose `Run workflow → operation: run` once. Look for
`RESULT=PRIVATE_SAXO_D1_DAILY_CYCLE_PASS` and
`PUBLIC_DAILY_ARTIFACTS_UNMODIFIED=TRUE`. Confirm a second scheduled run,
private decision history durability and first actual simulated open and close
before claiming complete operational integration.

Every run:
1. Fetch authoritative Belief Core / Epistemic state just like public Daily,
   with enforced no-retroactive-execution flags.
2. Restore the D1 snapshot into a temporary directory outside Git checkout,
   validating SHA-256 for each file and the whole compressed bundle.
3. Run existing v1.9 Belief/EPE admission and exit-only v1.8 lifecycle,
   priced privately from fresh Saxo SIM ASK/BID (and fresh Yahoo technical
   triggers) under `SAXO_PRIVATE_PRIMARY=1`.
4. Save the complete next state by a **single atomic SQL UPDATE with
   compare-and-swap** generation and checksum guards; verify readback.
   Missing/corrupt state, concurrent modifications, missing credentials,
   stale quotes and D1 Free limit errors fail closed without fabricating a
   close or resetting the trading journal.
5. Print only bounded status codes, never private quotes, credentials, Saxo
   tokens, trade fills, prices or P/L to logs/artifacts or public pages.

Disable immediately by setting `SAXO_PRIVATE_AUTO_ENABLED=false`;
the private D1 history is preserved. A five-minute GitHub Actions schedule
is best effort and **cannot** replace the fast five-second exit monitor or
provide real SL execution during outages. No claim of broker execution.
