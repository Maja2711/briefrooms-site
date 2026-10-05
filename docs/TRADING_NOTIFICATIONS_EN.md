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

For Weekly, the mechanism covers all three WES instruments — EUR/USD, BTC/USD and S&P 500 futures — whether the write comes from WES admission/lifecycle, full maintenance, or the canonical five-minute risk-exit path. For Stock Trading it covers both v2 production admission and canonical portfolio lifecycle. Daily uses the same shared mechanism, including its realtime exit watcher.

Web Push language is stored per device subscription: a PL device receives Polish copy and a PL destination, while an EN device receives English copy and an EN destination. Event IDs remain deterministic from `engine + event_type + position_id`, so a parallel recovery path cannot duplicate an already delivered transition.

Public direct `/ingest` is disabled. Internal `https://internal/ingest` is available only to the Worker scheduler as a recovery mechanism that reads canonical `data/notifications/trading-events.json`; polling is no longer the primary alert-delivery path. Recovery initialization remains seed-only, so historical events are not emitted.

A push-backend failure cannot affect TR-03/TR-04/TR-05 or any execution/risk/decision path.

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
