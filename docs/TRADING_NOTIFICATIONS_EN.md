# BriefRooms Trading Notifications

## Status

Background Web Push v2. This layer is a read-only observer of persisted trading state. It has no authority to change decisions, positions, risk, entry prices, TP/SL or execution workflows.

## User scope

Users can independently select:
- Daily Trading,
- Weekly Trading,
- Stock Trading,
- position opened,
- position closed.

The close reason is not a separate subscription. CLOSE means every real transition from OPEN to no longer active / CLOSED, regardless of TP, SL, time exit or an earlier thesis-driven exit.

## Notification copy and event clocks

Keep OPEN/CLOSE as the primary status in both languages, with LONG/SHORT for Daily/Weekly and generic position status for stocks. Never substitute entry price for a missing close price. Stock price return is descriptive price-only return, not net account return.

Through five minutes inclusive, neither background push nor foreground recovery adds a delay suffix. Older events retain `DELAYED … min` / `OPÓŹNIONE … min`. Original lifecycle, provider-acceptance and display timestamps remain distinct and unchanged in diagnostics. CLOSE recovery uses `closed_at`, falling back to `observed_at`, never the old opening time.

## Source of truth

Notifications only read persisted state after the authority-owning engine has written it:
- TR-03 Daily EUR/USD,
- TR-04 Stock Trading v2,
- TR-05 Weekly/WES.

The notification layer never invokes lifecycle logic and cannot create execution.

## Deduplication

The first run only seeds state and emits no historical alerts. Events are generated only from changes in active position IDs:
- absent -> OPEN = OPEN,
- OPEN -> absent = CLOSE.

Event IDs are deterministic from engine + event_type + position_id, so reruns cannot duplicate a transition.

## Access and future monetization

Configuration supports:
- PUBLIC,
- AUTHENTICATED,
- PAID,

globally and per channel. MVP starts in PUBLIC mode.

## Background Web Push

GitHub Pages remains the public frontend while an isolated Cloudflare Worker named `briefrooms-trading-push` performs push delivery. A Durable Object provides durable subscription storage.

The frontend registers `br-trading-sw.js`, fetches the public VAPID key from the Worker, creates a subscription through `PushManager`, and stores only the push endpoint, required Web Push key material, and Daily/Weekly/Stock + OPEN/CLOSE preferences.

The private VAPID key never reaches GitHub Pages or the public repository. It is generated and stored as a Cloudflare Worker Secret.

For every production channel, the push path is now immediate and commit-bound: **Daily EUR/USD, Weekly EUR/USD, Weekly BTC/USD, Weekly S&P 500 futures, and Stock Trading (GPW and US)**. Every canonical writer that successfully persists a position change to `main` sends only that exact commit SHA plus the channel name to the Worker through `/sync-trading`. The Worker verifies that the SHA is the current head or a recent ancestor of current `main`, fetches the exact state at that commit and its direct parent, and emits only real `absent -> OPEN` and `OPEN -> CLOSED` transitions. This prevents a simultaneous close of one position and opening of another from losing an alert, while ordinary mark-to-market updates do not create false notifications.

For Weekly, the mechanism covers all three WES instruments — EUR/USD, BTC/USD and S&P 500 futures — whether the write comes from WES admission/lifecycle, full maintenance, freshness recovery, the live-price/risk writer, Event Intelligence, or the canonical five-minute risk-exit path. For Stock Trading it covers v2 production admission, canonical portfolio lifecycle, and the Event Intelligence overlay. Daily uses the same shared mechanism, including its realtime exit watcher.

Web Push language is stored per device subscription: a PL device receives Polish copy and a PL destination, while an EN device receives English copy and an EN destination. Event IDs remain deterministic from `engine + event_type + position_id`, so a parallel recovery path cannot duplicate an already delivered transition.

Public direct `/ingest` is disabled. Internal `https://internal/ingest` is available only to the Worker scheduler as a recovery mechanism that reads canonical `data/notifications/trading-events.json`; polling is no longer the primary alert-delivery path. Recovery initialization remains seed-only, so historical events are not emitted.

A push-backend failure cannot affect TR-03/TR-04/TR-05 or any execution/risk/decision path.

## GitHub–Cloudflare handoff, retries and deployment

The writer calls `scripts/sync_trading_push_commit.sh` immediately after successful commit persistence. Requests have a 5s connection and 25s total deadline, with at most four handoff attempts. Receipts must reconcile exact SHA and requested channels. The script does not wait for device display or a separate website publishing workflow.

GitHub Actions forwards its short-lived token over HTTPS only to the owned Worker. The Worker uses it only within that request for GET of the fixed repository through GitHub's API, without redirects, token persistence or logging. This avoids anonymous API rate limits; unauthenticated fallback retains persisted-state verification. The credential adds no trading execution path to the notification layer.

Initial dispatch starts immediately. Explicit `429/5xx` rejections may enter the durable queue: a Durable Object alarm retries from 2s, honors `Retry-After`, and limits delivery to five attempts and ten minutes from creation. HTTP 202 means verified durable acceptance with a scheduled alarm, not proof of device display. Accepted or ambiguous sends are not replayed; provider 403 does not delete a subscription. Recovery-feed outages cannot block independent pending retries.

Recipient initialization and delivery are serialized. Device ACK requires completed `showNotification` or an existing notification; an IndexedDB `PENDING` reservation is not display proof. Provider acceptance is not device receipt, and missing ACK cannot trigger another audible alert.

Deployment checks read-only `/health` and `/inbox`, transport version `commit-bound-durable-v3`, and alarm capability. An additional authenticated `GET /verify-commit?sha=...` verifies Actions access to GitHub and the exact commit through Cloudflare; it performs no state writes, synchronization or delivery. Deployment neither replays production transitions as tests nor broadcasts fabricated alerts. Existing VAPID keys remain unchanged; a partial pair requires restoring the original key rather than invalidating every device. Notification code pushes validate trading writers only; position runtime remains scheduled or explicitly manual/governed.

A failed handoff stores a separate error time and allowlisted code while preserving the previous confirmed success. Actions logs show only the safe code, never raw responses or tokens. Publication parity also covers `br-trading-sw.js` and `scripts/trading-notifications.js`; all eight PL/EN trading pages use one current script URL version. Opening or refreshing the app allows the update to be downloaded; existing device subscriptions remain valid.

Submission to the push provider has no intentional wait for recovery polling. Actual display time also depends on network, Web Push provider and device OS; metrics never claim zero latency or confirmed delivery without ACK evidence.

## Target Notification Analytics

The backend should report at least:
- active_subscriptions,
- unique_users (once login exists),
- active_devices,
- Daily / Weekly / Stock opt-in,
- OPEN / CLOSE opt-in,
- new_7d,
- unsubscribed_7d,
- sent,
- delivered (when provider receipts exist),
- clicked,
- CTR.

Users and devices must remain separate metrics because one person can use multiple devices.
