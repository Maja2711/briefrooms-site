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

function notificationPayload(event, lang) {
  const pl = String(lang || "pl").toLowerCase().startsWith("pl");
  const engine = event.engine === "daily" ? "Daily Trading" : event.engine === "weekly" ? "Weekly Trading" : "Stock Trading";
  const action = event.event_type === "OPEN" ? (pl ? "OTWARTO" : "OPENED") : (pl ? "ZAMKNIĘTO" : "CLOSED");
  const direction = event.direction ? ` · ${event.direction}` : "";
  const entry = event.entry != null ? ` @ ${event.entry}` : "";
  return JSON.stringify({
    title: `BriefRooms · ${engine}`,
    body: `${action} · ${event.instrument || ""}${direction}${entry}`,
    event_id: event.event_id,
    url: notificationUrl(event, lang),
    data: { engine: event.engine, event_type: event.event_type, position_id: event.position_id },
  });
}

export class PushHub {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
  }

  async fetch(request) {
    const url = new URL(request.url);
    const origin = allowedOrigin(request, this.env);
    if (request.method === "OPTIONS") {
      if (origin === null) return new Response(null, { status: 403 });
      return new Response(null, { status: 204, headers: cors(origin) });
    }

    if (url.pathname === "/vapid-public-key" && request.method === "GET") {
      return json({ publicKey: this.env.VAPID_PUBLIC_KEY || null }, 200, cors(origin));
    }

    if (url.pathname === "/health" && request.method === "GET") {
      const subscriptions = await this.ctx.storage.list({ prefix: "sub:" });
      return json({
        ok: true,
        ready: Boolean(this.env.VAPID_PUBLIC_KEY && this.env.VAPID_PRIVATE_KEY),
        active_subscriptions: subscriptions.size,
      }, 200, cors(origin));
    }

    if (origin === null) return json({ error: "origin_not_allowed" }, 403);

    if (url.pathname === "/subscribe" && request.method === "POST") {
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

    if (url.pathname === "/unsubscribe" && request.method === "DELETE") {
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

    if (url.pathname === "/analytics/click" && request.method === "POST") {
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

    if (url.pathname === "/analytics" && request.method === "GET") {
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

    if (url.pathname === "/ingest" && request.method === "POST") {
      const payload = await bodyJson(request);
      const events = Array.isArray(payload.events) ? payload.events : [];
      const initialized = Boolean(await this.ctx.storage.get("feed_initialized"));
      if (!initialized) {
        for (const event of events) if (event?.event_id) await this.ctx.storage.put(`seen:${event.event_id}`, true);
        await this.ctx.storage.put("feed_initialized", true);
        return json({ ok: true, seeded: events.length, sent: 0 });
      }

      let sent = 0;
      let failed = 0;
      let expired = 0;
      const subscriptions = await this.ctx.storage.list({ prefix: "sub:" });
      if (this.env.VAPID_PUBLIC_KEY && this.env.VAPID_PRIVATE_KEY) {
        webpush.setVapidDetails(this.env.VAPID_SUBJECT || "https://briefrooms.com", this.env.VAPID_PUBLIC_KEY, this.env.VAPID_PRIVATE_KEY);
      }

      for (const event of events) {
        if (!event?.event_id) continue;
        const seenKey = `seen:${event.event_id}`;
        if (await this.ctx.storage.get(seenKey)) continue;
        await this.ctx.storage.put(seenKey, true);
        for (const [key, record] of subscriptions.entries()) {
          if (!accepts(record, event)) continue;
          try {
            await webpush.sendNotification(record.subscription, notificationPayload(event, record.language), { TTL: 300 });
            sent += 1;
          } catch (error) {
            const status = Number(error?.statusCode || 0);
            if (status === 404 || status === 410) {
              await this.ctx.storage.delete(key);
              expired += 1;
            } else {
              failed += 1;
            }
          }
        }
      }

      const stats = (await this.ctx.storage.get("stats")) || {};
      stats.sent = Number(stats.sent || 0) + sent;
      stats.failed = Number(stats.failed || 0) + failed;
      stats.expired_removed = Number(stats.expired_removed || 0) + expired;
      stats.active_subscriptions = (await this.ctx.storage.list({ prefix: "sub:" })).size;
      stats.last_dispatch_at = new Date().toISOString();
      await this.ctx.storage.put("stats", stats);
      return json({ ok: true, sent, failed, expired });
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
      const response = await fetch(env.EVENT_FEED_URL, { headers: { "cache-control": "no-cache" } });
      if (!response.ok) throw new Error(`event_feed_http_${response.status}`);
      const payload = await response.json();
      const h = await hub(env);
      await h.fetch("https://internal/ingest", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ events: Array.isArray(payload.events) ? payload.events : [] }),
      });
    })());
  },
};
