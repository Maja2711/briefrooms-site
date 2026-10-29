// Saxo SIM EURUSD FXSpot UIC21 native WebSocket streaming.
// Private-only, indicative paper quotes. This Worker NEVER places orders,
// publishes quotes to public files, or asserts a broker execution.
const GATEWAY = "https://gateway.saxobank.com/sim/openapi";
// Saxo deprecated streaming.saxobank.com/sim/openapi/streamingws on 2025-12-06.
// Use the currently documented SIM streaming host for both connect and authorize.
const STREAM = "https://sim-streaming.saxobank.com/sim/oapi/streaming/ws";
const TOKEN_URL = "https://sim.logonvalidation.net/token";
const REF = "EURUSD_SPOT_21";
const MAX_FRAME = 65536;
const QUOTE_MAX_AGE = 15000;
const HEARTBEAT_POLL_MS = 5000;
const decoder = new TextDecoder();

const fail = (status) => new Response(JSON.stringify({status:"UNAVAILABLE"}), {
  status, headers: {"Content-Type":"application/json", "Cache-Control":"no-store"}
});
function validFloat(x) {
  return typeof x === "number" && Number.isFinite(x);
}
function copyPartial(existing, delta) {
  const next = {...(existing || {})};
  for (const [key, value] of Object.entries(delta || {})) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      next[key] = copyPartial(next[key], value);
    } else {
      next[key] = value;
    }
  }
  return next;
}
function validQuoteValues(bid, ask) {
  return validFloat(bid) && validFloat(ask) && bid > 0.8 && bid < ask
    && ask < 1.5 && ask - bid <= 0.0015;
}
export function parseSaxoBinaryMessages(data) {
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  if (bytes.byteLength > MAX_FRAME) throw Error("frame_exceeds_limit");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const messages = [];
  let pos = 0;
  while (pos < bytes.byteLength) {
    if (bytes.byteLength - pos < 16) throw Error("incomplete_header");
    const messageId = view.getBigUint64(pos, true).toString();
    const refSize = view.getUint8(pos + 10);
    const offset = pos + 11 + refSize;
    if (bytes.byteLength < offset + 5) throw Error("incomplete_reference");
    const referenceId = decoder.decode(bytes.subarray(pos + 11, offset));
    const format = view.getUint8(offset);
    const payloadSize = view.getUint32(offset + 1, true);
    const end = offset + 5 + payloadSize;
    if (payloadSize > MAX_FRAME || end > bytes.byteLength || format !== 0) {
      throw Error("unsupported_or_incomplete_payload");
    }
    const json = JSON.parse(decoder.decode(bytes.subarray(offset + 5, end)));
    messages.push({messageId, referenceId, payload:json});
    pos = end;
  }
  return messages;
}
export function projectQuote(previous, payload, receivedAt) {
  const source = payload?.Snapshot || payload?.Data || payload;
  if ((source?.Uic != null && source.Uic !== 21) ||
      (source?.AssetType != null && source.AssetType !== "FxSpot") ||
      source?.InstrumentPriceDetails?.IsMarketOpen === false) return null;
  const q = source?.Quote;
  if (!q || typeof q !== "object") return null;
  const merged = copyPartial(previous?.raw, q);
  if (merged.DelayedByMinutes !== 0 ||
      ["OldIndicative", "None", "NoMarket"].includes(merged.PriceTypeBid) ||
      ["OldIndicative", "None", "NoMarket"].includes(merged.PriceTypeAsk) ||
      (merged.ErrorCode && merged.ErrorCode !== "None") ||
      !validQuoteValues(merged.Bid, merged.Ask)) return null;
  // A changed price side is evidence of a new tick for that side. A heartbeat
  // or other metadata delta never refreshes a price timestamp.
  const bidAt = validFloat(q.Bid) ? receivedAt : previous?.bidAt;
  const askAt = validFloat(q.Ask) ? receivedAt : previous?.askAt;
  if (!Number.isFinite(bidAt) || !Number.isFinite(askAt)) return null;
  const sourceTimestamp = source.LastUpdated || source.Timestamp;
  const sourceAt = sourceTimestamp ? Date.parse(sourceTimestamp) :
    (validFloat(q.Bid) || validFloat(q.Ask) ? receivedAt : previous?.sourceAt);
  // The first subscription snapshot may describe an OLD quote even though
  // it was received just now. Validate its Saxo timestamp independently.
  if (!Number.isFinite(sourceAt) || receivedAt < sourceAt - 30000 ||
      receivedAt - sourceAt > QUOTE_MAX_AGE) return null;
  return {raw:merged, bidAt, askAt, sourceAt};
}
export function quoteFresh(quote, now) {
  return Boolean(quote && validQuoteValues(quote.raw?.Bid, quote.raw?.Ask)
    && now >= quote.bidAt && now >= quote.askAt
    && now - quote.bidAt <= QUOTE_MAX_AGE
    && now - quote.askAt <= QUOTE_MAX_AGE
    && now >= quote.sourceAt && now - quote.sourceAt <= QUOTE_MAX_AGE);
}
async function matchSecret(candidate, expected) {
  if (!candidate || !expected) return false;
  const a = new TextEncoder().encode(candidate);
  const b = new TextEncoder().encode(expected);
  if (a.length !== b.length) return false;
  const [ha,hb] = await Promise.all([
    crypto.subtle.digest("SHA-256", a), crypto.subtle.digest("SHA-256", b)
  ]);
  const x = new Uint8Array(ha), y = new Uint8Array(hb);
  let diff = 0;
  for (let i=0; i<x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "GET" ||
        !["/saxo/quote", "/saxo/status"].includes(url.pathname) ||
        url.search || !env.SAXO_STREAM_BASIC_PASSWORD) return fail(404);
    const header = request.headers.get("Authorization") || "";
    let pass = "";
    if (header.startsWith("Basic ")) {
      try {
        const decoded = atob(header.slice(6));
        if (decoded.startsWith("admin:")) pass = decoded.slice(6);
      } catch { /* generic unauthorized */ }
    }
    if (!await matchSecret(pass, env.SAXO_STREAM_BASIC_PASSWORD)) return fail(403);
    const stub = env.EURUSD_SAXO_STREAM.getByName("eurusd-sim-uic21");
    // Strip caller auth before invoking Durable Object; the edge gate is final.
    return stub.fetch(new Request("https://private.internal" + url.pathname));
  },
};
export class EurusdSaxoStream {
  constructor(ctx, env) {
    this.ctx = ctx; this.env = env;
    this.ws = null; this.contextId = null;
    this.quote = null; this.connected = false; this.subscribed = false;
    this.pendingDeltas = [];
    this.token = null; this.tokenExpiresAt = 0;
    this.connectionStarted = 0; this.connectionEpoch = 0;
    this.lastFrameAt = 0; this.retryAfter = 0; this.failures = 0;
    // Private, credential-free diagnostics: never retain OAuth values or quotes.
    this.phase = "starting"; this.lastFailure = "none";
    // Counters contain no quotes, credentials or session identifiers.
    this.frameCount=0; this.priceMessageCount=0;
    this.acceptedPriceCount=0; this.heartbeatCount=0;
    ctx.blockConcurrencyWhile(async()=> {
      // Preserve an existing alarm across Durable Object re-instantiation.
      if (await ctx.storage.getAlarm() === null)
        await ctx.storage.setAlarm(Date.now() + 1000);
    });
  }
  async fetch(request) {
    const now = Date.now(), path = new URL(request.url).pathname;
    if (path === "/saxo/status") {
      return new Response(JSON.stringify({
        connected:this.connected, subscribed:this.subscribed,
        fresh:quoteFresh(this.quote, now),
        environment:"SIM", instrument:"EUR/USD",
        trading_enabled:false, streaming:true,
        phase:this.phase, last_failure:this.lastFailure,
        diagnostics_revision:"saxo_price_telemetry_v1",
        quote_present:Boolean(this.quote),
        frames_received:this.frameCount,
        price_messages_received:this.priceMessageCount,
        prices_accepted:this.acceptedPriceCount,
        heartbeats_received:this.heartbeatCount,
        last_frame_age_seconds:this.lastFrameAt ? Math.floor((now-this.lastFrameAt)/1000) : null,
        bid_age_seconds:this.quote ? Math.floor((now-this.quote.bidAt)/1000) : null,
        ask_age_seconds:this.quote ? Math.floor((now-this.quote.askAt)/1000) : null,
        source_age_seconds:this.quote ? Math.floor((now-this.quote.sourceAt)/1000) : null
      }), {headers:{"Content-Type":"application/json","Cache-Control":"no-store"}});
    }
    if (path !== "/saxo/quote" || !this.connected || !this.subscribed ||
        !quoteFresh(this.quote, now)) return fail(503);
    const bid = this.quote.raw.Bid, ask = this.quote.raw.Ask;
    // last_updated is the OLDEST observed arrival among quote sides, not a
    // synthetic clock manufactured by the /quote HTTP request.
    const observed = Math.min(this.quote.bidAt, this.quote.askAt);
    return new Response(JSON.stringify({
      source:"SAXO_SIM",instrument:"EUR/USD",bid,ask,mid:(bid+ask)/2,
      last_updated:new Date(observed).toISOString(),
      timestamp_origin:"SAXO_STREAM_PRICE_SIDE_RECEIVED_AT",
      delayed_by_minutes:0, market_state:"open",
      redistributable:false,trading_enabled:false,
      price_type_bid:this.quote.raw.PriceTypeBid || null,
      price_type_ask:this.quote.raw.PriceTypeAsk || null
    }), {headers:{"Content-Type":"application/json","Cache-Control":"no-store"}});
  }
  async alarm() {
    try {
      // A dead TCP connection can remain open without price updates. The
      // Saxo stream normally emits heartbeats; silence must trigger recovery.
      if (this.connected && Date.now() - this.lastFrameAt > 60000) {
        this.lastFailure="websocket_idle"; this.reset();
      }
      if (this.connected && this.tokenExpiresAt < Date.now() + 90000) {
        const token = await this.refreshToken();
        const url = STREAM + "/authorize?contextid=" + encodeURIComponent(this.contextId);
        const response = await fetch(url, {method:"PUT",headers:{"Authorization":"Bearer " + token}});
        if (response.status !== 202) {
          this.lastFailure="websocket_reauthorize_http_" + response.status;
          this.reset();
        }
      }
      if (!this.connected && Date.now() >= this.retryAfter) await this.connect();
    } catch (error) {
      // Allow only fixed machine-readable error categories: no Saxo bodies,
      // token values, request URLs or exception messages in diagnostics.
      const category=String(error?.message || "");
      this.lastFailure=/^[a-z_]+(?:_http_[0-9]{3})?$/.test(category) && category.length < 80
        ? category : this.phase + "_exception";
      this.reset(); // credentials/stream error: never use an old price
      this.failures = Math.min(this.failures + 1, 6);
      this.retryAfter = Date.now() + Math.min(60000, 1000 * 2 ** this.failures);
    } finally {
      await this.ctx.storage.setAlarm(Date.now() + HEARTBEAT_POLL_MS);
    }
  }
  async refreshToken() {
    if (this.token && this.tokenExpiresAt > Date.now() + 90000) return this.token;
    this.phase="oauth_refresh";
    const stored = await this.ctx.storage.get("oauth_refresh_token");
    const configured = this.env.SAXO_STREAM_REFRESH_TOKEN;
    if ((!stored && !configured) || !this.env.SAXO_STREAM_APP_KEY || !this.env.SAXO_STREAM_APP_SECRET)
      throw Error("oauth_not_configured");
    const auth = btoa(this.env.SAXO_STREAM_APP_KEY + ":" + this.env.SAXO_STREAM_APP_SECRET);
    // Prefer the last successfully rotated token. If it is rejected as
    // invalid and the operator installed a DIFFERENT GitHub refresh token,
    // make one controlled recovery attempt with that new token.
    // Never retry configured (older) credentials when a stored token works.
    const candidates = stored ? [stored] : [configured];
    if (stored && configured && stored !== configured) candidates.push(configured);
    for (let i = 0; i < candidates.length; i++) {
      const body = new URLSearchParams({
        grant_type:"refresh_token",refresh_token:candidates[i]
      });
      const response = await fetch(TOKEN_URL, {
        method:"POST", headers:{"Authorization":"Basic " + auth,
          "Content-Type":"application/x-www-form-urlencoded"},body
      });
      if (!response.ok) {
        // OAuth invalid_grant/credentials: only a rotated token from storage
        // can fall back to a DIFFERENT freshly configured credential.
        if (i === 0 && stored && candidates.length > 1 &&
            (response.status === 400 || response.status === 401)) continue;
        throw Error("oauth_http_" + response.status);
      }
      const data = await response.json();
      if (!data.access_token || !data.refresh_token ||
          !Number.isFinite(data.expires_in) || data.expires_in < 120)
        throw Error("oauth_response_incomplete");
      // Persist every newly rotated refresh token BEFORE using the access token.
      await this.ctx.storage.put("oauth_refresh_token", data.refresh_token);
      this.token = data.access_token;
      this.tokenExpiresAt = Date.now() + data.expires_in * 1000;
      return this.token;
    }
    throw Error("oauth_refresh_failed");
  }
  reset() {
    this.connected = false; this.subscribed = false; this.quote = null;
    this.contextId = null;
    this.pendingDeltas = [];
    this.connectionEpoch += 1;
    const socket = this.ws; this.ws = null;
    try { if (socket) socket.close(1000,"reconnect"); } catch {}
  }
  async connect() {
    const token = await this.refreshToken();
    this.phase="websocket_connect";
    // Never reuse a stale context without a valid delta/resumption snapshot.
    const context = "BRSEURUSD" + crypto.randomUUID().replace(/-/g,"").slice(0,28);
    const url = STREAM + "/connect?contextId=" + encodeURIComponent(context);
    const response = await fetch(url, {
      headers:{"Upgrade":"websocket","Authorization":"Bearer "+token}
    });
    if (response.status !== 101 || !response.webSocket)
      throw Error("websocket_http_" + response.status);
    const socket = response.webSocket;
    socket.accept();
    const epoch = this.connectionEpoch;
    this.ws=socket;this.contextId=context;this.connected=true;
    this.subscribed=false;this.quote=null;this.pendingDeltas=[];
    this.connectionStarted=Date.now();this.lastFrameAt=Date.now();
    socket.addEventListener("message", event=>{
      if (epoch !== this.connectionEpoch) return;
      try {
        this.lastFrameAt=Date.now();this.frameCount++;
        if (typeof event.data === "string") throw Error("unexpected_text");
        const messages=parseSaxoBinaryMessages(event.data);
        for (const item of messages) this.onMessage(item);
      } catch { this.lastFailure="stream_frame_invalid"; this.reset(); }
    });
    for (const eventName of ["close","error"]) socket.addEventListener(eventName,()=>{
      if (epoch === this.connectionEpoch) {
        this.lastFailure="websocket_closed"; this.reset();
        this.ctx.storage.setAlarm(Date.now()+1000).catch(()=>{});
      }
    });
    this.phase="price_subscription";
    const subscription = await fetch(GATEWAY + "/trade/v1/prices/subscriptions",{
      method:"POST",
      headers:{"Authorization":"Bearer "+token,"Content-Type":"application/json"},
      body:JSON.stringify({
        Arguments:{Uic:21,AssetType:"FxSpot",FieldGroups:["Quote"]},
        ContextId:context,ReferenceId:REF,RefreshRate:1000,
        Format:"application/json"
      })
    });
    if (subscription.status !== 201) { this.reset(); throw Error("subscription_http_" + subscription.status); }
    const payload = await subscription.json();
    if (epoch !== this.connectionEpoch) return;
    this.phase="initial_quote";
    const initial=projectQuote(null,payload.Snapshot || payload,Date.now());
    if (!initial) { this.reset(); throw Error("initial_quote_invalid"); }
    this.quote=initial;this.acceptedPriceCount++;
    // Saxo can send deltas before the subscription snapshot response. Apply
    // them in arrival order; do not drop them or overwrite a newer side.
    for (const pending of this.pendingDeltas) {
      const next=projectQuote(this.quote,pending.payload,pending.receivedAt);
      if (next) {this.quote=next;this.acceptedPriceCount++;}
    }
    this.pendingDeltas=[];
    this.subscribed=true;
    this.phase="subscribed"; this.lastFailure="none";
    this.failures=0;this.retryAfter=0;
  }
  onMessage(message) {
    if (message.referenceId === REF) {
      this.priceMessageCount++;
      const receivedAt=Date.now();
      if (!this.subscribed) {
        if (this.pendingDeltas.length >= 64) { this.reset(); return; }
        this.pendingDeltas.push({payload:message.payload,receivedAt});
        return;
      }
      const next=projectQuote(this.quote,message.payload,receivedAt);
      if (next) {this.quote=next;this.acceptedPriceCount++;}
      else if (message.payload?.Quote || message.payload?.Data?.Quote) this.quote=null;
      if (message.payload?.Quote?.PriceTypeBid === "OldIndicative" ||
          message.payload?.Quote?.PriceTypeAsk === "OldIndicative" ||
          message.payload?.Quote?.DelayedByMinutes > 0) this.quote=null;
    } else if (message.referenceId === "_heartbeat") {
      this.heartbeatCount++;
    } else if (message.referenceId === "_resetsubscriptions" ||
               message.referenceId === "_disconnect" ||
               message.referenceId === "_subscriptiontemporarilydisabled") {
      this.reset();
    }
    // _heartbeat does NOT refresh BID/ASK timestamps.
  }
}
