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
7. Until a separate private scheduler/persistent store is configured, no continuously running private Saxo executor is guaranteed. Existing public schedule cannot write private Saxo entry/exit prices to GitHub history. A successful merge is not proof of a live cycle.

## Required private authentication

The Cloudflare Worker `briefrooms-saxo-eurusd` has working Saxo SIM OAuth and its own D1 token storage. The Python adapter currently expects HTTP Basic (`SAXO_PRIVATE_BASIC_USER`, `SAXO_PRIVATE_BASIC_PASSWORD` = password set as Worker's `SAXO_ADMIN_PASSWORD`). Browser access may be authorized by a cookie instead of Basic; this needs a separate real machine-to-machine test **before** enabling automatic trading simulation. Never paste API client secrets or the Saxo account password into chat, code or public Actions logs.

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
