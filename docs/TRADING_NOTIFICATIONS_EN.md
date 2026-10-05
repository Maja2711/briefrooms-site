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

For Daily EUR/USD the production path is immediate and bound to a specific persisted state: after a successful OPEN/CLOSE write, the workflow calls `/sync-daily` and sends only the commit SHA. The Worker verifies through the GitHub API that the SHA is the current `main` head, fetches `eurusd_daily_spot.json` from that exact commit, builds the deterministic event, and immediately sends Web Push. This keeps the notification layer from guessing market state or waiting for the next poll. Public direct `/ingest` is disabled. Minute polling of the canonical `data/notifications/trading-events.json` feed remains a recovery mechanism for all channels. Initial startup is seed-only, so historical events are not sent.

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
