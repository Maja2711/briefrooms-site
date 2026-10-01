# BriefRooms Trading Notifications

## Status

MVP v1. This layer is a read-only observer of persisted trading state. It has no authority to change decisions, positions, risk, entry prices, TP/SL or execution workflows.

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

## GitHub Pages limitation

GitHub Pages cannot safely store Web Push subscriptions or VAPID sending secrets. MVP v1 therefore provides:
1. per-device preferences,
2. system test notifications,
3. polling of the event feed while BriefRooms is open,
4. a service worker ready to receive real Web Push.

Full background push with the site closed and global Notification Analytics require a secure backend/serverless subscription store. Subscription endpoints and sender secrets must never be stored in the public repository.

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
