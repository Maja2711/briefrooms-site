# EURUSD Daily — native Saxo SIM streaming, no 5-minute market-data polling

## What it does

This isolated Cloudflare **Durable Object** uses the Saxo **trade/v1/prices/subscriptions**
EURUSD `FxSpot` UIC 21 **WebSocket push stream**. One connection receives
price updates whenever Saxo publishes them; there is no REST polling loop for
market quotations. A five-second Durable Object alarm checks connection
health/re-authorizes/reconnects if needed. **The alarm cadence is NOT the market
quote arrival cadence.** Bid and ask each retain their own tick-arrival time;
a heartbeat or empty delta cannot refresh stale prices.

A Basic-authenticated `/saxo/quote` returns the latest private indicative
BID/ASK only when both sides are <=15 seconds old. Missing, delayed, invalid
or disconnected quotes return 503, never a synthetic MID-based fill.

This is **read-only paper SIM market data** and never sends broker orders.
It does not automatically become an executable brokerage quote. The public
BriefRooms website, Weekly/BTC/S&P, and the canonical Daily history remain
unchanged. Private Saxo quotations and P/L must not be published.

## Deployment prerequisites and owner-controlled activation

This is a **new isolated Worker** named `briefrooms-saxo-eurusd-stream`. It
does not modify/replace the existing `briefrooms-saxo-eurusd` OAuth Worker.
The existing OAuth refresh token must NOT be reused: refresh tokens rotate,
and simultaneous independent consumers of one refresh token will disconnect.

Provision these repository secrets under GitHub Actions (do not paste them
in a chat, public repository, log, or workflow dispatch input):

- `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` (existing)
- `SAXO_PRIVATE_BASIC_PASSWORD` (existing private worker reader password)
- `SAXO_STREAM_APP_KEY`, `SAXO_STREAM_APP_SECRET` (dedicated Saxo SIM app)
- `SAXO_STREAM_REFRESH_TOKEN` (dedicated OAuth authorization/session with
  **Personal: Subscribe** rights for price streaming)

The workflow `saxo-eurusd-native-stream.yml` runs parser/transport checks
on PR and main. It deploys the Worker and injects secrets on main **only
if all required credentials exist**. If not, it explicitly reports
`SAXO_STREAM_DEPLOY=DISABLED_MISSING_DEDICATED_OAUTH_CREDS`. A successful
GitHub merge/CI never proves the stream is connected.

After the private authenticated `/saxo/status` shows connected and subscribed
and `/saxo/quote` returns fresh quote sides, set repository variables:

- `SAXO_STREAM_PRIMARY=1`
- `SAXO_PRIVATE_STREAM_URL=https://briefrooms-saxo-eurusd-stream.<account-subdomain>.workers.dev/saxo/quote`

These variables are consumed by the **private Daily D1** cycle and private
Weekly observer. When `SAXO_STREAM_PRIMARY=1`, the Python EPE adapter
does **not** fall back to the old REST Worker on missing/stale/invalid
streaming price. Entry and exit remain bound to their strict <=15s checks.
Until those variables are enabled, the existing private REST source remains
in use.

The standalone private **Daily decision/lifecycle** remains on a 5-minute
scheduler. This change makes its quote source continuous, but it **does
not** make the Python Daily decision/exit loop run on every tick. Moving
the position exit authority to a permanent private execution service is
separate work requiring an atomic single-writer position journal and a
certified simulation policy. Do not claim five-second SL/TP execution.

## Transport and continuity

- Distinct `ContextId` per WebSocket connection; subscribing with
  `ReferenceId=EURUSD_SPOT_21`, `RefreshRate=1000` request.
- SAXO binary data messages use 64-bit little-endian message IDs, reference
  IDs and length-prefixed JSON. Multiple updates per websocket message are
  handled. Deltas received before the initial snapshot are buffered in order.
- Only price-side deltas refresh that side's timestamp. `_heartbeat` and
  unrelated metadata do not. Price marked `OldIndicative`, delayed quotes,
  malformed frames or a closed socket cannot be used.
- On disconnect, mark data unavailable and re-establish a fresh context and
  subscription. Do not replay a stale price as a new quote.
- Dedicated OAuth refresh token rotates in private Durable Object storage.
  The existing WebSocket session is explicitly re-authorized using the new
  access token. OAuth/permission errors fail closed; no fallback to MID.
- Avoid snapshot DB writes for every tick. Current prices live only in
  Durable Object memory and become unavailable after a restart until a
  validated Saxo subscription snapshot is received.

## Acceptance evidence still required

Before claiming production-live streaming, verify from the deployed runtime:
1. Private authenticated `/saxo/status` connected + subscribed.
2. A sequence of changing private BID/ASK ticks, with bounded measured
   received-to-consumer latency and a fresh `/saxo/quote` contract.
3. Token refresh and reconnect including a forced disconnect; stale data
   always blocks.
4. A private Daily entry/exit evaluated using streamed side quotes and
   immutable D1 history (no invented broker execution).
5. Neither prices nor tokens nor Saxo-derived trade P/L appear in public Git
   artifacts, notifications or browser-facing endpoints.

Official Saxo guide:
https://www.developer.saxo/openapi/learn/plain-websocket-streaming

Cloudflare Durable Object WebSocket:
https://developers.cloudflare.com/durable-objects/best-practices/websockets/
