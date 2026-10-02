import webpush from "web-push";

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8" };

function json(value, status = 200, extra = {}) {
  return new Response(JSON.stringify(value), { status, headers: { ...JSON_HEADERS, ...extra } });
}

function allowedOrigin(request, env) {
  const origin = request.headers.get("origin") || "";
  const allowed = String(env.ALLOWED_ORIGINS || "").split(",").map((x) => x.trim()).filter(Boolean);
  if (!origin || allowed.includes(origin)) return origin;
  return null;
}

function cors(origin) {
  return {
    "access-control-allow-origin": origin || "https://briefrooms.com",
    "access-control-allow-methods": "GET,POST,DELETE,OPTIONS",
    "access-control-allow-headers": "content-type,authorization",
    "access-control-max-age": "86400",
    "vary": "Origin",
  };
}

async function bodyJson(request) {
  try { return await request.json(); } catch { return {}; }
}

async function endpointId(endpoint) {
  const bytes = new TextEncoder().encode(String(endpoint || ""));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function eventId(engine, eventType, positionId) {
  const raw = `${engine}|${eventType}|${positionId}`;
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(raw));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 24);
}

function finiteNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function openDailyPosition(payload) {
  const position = payload?.metadata?.position;
  if (!position || String(position.status || "").toUpperCase() !== "OPEN") return null;
  const direction = String(position.direction || "").toUpperCase();
  if (!["LONG", "SHORT"].includes(direction)) return null;
  if (finiteNumber(position.entry) == null || finiteNumber(position.stop) == null || finiteNumber(position.target) == null) return null;
  return position;
}

function parseCsvLine(line) {
  const out = [];
  let current = "";
  let quoted = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (ch === '"') {
      if (quoted && line[i + 1] === '"') { current += '"'; i += 1; }
      else quoted = !quoted;
    } else if (ch === "," && !quoted) {
      out.push(current);
      current = "";
    } else {
      current += ch;
    }
  }
  out.push(current);
  return out;
}

function parseStooqQuoteCsv(text) {
  const lines = String(text || "").trim().split(/\r?\n/).filter(Boolean);
  if (lines.length < 2) throw new Error("stooq_eurusd_empty");
  const headers = parseCsvLine(lines[0]).map((x) => String(x || "").trim().toLowerCase());
  const values = parseCsvLine(lines[1]);
  const row = {};
  headers.forEach((key, i) => { row[key] = String(values[i] ?? "").trim(); });
  if (Object.values(row).some((v) => ["N/D", "N/A"].includes(String(v).toUpperCase()))) {
    throw new Error("stooq_eurusd_unavailable");
  }
  const bid = finiteNumber(row.bid);
  const ask = finiteNumber(row.ask);
  const close = finiteNumber(row.close ?? row.last ?? row.kurs);
  const high = finiteNumber(row.high ?? row.max);
  const low = finiteNumber(row.low ?? row.min);
  const open = finiteNumber(row.open);
  const price = bid != null && ask != null && ask >= bid ? (bid + ask) / 2 : close;
  if (price == null) throw new Error("stooq_eurusd_no_price");
  return {
    source: "Stooq EURUSD live",
    symbol: row.symbol || row.symbol_pl || "EURUSD",
    source_date: row.date || row.data || null,
    source_time: row.time || row.czas || null,
    fetched_at: new Date().toISOString(),
    price,
    bid,
    ask,
    open,
    high,
    low,
    close,
  };
}

async function fetchStooqEurusd(env) {
  const configured = String(env.STOOQ_EURUSD_URL || "").trim();
  const candidates = configured
    ? [configured]
    : [
        "https://stooq.com/q/l/?s=eurusd&f=sd2t2ohlcvba&h&e=csv",
        "https://stooq.com/q/l/?s=eurusd&f=sd2t2ohlcv&h&e=csv",
        "https://stooq.pl/q/l/?s=eurusd&f=sd2t2ohlcvba&h&e=csv",
        "https://stooq.pl/q/l/?s=eurusd&f=sd2t2ohlcv&h&e=csv",
      ];
  const errors = [];
  for (const base of candidates) {
    try {
      const response = await fetch(`${base}${base.includes("?") ? "&" : "?"}_=${Date.now()}`, {
        headers: {
          "cache-control": "no-cache",
          "accept": "text/csv,text/plain,*/*",
          "user-agent": "Mozilla/5.0 (compatible; BriefRooms/1.0; +https://briefrooms.com)",
        },
      });
      if (!response.ok) throw new Error(`http_${response.status}`);
      const quote = parseStooqQuoteCsv(await response.text());
      return { ...quote, endpoint: base.includes("stooq.pl") ? "stooq.pl" : "stooq.com" };
    } catch (error) {
      errors.push(`${base.includes("stooq.pl") ? "pl" : "com"}:${String(error?.message || error)}`);
    }
  }
  throw new Error(`stooq_eurusd_all_candidates_failed:${errors.join("|")}`);
}

function fastExitHitQuote(position, quote) {
  const direction = String(position.direction || "").toUpperCase();
  const stop = finiteNumber(position.stop);
  const target = finiteNumber(position.target);
  if (!["LONG", "SHORT"].includes(direction) || stop == null || target == null) return null;

  const bid = finiteNumber(quote?.bid);
  const ask = finiteNumber(quote?.ask);
  const mid = finiteNumber(quote?.price);
  const execution = position.execution_price_engine || {};
  const halfPips = finiteNumber(execution.synthetic_half_spread_pips) || 0;
  const halfPrice = halfPips * 0.0001;
  const executable = direction === "LONG"
    ? (bid ?? (mid == null ? null : mid - halfPrice))
    : (ask ?? (mid == null ? null : mid + halfPrice));
  if (executable == null) return null;

  const stopHit = direction === "LONG" ? executable <= stop : executable >= stop;
  const targetHit = direction === "LONG" ? executable >= target : executable <= target;
  if (stopHit && targetHit) return { exit_reason: "STOP_LOSS", exit_price: stop, bar_timestamp: quote.fetched_at, conservative_same_bar: true };
  if (stopHit) return { exit_reason: "STOP_LOSS", exit_price: stop, bar_timestamp: quote.fetched_at, conservative_same_bar: false };
  if (targetHit) return { exit_reason: "TAKE_PROFIT", exit_price: target, bar_timestamp: quote.fetched_at, conservative_same_bar: false };
  return null;
}

function yahooMinuteBars(payload) {
  const result = payload?.chart?.result?.[0];
  const timestamps = Array.isArray(result?.timestamp) ? result.timestamp : [];
  const quote = result?.indicators?.quote?.[0] || {};
  const rows = [];
  for (let i = 0; i < timestamps.length; i += 1) {
    const close = finiteNumber(quote.close?.[i]);
    if (close == null) continue;
    const epoch = Number(timestamps[i]);
    if (!Number.isFinite(epoch)) continue;
    rows.push({
      timestamp: new Date(epoch * 1000).toISOString(),
      open: finiteNumber(quote.open?.[i]),
      high: finiteNumber(quote.high?.[i]),
      low: finiteNumber(quote.low?.[i]),
      close,
    });
  }
  return rows;
}

async function fetchYahooMinuteBars(env) {
  const configured = String(env.YAHOO_EURUSD_1M_URL || "").trim();
  const direct1 = configured || "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD%3DX?range=1d&interval=1m&includePrePost=false&events=div%2Csplits";
  const direct2 = "https://query2.finance.yahoo.com/v8/finance/chart/EURUSD%3DX?range=1d&interval=1m&includePrePost=false&events=div%2Csplits";
  const candidates = [
    { url: direct1, label: "Yahoo Finance query1 EURUSD=X 1m OHLC" },
    { url: direct2, label: "Yahoo Finance query2 EURUSD=X 1m OHLC" },
    { url: `https://proxy.cors.dev/${direct1}`, label: "Yahoo Finance EURUSD=X 1m OHLC via cors.dev" },
    { url: `https://api.allorigins.win/raw?url=${encodeURIComponent(direct1)}`, label: "Yahoo Finance EURUSD=X 1m OHLC via AllOrigins" },
  ].filter((candidate, index, values) =>
    candidate.url && values.findIndex((item) => item.url === candidate.url) === index
  );

  const errors = [];
  for (const candidate of candidates) {
    try {
      const response = await fetch(`${candidate.url}${candidate.url.includes("?") ? "&" : "?"}_=${Date.now()}`, {
        headers: {
          "cache-control": "no-cache",
          "accept": "application/json",
          "user-agent": "Mozilla/5.0 (compatible; BriefRooms/1.0; +https://briefrooms.com)",
        },
      });
      if (!response.ok) throw new Error(`http_${response.status}`);
      const bars = yahooMinuteBars(await response.json());
      if (!bars.length) throw new Error("no_bars");
      const latest = bars[bars.length - 1];
      const latestMs = Date.parse(String(latest.timestamp || ""));
      if (!Number.isFinite(latestMs)) throw new Error("invalid_latest_timestamp");
      const ageMs = Date.now() - latestMs;
      if (ageMs < -60_000 || ageMs > 6 * 60_000) throw new Error(`stale_latest_${Math.round(ageMs / 1000)}s`);
      return { bars, source: candidate.label, latest };
    } catch (error) {
      errors.push(`${candidate.label}:${String(error?.message || error)}`);
    }
  }
  throw new Error(`yahoo_eurusd_all_candidates_failed:${errors.join("|")}`);
}


function fastExitHit(position, bars) {
  const direction = String(position.direction || "").toUpperCase();
  const stop = finiteNumber(position.stop);
  const target = finiteNumber(position.target);
  const openedMs = Date.parse(String(position.opened_at || ""));
  if (!["LONG", "SHORT"].includes(direction) || stop == null || target == null || !Number.isFinite(openedMs)) return null;

  const execution = position.execution_price_engine || {};
  const halfPips = finiteNumber(execution.synthetic_half_spread_pips) || 0;
  const halfPrice = halfPips * 0.0001;
  const shift = direction === "LONG" ? -halfPrice : halfPrice;

  for (const bar of bars) {
    const ts = Date.parse(String(bar.timestamp || ""));
    if (!Number.isFinite(ts) || ts < openedMs) continue;
    const close = finiteNumber(bar.close);
    if (close == null) continue;
    const high = (finiteNumber(bar.high) ?? close) + shift;
    const low = (finiteNumber(bar.low) ?? close) + shift;
    const stopHit = direction === "LONG" ? low <= stop : high >= stop;
    const targetHit = direction === "LONG" ? high >= target : low <= target;

    if (stopHit && targetHit) {
      return { exit_reason: "STOP_LOSS", exit_price: stop, bar_timestamp: bar.timestamp, conservative_same_bar: true };
    }
    if (stopHit) return { exit_reason: "STOP_LOSS", exit_price: stop, bar_timestamp: bar.timestamp, conservative_same_bar: false };
    if (targetHit) return { exit_reason: "TAKE_PROFIT", exit_price: target, bar_timestamp: bar.timestamp, conservative_same_bar: false };
  }
  return null;
}

function normalizedPrefs(input = {}) {
  const channels = input.channels || {};
  const events = input.events || {};
  return {
    channels: {
      daily: channels.daily !== false,
      weekly: channels.weekly !== false,
      stock: channels.stock !== false,
    },
    events: {
      open: events.open !== false,
      close: events.close !== false,
    },
  };
}

function accepts(record, event) {
  const engine = String(event.engine || "").toLowerCase();
  const type = String(event.event_type || "").toLowerCase();
  const prefs = record.preferences || normalizedPrefs();
  return Boolean(prefs.channels?.[engine] && prefs.events?.[type]);
}

function notificationUrl(event, lang) {
  const pl = String(lang || "pl").toLowerCase().startsWith("pl");
  if (event.engine === "weekly") return pl ? "/pl/inwestycje/pozycje-tygodniowe.html" : "/en/investing/open-weekly-positions.html";
  if (event.engine === "stock") return pl ? "/pl/inwestycje/stock-trading.html" : "/en/investing/stock-trading.html";
  return pl ? "/pl/inwestycje/daily-trading.html" : "/en/investing/daily-trading.html";
}

function testNotificationPayload(lang) {
  const pl = String(lang || "pl").toLowerCase().startsWith("pl");
  return JSON.stringify({
    title: "BriefRooms · test",
    body: pl ? "Powiadomienia tradingowe działają na tym urządzeniu." : "Trading notifications work on this device.",
    event_id: `test:${Date.now()}`,
    url: pl ? "/pl/inwestycje/daily-trading.html" : "/en/investing/daily-trading.html",
    data: { test: true },
  });
}

function notificationPayload(event, lang, publicBaseUrl = "") {
  const pl = String(lang || "pl").toLowerCase().startsWith("pl");
  const engine = event.engine === "daily" ? "Daily Trading" : event.engine === "weekly" ? "Weekly Trading" : "Stock Trading";
  const reason = String(event.exit_reason || "").toUpperCase();
  let action = event.event_type === "OPEN" ? (pl ? "OTWARTO" : "OPENED") : (pl ? "ZAMKNIĘTO" : "CLOSED");
  if (event.event_type === "CLOSE" && reason === "TAKE_PROFIT") action = pl ? "TP OSIĄGNIĘTY" : "TAKE PROFIT";
  if (event.event_type === "CLOSE" && reason === "STOP_LOSS") action = "STOP LOSS";
  const direction = event.direction ? ` · ${event.direction}` : "";
  const priceValue = event.event_type === "CLOSE" && event.exit_price != null ? event.exit_price : event.entry;
  const price = priceValue != null ? ` @ ${priceValue}` : "";
  return JSON.stringify({
    title: `BriefRooms · ${engine}`,
    body: `${action} · ${event.instrument || ""}${direction}${price}`,
    event_id: event.event_id,
    url: notificationUrl(event, lang),
    data: {
      engine: event.engine,
      event_type: event.event_type,
      position_id: event.position_id,
      exit_reason: event.exit_reason || null,
      analytics_url: `${String(publicBaseUrl || "").replace(/\/$/, "")}/analytics/click`,
    },
  });
}

export class PushHub {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
  }

  async dispatchEvents(events, { seedIfUninitialized = false } = {}) {
    const initialized = Boolean(await this.ctx.storage.get("feed_initialized"));
    if (!initialized && seedIfUninitialized) {
      for (const event of events) {
        if (!event?.event_id) continue;
        await this.ctx.storage.put(`event-complete:${event.event_id}`, true);
      }
      await this.ctx.storage.put("feed_initialized", true);
      await this.ctx.storage.put("delivery_v2_initialized", true);
      return { ok: true, seeded: events.length, sent: 0, failed: 0, expired: 0, pending: 0 };
    }
    if (!initialized) await this.ctx.storage.put("feed_initialized", true);

    // One-time migration: legacy seen:* keys represented globally consumed events.
    // Convert them to event-complete:* once, then never consult seen:* again.
    if (!await this.ctx.storage.get("delivery_v2_initialized")) {
      const legacySeen = await this.ctx.storage.list({ prefix: "seen:" });
      for (const [key] of legacySeen.entries()) {
        const eventIdValue = key.slice("seen:".length);
        if (eventIdValue) await this.ctx.storage.put(`event-complete:${eventIdValue}`, true);
      }
      await this.ctx.storage.put("delivery_v2_initialized", true);
    }

    let sent = 0;
    let failed = 0;
    let expired = 0;
    let pending = 0;
    const failedStatuses = {};
    const subscriptions = await this.ctx.storage.list({ prefix: "sub:" });

    if (this.env.VAPID_PUBLIC_KEY && this.env.VAPID_PRIVATE_KEY) {
      webpush.setVapidDetails(
        this.env.VAPID_SUBJECT || "https://briefrooms.com",
        this.env.VAPID_PUBLIC_KEY,
        this.env.VAPID_PRIVATE_KEY,
      );
    }

    for (const event of events) {
      if (!event?.event_id) continue;
      const eventCompleteKey = `event-complete:${event.event_id}`;
      if (await this.ctx.storage.get(eventCompleteKey)) continue;

      let eventFailed = 0;
      let eventEligible = 0;

      for (const [key, record] of subscriptions.entries()) {
        if (!accepts(record, event)) continue;
        eventEligible += 1;

        const subId = key.startsWith("sub:") ? key.slice(4) : key;
        const deliveredKey = `delivered:${event.event_id}:${subId}`;
        if (await this.ctx.storage.get(deliveredKey)) continue;

        try {
          await webpush.sendNotification(
            record.subscription,
            notificationPayload(event, record.language, this.env.PUBLIC_BASE_URL),
            { TTL: 300 },
          );
          await this.ctx.storage.put(deliveredKey, true);
          sent += 1;
        } catch (error) {
          const status = Number(error?.statusCode || 0);
          if (status === 403 || status === 404 || status === 410) {
            await this.ctx.storage.delete(key);
            await this.ctx.storage.put(deliveredKey, true);
            expired += 1;
          } else {
            failed += 1;
            eventFailed += 1;
            const statusKey = String(status || "unknown");
            failedStatuses[statusKey] = Number(failedStatuses[statusKey] || 0) + 1;
          }
        }
      }

      if (eventFailed === 0) {
        await this.ctx.storage.put(eventCompleteKey, true);
      } else {
        pending += 1;
      }

    }

    const stats = (await this.ctx.storage.get("stats")) || {};
    stats.sent = Number(stats.sent || 0) + sent;
    stats.failed = Number(stats.failed || 0) + failed;
    stats.expired_removed = Number(stats.expired_removed || 0) + expired;
    stats.active_subscriptions = (await this.ctx.storage.list({ prefix: "sub:" })).size;
    stats.last_failed_statuses = failedStatuses;
    stats.last_dispatch_sent = sent;
    stats.last_dispatch_failed = failed;
    stats.last_dispatch_expired = expired;
    stats.last_dispatch_pending = pending;
    stats.last_dispatch_at = new Date().toISOString();
    await this.ctx.storage.put("stats", stats);
    return { ok: failed === 0, sent, failed, expired, pending };
  }

  async recordFastDailyError(error) {
    const stats = (await this.ctx.storage.get("stats")) || {};
    stats.fast_daily_failures = Number(stats.fast_daily_failures || 0) + 1;
    stats.last_fast_daily_error_at = new Date().toISOString();
    stats.last_fast_daily_error = String(error?.message || error || "unknown").slice(0, 500);
    stats.last_fast_daily_status = "ERROR";
    await this.ctx.storage.put("stats", stats);
  }

  async fastDailyWatch() {
    const stateUrls = [
      this.env.DAILY_STATE_URL || "https://raw.githubusercontent.com/Maja2711/briefrooms-site/main/data/investments/eurusd_daily_spot.json",
      "https://briefrooms.com/data/investments/eurusd_daily_spot.json",
    ].filter((value, index, values) => value && values.indexOf(value) === index);

    let state = null;
    const stateErrors = [];
    for (const stateUrl of stateUrls) {
      try {
        const stateResponse = await fetch(`${stateUrl}${stateUrl.includes("?") ? "&" : "?"}_=${Date.now()}`, {
          headers: { "cache-control": "no-cache", "accept": "application/json" },
        });
        if (!stateResponse.ok) throw new Error(`http_${stateResponse.status}`);
        state = await stateResponse.json();
        break;
      } catch (error) {
        stateErrors.push(`${stateUrl}:${String(error?.message || error)}`);
      }
    }
    if (!state) throw new Error(`daily_state_all_sources_failed:${stateErrors.join("|")}`);
    const position = openDailyPosition(state);

    const stats = (await this.ctx.storage.get("stats")) || {};
    stats.fast_daily_checks = Number(stats.fast_daily_checks || 0) + 1;
    stats.last_fast_daily_check_at = new Date().toISOString();

    if (!position) {
      stats.last_fast_daily_status = "FLAT";
      await this.ctx.storage.put("stats", stats);
      return { ok: true, status: "FLAT" };
    }

    const positionId = String(position.trade_id || `daily:${position.opened_at}:${position.direction}`);
    const openEvent = {
      event_id: await eventId("daily", "OPEN", positionId),
      engine: "daily",
      event_type: "OPEN",
      position_id: positionId,
      instrument: "EUR/USD",
      market: null,
      direction: String(position.direction || "").toUpperCase(),
      entry: finiteNumber(position.entry),
      opened_at: position.opened_at || null,
      observed_at: new Date().toISOString(),
      source: "cloudflare_fast_daily_watcher",
    };
    const openDispatch = await this.dispatchEvents([openEvent]);

    let hit = null;
    const marketSources = [];
    const marketErrors = [];

    // Canonical SL/TP authority: Yahoo EURUSD=X 1-minute OHLC.
    // The complete bar history since position open is checked every cycle, so a
    // brief touch cannot disappear simply because the current quote later reverted.
    try {
      const yahoo = await fetchYahooMinuteBars(this.env);
      marketSources.push(yahoo.source);
      hit = fastExitHit(position, yahoo.bars);
      stats.last_fast_daily_quote = {
        source: yahoo.source,
        fetched_at: yahoo.latest.timestamp,
        bid: null,
        ask: null,
        price: yahoo.latest.close,
        high: yahoo.latest.high,
        low: yahoo.latest.low,
      };
    } catch (error) {
      marketErrors.push(`yahoo:${String(error?.message || error)}`);
    }

    // Fallback #1: current Stooq point. It may confirm a cross immediately, but
    // does not replace 1m OHLC as the canonical no-cross evidence.
    if (!hit) {
      try {
        const stooqQuote = await fetchStooqEurusd(this.env);
        marketSources.push(stooqQuote.source);
        hit = fastExitHitQuote(position, stooqQuote);
        if (!stats.last_fast_daily_quote) {
          stats.last_fast_daily_quote = {
            source: stooqQuote.source,
            fetched_at: stooqQuote.fetched_at,
            bid: stooqQuote.bid,
            ask: stooqQuote.ask,
            price: stooqQuote.price,
          };
        }
      } catch (error) {
        marketErrors.push(`stooq:${String(error?.message || error)}`);
      }
    }

    // Fallback #2: current fxapi point. This can confirm a current cross only.
    if (!hit) {
      try {
        const fxUrl = this.env.FXAPI_EURUSD_URL || "https://fxapi.app/api/EUR/USD.json";
        const response = await fetch(`${fxUrl}${fxUrl.includes("?") ? "&" : "?"}_=${Date.now()}`, {
          headers: { "cache-control": "no-cache", "accept": "application/json" },
        });
        if (!response.ok) throw new Error(`fxapi_http_${response.status}`);
        const quote = await response.json();
        const rate = finiteNumber(quote?.rate);
        if (rate == null) throw new Error("fxapi_no_rate");
        const current = {
          source: "fxapi.app EUR/USD spot",
          fetched_at: new Date().toISOString(),
          price: rate,
          bid: null,
          ask: null,
        };
        marketSources.push(current.source);
        hit = fastExitHitQuote(position, current);
        if (!stats.last_fast_daily_quote) {
          stats.last_fast_daily_quote = current;
        }
      } catch (error) {
        marketErrors.push(`fxapi:${String(error?.message || error)}`);
      }
    }


    const marketSource = marketSources.join(" + ") || "none";
    stats.last_fast_daily_market_errors = marketErrors;
    if (!hit) {
      stats.last_fast_daily_status = "OPEN_NO_EXIT";
      stats.last_fast_daily_market_source = marketSource;
      await this.ctx.storage.put("stats", stats);
      return { ok: true, status: "OPEN_NO_EXIT", open_dispatch: openDispatch };
    }

    const closeEvent = {
      event_id: await eventId("daily", "CLOSE", positionId),
      engine: "daily",
      event_type: "CLOSE",
      position_id: positionId,
      instrument: "EUR/USD",
      market: null,
      direction: String(position.direction || "").toUpperCase(),
      entry: finiteNumber(position.entry),
      opened_at: position.opened_at || null,
      observed_at: hit.bar_timestamp || new Date().toISOString(),
      exit_reason: hit.exit_reason,
      exit_price: hit.exit_price,
      conservative_same_bar: Boolean(hit.conservative_same_bar),
      source: "cloudflare_fast_exit_watcher",
      market_source: marketSource,
    };
    const closeDispatch = await this.dispatchEvents([closeEvent]);
    stats.fast_daily_exit_hits = Number(stats.fast_daily_exit_hits || 0) + 1;
    stats.last_fast_daily_status = hit.exit_reason;
    stats.last_fast_daily_exit_at = hit.bar_timestamp || new Date().toISOString();
    stats.last_fast_daily_market_source = marketSource;
    await this.ctx.storage.put("stats", stats);
    return { ok: true, status: hit.exit_reason, open_dispatch: openDispatch, close_dispatch: closeDispatch };
  }

  async fetch(request) {
    const url = new URL(request.url);
    const path = url.pathname;
    const origin = allowedOrigin(request, this.env);
    if (request.method === "OPTIONS") {
      if (origin === null) return new Response(null, { status: 403 });
      return new Response(null, { status: 204, headers: cors(origin) });
    }

    if (path === "/vapid-public-key" && request.method === "GET") {
      return json({ publicKey: this.env.VAPID_PUBLIC_KEY || null }, 200, cors(origin));
    }

    if (path === "/health" && request.method === "GET") {
      const subscriptions = await this.ctx.storage.list({ prefix: "sub:" });
      const stats = (await this.ctx.storage.get("stats")) || {};
      return json({
        ok: true,
        ready: Boolean(this.env.VAPID_PUBLIC_KEY && this.env.VAPID_PRIVATE_KEY),
        active_subscriptions: subscriptions.size,
        sent: Number(stats.sent || 0),
        failed: Number(stats.failed || 0),
        expired_removed: Number(stats.expired_removed || 0),
        test_sent: Number(stats.test_sent || 0),
        test_failed: Number(stats.test_failed || 0),
        last_test_failed_status: stats.last_test_failed_status || null,
        last_failed_statuses: stats.last_failed_statuses || {},
        last_dispatch_sent: Number(stats.last_dispatch_sent || 0),
        last_dispatch_failed: Number(stats.last_dispatch_failed || 0),
        last_dispatch_expired: Number(stats.last_dispatch_expired || 0),
        last_dispatch_pending: Number(stats.last_dispatch_pending || 0),
        last_dispatch_at: stats.last_dispatch_at || null,
        fast_daily_watcher: true,
        fast_daily_failures: Number(stats.fast_daily_failures || 0),
        last_fast_daily_error_at: stats.last_fast_daily_error_at || null,
        last_fast_daily_error: stats.last_fast_daily_error || null,
        last_fast_daily_check_at: stats.last_fast_daily_check_at || null,
        last_fast_daily_status: stats.last_fast_daily_status || null,
        last_fast_daily_market_source: stats.last_fast_daily_market_source || null,
        last_fast_daily_market_errors: stats.last_fast_daily_market_errors || [],
        last_fast_daily_quote: stats.last_fast_daily_quote || null,
      }, 200, cors(origin));
    }

    if (url.hostname === "internal" && path === "/fast-daily-watch" && request.method === "POST") {
      try {
        return json(await this.fastDailyWatch());
      } catch (error) {
        await this.recordFastDailyError(error);
        return json({ ok: false, error: String(error?.message || error) }, 500);
      }
    }

    if (path === "/market/eurusd" && request.method === "GET") {
      const errors = [];
      try {
        const yahoo = await fetchYahooMinuteBars(this.env);
        const latest = yahoo.latest;
        return json({
          ok: true,
          source: yahoo.source,
          price: latest.close,
          open: latest.open,
          high: latest.high,
          low: latest.low,
          close: latest.close,
          timestamp: latest.timestamp,
          ohlc_1m: true,
        }, 200, cors(origin));
      } catch (error) {
        errors.push(`yahoo:${String(error?.message || error)}`);
      }

      try {
        const quote = await fetchStooqEurusd(this.env);
        return json({ ok: true, ...quote, ohlc_1m: false, fallback: true, errors }, 200, cors(origin));
      } catch (error) {
        errors.push(`stooq:${String(error?.message || error)}`);
      }

      try {
        const fxUrl = this.env.FXAPI_EURUSD_URL || "https://fxapi.app/api/EUR/USD.json";
        const response = await fetch(`${fxUrl}${fxUrl.includes("?") ? "&" : "?"}_=${Date.now()}`, {
          headers: { "cache-control": "no-cache", "accept": "application/json" },
        });
        if (!response.ok) throw new Error(`http_${response.status}`);
        const payload = await response.json();
        const price = finiteNumber(payload?.rate);
        if (price == null) throw new Error("no_rate");
        return json({
          ok: true,
          source: "fxapi.app EUR/USD spot fallback",
          price,
          timestamp: payload?.timestamp || new Date().toISOString(),
          ohlc_1m: false,
          fallback: true,
          errors,
        }, 200, cors(origin));
      } catch (error) {
        errors.push(`fxapi:${String(error?.message || error)}`);
      }

      return json({ ok: false, error: "eurusd_all_sources_failed", errors }, 502, cors(origin));
    }

    if (origin === null) return json({ error: "origin_not_allowed" }, 403);

    if (path === "/subscription-status" && request.method === "POST") {
      const payload = await bodyJson(request);
      if (!payload.endpoint) return json({ error: "endpoint_required" }, 400, cors(origin));
      const id = await endpointId(payload.endpoint);
      const record = await this.ctx.storage.get(`sub:${id}`);
      return json({
        ok: true,
        id,
        registered: Boolean(record?.subscription),
        updated_at: record?.updated_at || null,
      }, 200, cors(origin));
    }

    if (path === "/subscribe" && request.method === "POST") {
      const payload = await bodyJson(request);
      const subscription = payload.subscription;
      if (!subscription?.endpoint || !subscription?.keys?.p256dh || !subscription?.keys?.auth) {
        return json({ error: "invalid_subscription" }, 400, cors(origin));
      }
      const id = await endpointId(subscription.endpoint);
      const key = `sub:${id}`;
      const existing = await this.ctx.storage.get(key);
      const now = new Date().toISOString();
      const record = {
        subscription,
        preferences: normalizedPrefs(payload.preferences),
        language: String(payload.language || "pl").slice(0, 5),
        created_at: existing?.created_at || now,
        updated_at: now,
      };
      await this.ctx.storage.put(key, record);
      const stats = (await this.ctx.storage.get("stats")) || {};
      if (!existing) stats.subscribed_total = Number(stats.subscribed_total || 0) + 1;
      stats.active_subscriptions = (await this.ctx.storage.list({ prefix: "sub:" })).size;
      await this.ctx.storage.put("stats", stats);
      return json({ ok: true, id }, 200, cors(origin));
    }

    if (path === "/test-subscription" && request.method === "POST") {
      const payload = await bodyJson(request);
      if (!payload.endpoint) return json({ error: "endpoint_required" }, 400, cors(origin));
      const id = await endpointId(payload.endpoint);
      const key = `sub:${id}`;
      const record = await this.ctx.storage.get(key);
      if (!record?.subscription) return json({ error: "subscription_not_registered" }, 404, cors(origin));

      const rateKey = `test-rate:${id}`;
      const now = Date.now();
      const last = Number((await this.ctx.storage.get(rateKey)) || 0);
      if (now - last < 10_000) return json({ error: "test_rate_limited" }, 429, cors(origin));
      await this.ctx.storage.put(rateKey, now);

      if (!this.env.VAPID_PUBLIC_KEY || !this.env.VAPID_PRIVATE_KEY) {
        return json({ error: "vapid_not_ready" }, 503, cors(origin));
      }
      webpush.setVapidDetails(
        this.env.VAPID_SUBJECT || "https://briefrooms.com",
        this.env.VAPID_PUBLIC_KEY,
        this.env.VAPID_PRIVATE_KEY,
      );
      try {
        await webpush.sendNotification(
          record.subscription,
          testNotificationPayload(record.language),
          { TTL: 60 },
        );
        const stats = (await this.ctx.storage.get("stats")) || {};
        stats.test_sent = Number(stats.test_sent || 0) + 1;
        stats.last_test_sent_at = new Date().toISOString();
        await this.ctx.storage.put("stats", stats);
        return json({ ok: true, id }, 200, cors(origin));
      } catch (error) {
        const status = Number(error?.statusCode || 0);
        const stats = (await this.ctx.storage.get("stats")) || {};
        stats.test_failed = Number(stats.test_failed || 0) + 1;
        stats.last_test_failed_at = new Date().toISOString();
        stats.last_test_failed_status = status || null;
        await this.ctx.storage.put("stats", stats);
        if (status === 404 || status === 410) await this.ctx.storage.delete(key);
        return json({ error: "test_push_failed", status: status || null }, 502, cors(origin));
      }
    }

    if (path === "/unsubscribe" && request.method === "DELETE") {
      const payload = await bodyJson(request);
      if (!payload.endpoint) return json({ error: "endpoint_required" }, 400, cors(origin));
      const id = await endpointId(payload.endpoint);
      const key = `sub:${id}`;
      const existed = await this.ctx.storage.get(key);
      await this.ctx.storage.delete(key);
      const stats = (await this.ctx.storage.get("stats")) || {};
      if (existed) stats.unsubscribed_total = Number(stats.unsubscribed_total || 0) + 1;
      stats.active_subscriptions = (await this.ctx.storage.list({ prefix: "sub:" })).size;
      await this.ctx.storage.put("stats", stats);
      return json({ ok: true }, 200, cors(origin));
    }

    if (path === "/analytics/click" && request.method === "POST") {
      const payload = await bodyJson(request);
      const stats = (await this.ctx.storage.get("stats")) || {};
      stats.clicked = Number(stats.clicked || 0) + 1;
      if (payload.event_id) {
        const k = `click:${String(payload.event_id).slice(0, 80)}`;
        await this.ctx.storage.put(k, Number((await this.ctx.storage.get(k)) || 0) + 1);
      }
      await this.ctx.storage.put("stats", stats);
      return json({ ok: true }, 200, cors(origin));
    }

    if (path === "/analytics/public" && request.method === "GET") {
      const stats = (await this.ctx.storage.get("stats")) || {};
      const subs = await this.ctx.storage.list({ prefix: "sub:" });
      const breakdown = { daily: 0, weekly: 0, stock: 0, open: 0, close: 0 };
      for (const record of subs.values()) {
        const p = record.preferences || {};
        if (p.channels?.daily) breakdown.daily += 1;
        if (p.channels?.weekly) breakdown.weekly += 1;
        if (p.channels?.stock) breakdown.stock += 1;
        if (p.events?.open) breakdown.open += 1;
        if (p.events?.close) breakdown.close += 1;
      }
      const sent = Number(stats.sent || 0);
      const clicked = Number(stats.clicked || 0);
      return json({
        ok: true,
        active_subscriptions: subs.size,
        sent,
        clicked,
        ctr_percent: sent > 0 ? Number(((clicked / sent) * 100).toFixed(2)) : 0,
        failed: Number(stats.failed || 0),
        last_dispatch_sent: Number(stats.last_dispatch_sent || 0),
        last_dispatch_failed: Number(stats.last_dispatch_failed || 0),
        last_dispatch_expired: Number(stats.last_dispatch_expired || 0),
        last_failed_statuses: stats.last_failed_statuses || {},
        expired_removed: Number(stats.expired_removed || 0),
        breakdown,
        last_dispatch_at: stats.last_dispatch_at || null,
      }, 200, cors(origin));
    }

    if (path === "/analytics" && request.method === "GET") {
      if (!this.env.ADMIN_TOKEN || request.headers.get("authorization") !== `Bearer ${this.env.ADMIN_TOKEN}`) {
        return json({ error: "unauthorized" }, 401, cors(origin));
      }
      const stats = (await this.ctx.storage.get("stats")) || {};
      const subs = await this.ctx.storage.list({ prefix: "sub:" });
      const breakdown = { daily: 0, weekly: 0, stock: 0, open: 0, close: 0 };
      for (const record of subs.values()) {
        const p = record.preferences || {};
        if (p.channels?.daily) breakdown.daily += 1;
        if (p.channels?.weekly) breakdown.weekly += 1;
        if (p.channels?.stock) breakdown.stock += 1;
        if (p.events?.open) breakdown.open += 1;
        if (p.events?.close) breakdown.close += 1;
      }
      return json({ ...stats, active_subscriptions: subs.size, breakdown }, 200, cors(origin));
    }

    if (path === "/ingest" && request.method === "POST") {
      const payload = await bodyJson(request);
      const events = Array.isArray(payload.events) ? payload.events : [];
      return json(await this.dispatchEvents(events, { seedIfUninitialized: true }), 200, cors(origin));
    }

    return json({ error: "not_found" }, 404, cors(origin));
  }
}

async function hub(env) {
  const id = env.PUSH_HUB.idFromName("global");
  return env.PUSH_HUB.get(id);
}

export default {
  async fetch(request, env) {
    const h = await hub(env);
    return h.fetch(request);
  },

  async scheduled(_controller, env, ctx) {
    ctx.waitUntil((async () => {
      const h = await hub(env);

      let fastWatchOk = false;
      let lastFastError = null;
      for (let attempt = 1; attempt <= 3; attempt += 1) {
        try {
          const response = await h.fetch("https://internal/fast-daily-watch", { method: "POST" });
          if (!response.ok) {
            const payload = await response.text();
            throw new Error(`fast_daily_watch_http_${response.status}:${payload.slice(0, 300)}`);
          }
          fastWatchOk = true;
          break;
        } catch (error) {
          lastFastError = error;
          if (attempt < 3) await new Promise((resolve) => setTimeout(resolve, attempt * 750));
        }
      }

      const response = await fetch(env.EVENT_FEED_URL, {
        headers: { "cache-control": "no-cache", "accept": "application/json" },
      });
      if (!response.ok) throw new Error(`event_feed_http_${response.status}`);
      const payload = await response.json();
      const ingestResponse = await h.fetch("https://internal/ingest", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ events: Array.isArray(payload.events) ? payload.events : [] }),
      });
      if (!ingestResponse.ok) {
        throw new Error(`event_ingest_http_${ingestResponse.status}`);
      }

      if (!fastWatchOk) {
        throw lastFastError || new Error("fast_daily_watch_failed_after_retries");
      }
    })());
  }
};
