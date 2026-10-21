import test from "node:test";
import assert from "node:assert/strict";
import webpush from "web-push";
import { PushHub, DELIVERY_STATUS, githubJson, providerRetryDelayMs, runScheduledRecovery } from "../src/index.js";

class Storage {
  constructor() { this.map = new Map(); this.alarmAt = null; }
  async get(key) { return this.map.get(key); }
  async put(key, value) { this.map.set(key, structuredClone(value)); }
  async delete(key) { this.map.delete(key); }
  async list({ prefix = "" } = {}) {
    return new Map([...this.map].filter(([key]) => key.startsWith(prefix)));
  }
  async setAlarm(at) { this.alarmAt = Number(at); }
  async deleteAlarm() { this.alarmAt = null; }
}

const sha = "a".repeat(40);
const parent = "b".repeat(40);
const event = {
  event_id: "transport-event", engine: "daily", event_type: "OPEN",
  position_id: "transport-position", instrument: "EUR/USD", direction: "LONG",
  entry: 1.12, opened_at: "2026-10-21T09:00:00Z",
};

async function fixture() {
  const storage = new Storage();
  await storage.put("sub:phone", {
    subscription: { endpoint: "https://push.example/phone" }, language: "pl",
    preferences: { channels: { daily: true }, events: { open: true } },
  });
  return { storage, hub: new PushHub({ storage }, { PUBLIC_BASE_URL: "https://push.example" }) };
}

function commitFetch(calls, { status = 200, noTransition = false } = {}) {
  return async (url, options = {}) => {
    const target = new URL(url);
    calls.push({ url: String(url), authorization: options.headers?.authorization });
    if (target.hostname === "api.github.com") {
      if (status !== 200) return new Response("{}", { status });
      return Response.json({ sha, parents: [{ sha: parent }] });
    }
    assert.equal(target.hostname, "raw.githubusercontent.com");
    const isTarget = target.pathname.includes(`/${sha}/`);
    return Response.json({
      timestamp: event.opened_at,
      metadata: { position: isTarget && !noTransition ? {
        trade_id: event.position_id, status: "OPEN", direction: event.direction,
        opened_at: event.opened_at, entry: event.entry, stop: 1.11, target: 1.138,
      } : null },
    });
  };
}

test("concurrent commit/recovery dispatches claim a recipient once before provider I/O", async () => {
  const { hub, storage } = await fixture();
  const original = webpush.sendNotification;
  let release;
  let started;
  const providerStarted = new Promise((resolve) => { started = resolve; });
  const providerRelease = new Promise((resolve) => { release = resolve; });
  let sends = 0;
  webpush.sendNotification = async () => {
    sends += 1; started(); await providerRelease; return { statusCode: 201 };
  };
  try {
    const first = hub.dispatchEvents([event]);
    await providerStarted;
    const second = hub.dispatchEvents([{ ...event, source: "recovery" }]);
    const retry = hub.deliverPending();
    release();
    await Promise.all([first, second, retry]);
    assert.equal(sends, 1);
    assert.equal((await storage.get("delivery:transport-event:phone")).attempts, 1);
    assert.equal(storage.alarmAt, null);
  } finally { webpush.sendNotification = original; }
});

test("explicit provider rejection gets a durable alarm honoring Retry-After, accepted push never replays", async () => {
  const { hub, storage } = await fixture();
  const original = webpush.sendNotification;
  let sends = 0;
  webpush.sendNotification = async (_subscription, _payload, options) => {
    assert.equal(options.urgency, "high");
    assert.equal(options.timeout, 10_000);
    sends += 1;
    if (sends === 1) throw Object.assign(new Error("limited"), { statusCode: 429, headers: { "retry-after": "120" } });
    return { statusCode: 201 };
  };
  try {
    const before = Date.now();
    const queued = await hub.dispatchEvents([event]);
    assert.equal(queued.durable_accepted, true);
    assert.equal(queued.pending, 1);
    assert.ok(Date.parse(queued.retry_scheduled_at) >= before + 120_000);
    assert.equal(storage.alarmAt, Date.parse(queued.retry_scheduled_at));
    const key = "delivery:transport-event:phone";
    const record = await storage.get(key);
    await storage.put(key, { ...record, next_retry_at: new Date(Date.now() - 1).toISOString() });
    await hub.alarm();
    assert.equal((await storage.get(key)).status, DELIVERY_STATUS.SENT_TO_PUSH);
    assert.equal((await storage.get(key)).attempts, 2);
    assert.equal(storage.alarmAt, null);
    await hub.alarm();
    assert.equal(sends, 2);
  } finally { webpush.sendNotification = original; }
});

test("Retry-After HTTP date never shortens the exponential retry delay", () => {
  const now = Date.parse("2026-10-21T09:00:00Z");
  assert.equal(providerRetryDelayMs({ headers: { "retry-after": "Wed, 21 Oct 2026 09:04:00 GMT" } }, 1, now), 240_000);
  assert.equal(providerRetryDelayMs({ headers: { "retry-after": "1" } }, 3, now), 8_000);
});

test("provider 403 does not erase a registered device and ambiguous failure cannot retry", async () => {
  for (const status of [403, 0]) {
    const { hub, storage } = await fixture();
    const original = webpush.sendNotification;
    let sends = 0;
    webpush.sendNotification = async () => { sends += 1; throw Object.assign(new Error("rejected"), { statusCode: status }); };
    try {
      const result = await hub.dispatchEvents([event]);
      assert.equal(result.ok, false);
      assert.equal(result.failed, 1);
      assert.equal(result.terminal_failed, 1);
      assert.equal(result.durable_accepted, false);
      await hub.alarm();
      assert.equal(sends, 1);
      assert.ok(await storage.get("sub:phone"));
      assert.equal((await storage.get("delivery:transport-event:phone")).status, DELIVERY_STATUS.EXPIRED);
      assert.equal(storage.alarmAt, null);
      const replay = await hub.dispatchEvents([event]);
      assert.equal(replay.ok, false);
      assert.equal(replay.durable_accepted, false);
      assert.equal((await storage.get("stats")).failed, 1);
      if (status) assert.equal((await storage.get("stats")).last_failed_statuses["403"], 1);
    } finally { webpush.sendNotification = original; }
  }
});

test("five definitive rejections exhaust the attempt budget and cannot turn into success on re-ingest", async () => {
  const { hub, storage } = await fixture();
  const original = webpush.sendNotification;
  let sends = 0;
  webpush.sendNotification = async () => { sends += 1; throw Object.assign(new Error("unavailable"), { statusCode: 503 }); };
  try {
    for (let attempt = 1; attempt <= 5; attempt += 1) {
      const result = await hub.dispatchEvents([event]);
      const key = "delivery:transport-event:phone";
      const record = await storage.get(key);
      assert.equal(record.attempts, attempt);
      assert.equal(result.durable_accepted, attempt < 5);
      if (attempt < 5) await storage.put(key, { ...record, next_retry_at: new Date(Date.now() - 1).toISOString() });
    }
    assert.equal(sends, 5);
    assert.equal(storage.alarmAt, null);
    assert.equal((await storage.get("delivery:transport-event:phone")).last_error, "retry_limit_exhausted");
    assert.equal((await hub.dispatchEvents([event])).durable_accepted, false);
    assert.equal(sends, 5);
  } finally { webpush.sendNotification = original; }
});

test("Retry-After beyond the ten-minute window schedules expiry and cannot send a stale queued alert", async () => {
  const { hub, storage } = await fixture();
  const original = webpush.sendNotification;
  let sends = 0;
  webpush.sendNotification = async () => { sends += 1; throw Object.assign(new Error("limited"), { statusCode: 429, headers: { "retry-after": "3600" } }); };
  try {
    const result = await hub.dispatchEvents([event]);
    const key = "delivery:transport-event:phone";
    const record = await storage.get(key);
    assert.equal(Date.parse(result.retry_scheduled_at), Date.parse(record.created_at) + 600_000);
    await storage.put(key, { ...record, created_at: new Date(Date.now() - 600_001).toISOString() });
    await hub.alarm();
    assert.equal(sends, 1);
    assert.equal((await storage.get(key)).last_error, "delivery_window_expired");
    assert.equal(storage.alarmAt, null);
    assert.equal((await hub.dispatchEvents([event])).durable_accepted, false);
  } finally { webpush.sendNotification = original; }
});

test("request-scoped GitHub token authenticates fixed-repo API only and never enters durable storage", async () => {
  const { hub, storage } = await fixture();
  const originalFetch = globalThis.fetch;
  const originalPush = webpush.sendNotification;
  const calls = [];
  const token = "ghs_fake_request_token";
  globalThis.fetch = commitFetch(calls);
  webpush.sendNotification = async () => ({ statusCode: 201 });
  try {
    const response = await hub.fetch(new Request("https://push.example/sync-trading", {
      method: "POST", headers: { authorization: `Bearer ${token}` },
      body: JSON.stringify({ sha, engines: ["daily"] }),
    }));
    const result = await response.json();
    assert.equal(response.status, 200);
    assert.equal(result.sha, sha);
    assert.deepEqual(result.engines, ["daily"]);
    assert.equal(result.durable_accepted, true);
    for (const call of calls) {
      assert.equal(call.authorization, new URL(call.url).hostname === "api.github.com" ? `Bearer ${token}` : undefined);
    }
    const saved = [...(await storage.list({ prefix: "event:" })).values()][0].event;
    assert.equal(saved.source_commit_sha, sha);
    assert.equal(saved.source_parent_sha, parent);
    assert.equal(JSON.stringify([...storage.map]).includes(token), false);
    assert.equal((await storage.get("stats")).last_commit_sync_authenticated, true);
  } finally { globalThis.fetch = originalFetch; webpush.sendNotification = originalPush; }
});

test("verified durable transient rejection returns 202 with an exact commit receipt and scheduled retry", async () => {
  const { hub } = await fixture();
  const originalFetch = globalThis.fetch;
  const originalPush = webpush.sendNotification;
  globalThis.fetch = commitFetch([]);
  webpush.sendNotification = async () => { throw Object.assign(new Error("unavailable"), { statusCode: 503 }); };
  try {
    const response = await hub.fetch(new Request("https://push.example/sync-trading", {
      method: "POST", body: JSON.stringify({ sha, engines: ["daily"] }),
    }));
    const result = await response.json();
    assert.equal(response.status, 202);
    assert.equal(result.sha, sha);
    assert.equal(result.durable_accepted, true);
    assert.equal(result.dispatch.pending, 1);
    assert.ok(Number.isFinite(Date.parse(result.retry_scheduled_at)));
  } finally { globalThis.fetch = originalFetch; webpush.sendNotification = originalPush; }
});

test("verified NO_TRANSITION is an exact durable receipt without a retry alarm", async () => {
  const { hub, storage } = await fixture();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = commitFetch([], { noTransition: true });
  try {
    const result = await hub.syncTradingCommit(sha, ["daily"]);
    assert.equal(result.durable_accepted, true);
    assert.equal(result.event_count, 0);
    assert.equal(result.retry_scheduled_at, null);
    assert.equal(storage.alarmAt, null);
  } finally { globalThis.fetch = originalFetch; }
});

test("supplied GitHub token failure fails closed rather than using unauthenticated raw fallback", async () => {
  const { hub, storage } = await fixture();
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = commitFetch(calls, { status: 403 });
  try {
    const response = await hub.fetch(new Request("https://push.example/sync-trading", {
      method: "POST", headers: { authorization: "Bearer ghs_invalid" },
      body: JSON.stringify({ sha, engines: ["daily"] }),
    }));
    assert.equal(response.status, 403);
    assert.equal((await response.json()).error_code, "GITHUB_HTTP_403");
    assert.ok(calls.every((call) => new URL(call.url).hostname === "api.github.com"));
    assert.equal((await storage.list({ prefix: "event:" })).size, 0);
  } finally { globalThis.fetch = originalFetch; }
});

test("parallel request tokens cannot cross-contaminate API calls and are rejected for other origins/repos", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, authorization: options.headers.authorization });
    await Promise.resolve();
    return Response.json({ sha });
  };
  try {
    const url = "https://api.github.com/repos/Maja2711/briefrooms-site/commits/main";
    await Promise.all([
      githubJson(`${url}?request=a`, { githubToken: "token_a" }),
      githubJson(`${url}?request=b`, { githubToken: "token_b" }),
    ]);
    assert.equal(calls.find((call) => call.url.endsWith("request=a")).authorization, "Bearer token_a");
    assert.equal(calls.find((call) => call.url.endsWith("request=b")).authorization, "Bearer token_b");
    await assert.rejects(githubJson("https://example.com/", { githubToken: "never_send" }), /target_not_allowed/);
    await assert.rejects(githubJson("https://api.github.com/repos/other/repo/commits/main", { githubToken: "never_send" }), /target_not_allowed/);
    assert.equal(calls.length, 2);
  } finally { globalThis.fetch = originalFetch; }
});

test("both recovery feeds can fail while durable retry still runs immediately", async () => {
  const originalFetch = globalThis.fetch;
  const paths = [];
  globalThis.fetch = async () => new Response("unavailable", { status: 503 });
  const stub = { fetch: async (url) => {
    paths.push(new URL(url).pathname);
    return Response.json({ ok: true });
  } };
  try {
    await assert.rejects(runScheduledRecovery({
      PUSH_HUB: { idFromName: () => "global", get: () => stub },
      EVENT_FEED_URL: "https://example.test/events",
      WES_EVENT_FEED_URL: "https://example.test/wes",
    }), /event_feed.*wes_feed/);
    assert.ok(paths.includes("/retry-pending"));
    assert.ok(paths.includes("/fast-daily-watch"));
  } finally { globalThis.fetch = originalFetch; }
});
