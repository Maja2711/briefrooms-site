import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const NOW = Date.parse("2026-10-21T12:00:00Z");
class Clock extends Date {
  constructor(...args) { super(...(args.length ? args : [NOW])); }
  static now() { return NOW; }
}

// Minimal asynchronous IndexedDB adapter: exercise the actual add/constraint,
// read, write and delete callbacks used by both browser contexts.
function deviceDatabase() {
  const records = new Map();
  const db = {
    close() {},
    createObjectStore() {},
    transaction() {
      const tx = {};
      let pending = 0;
      const request = (operation) => {
        pending += 1;
        const req = {};
        queueMicrotask(() => {
          try {
            req.result = operation();
            req.onsuccess?.();
          } catch (error) {
            req.error = error;
            req.onerror?.({ preventDefault() {}, stopPropagation() {} });
          }
          pending -= 1;
          queueMicrotask(() => { if (!pending) tx.oncomplete?.(); });
        });
        return req;
      };
      tx.objectStore = () => ({
        add: (row) => request(() => {
          if (records.has(row.id)) throw Object.assign(new Error("duplicate"), { name: "ConstraintError" });
          records.set(row.id, { ...row });
        }),
        put: (row) => request(() => records.set(row.id, { ...row })),
        get: (id) => request(() => records.get(id)),
        delete: (id) => request(() => records.delete(id)),
      });
      return tx;
    },
  };
  return {
    records,
    open() {
      const req = {};
      queueMicrotask(() => { req.result = db; req.onsuccess?.(); });
      return req;
    },
  };
}

function pushPayload(ageMs = 0, language = "pl") {
  const original = new Date(NOW - ageMs).toISOString();
  return {
    title: "BriefRooms · Daily Trading", body: "ZAMKNIĘTY SHORT · EUR/USD @ 1,1248",
    event_id: "close-p1", event_at: original, sent_at: original, language,
    ack_url: "https://push.example/ack", ack_token: "token", delivery_id: "delivery-p1",
    data: { engine: "daily", event_type: "CLOSE", position_id: "p1" },
  };
}

function serviceWorker({ database = deviceDatabase(), show, existing = [] } = {}) {
  const shown = [];
  const acks = [];
  const handlers = {};
  const self = {
    indexedDB: database,
    addEventListener: (name, handler) => { handlers[name] = handler; },
    registration: {
      getNotifications: async () => existing,
      showNotification: async (title, options) => {
        if (show) await show(title, options);
        shown.push(options);
      },
    },
  };
  const context = { self, Date: Clock, console, setTimeout, fetch: async (url, options) => {
    acks.push({ url, body: JSON.parse(options.body) });
    return { ok: true };
  } };
  runInNewContext(readFileSync(new URL("../../../br-trading-sw.js", import.meta.url), "utf8"), context);
  return {
    context, shown, acks, database,
    push(payload) {
      let pending;
      handlers.push({ data: { json: () => payload }, waitUntil: (promise) => { pending = promise; } });
      return pending;
    },
  };
}

function foreground(language = "pl", { database = deviceDatabase(), events = [], show } = {}) {
  const local = new Map();
  const shown = [];
  const Notification = Object.assign(function () {}, { permission: "granted" });
  const context = {
    Date: Clock, Intl, console, structuredClone, indexedDB: database,
    document: { documentElement: { lang: language }, readyState: "loading", addEventListener() {} },
    window: { indexedDB: database, Notification, localStorage: true }, Notification,
    localStorage: { getItem: (key) => local.get(key) ?? null, setItem: (key, value) => local.set(key, value) },
    navigator: { serviceWorker: { getRegistration: async () => ({ showNotification: async (_title, options) => {
      if (show) await show(options);
      shown.push(options);
    } }) } },
    location: { href: "https://briefrooms.com/pl/inwestycje/daily-trading.html" },
    fetch: async () => ({ ok: true, json: async () => ({ events }) }),
  };
  let script = readFileSync(new URL("../../../scripts/trading-notifications.js", import.meta.url), "utf8");
  const bootstrap = '  if (document.readyState === "loading")';
  assert.ok(script.includes(bootstrap));
  // Expose private entry points only inside this test VM; mount stays dormant.
  script = script.replace(bootstrap, '  globalThis.browserTest = { showNative, pollEvents, notificationText };\n' + bootstrap);
  runInNewContext(script, context);
  return { ...context.browserTest, shown, local, database };
}

function closeEvent(ageMs = 0) {
  return {
    event_id: "close-p1", engine: "daily", event_type: "CLOSE", position_id: "p1",
    instrument: "EUR/USD", direction: "SHORT", entry: 1.12, exit_price: 1.1248,
    opened_at: "2026-10-01T10:00:00Z", closed_at: new Date(NOW - ageMs).toISOString(),
    observed_at: new Date(NOW - ageMs).toISOString(),
  };
}

for (const language of ["pl", "en"]) {
  for (const ageMs of [0, 2 * 60_000, 5 * 60_000, 5 * 60_000 + 1, 30 * 60_000]) {
    test(`${language}: push and foreground delay copy agree at ${ageMs}ms`, async () => {
      const payload = pushPayload(ageMs, language);
      const sw = serviceWorker();
      await sw.push(payload);
      const browser = foreground(language);
      const event = closeEvent(ageMs);
      await browser.showNative(payload.title, payload.body, event);
      const suffix = ageMs > 5 * 60_000
        ? ` · ${language === "pl" ? "OPÓŹNIONE" : "DELAYED"} ${Math.floor(ageMs / 60_000)} min` : "";
      assert.equal(sw.shown[0].body, payload.body + suffix);
      assert.equal(browser.shown[0].body, payload.body + suffix);
      assert.equal(sw.shown[0].timestamp, NOW);
      assert.equal(sw.shown[0].data.event_at, payload.event_at);
      assert.equal(sw.shown[0].data.sent_at, payload.sent_at);
      assert.equal(browser.shown[0].data.closed_at, event.closed_at);
    });
  }
}

test("missing or invalid clocks do not invent a delay warning", () => {
  const sw = serviceWorker();
  assert.equal(sw.context.pushDisplayCopy({ body: "body" }), "body");
  assert.equal(sw.context.pushDisplayCopy({ body: "body", event_at: "bad-clock" }), "body");
});

test("concurrent push does not ACK a claim before failed display; retry can display", async () => {
  let rejectDisplay;
  let enteredDisplay;
  const entered = new Promise((resolve) => { enteredDisplay = resolve; });
  let failFirst = true;
  const sw = serviceWorker({ show: async () => {
    if (!failFirst) return;
    enteredDisplay();
    await new Promise((_resolve, reject) => { rejectDisplay = reject; });
  } });
  const first = sw.push(pushPayload());
  const rejected = assert.rejects(first, /display-blocked/);
  await entered;
  await sw.push(pushPayload());
  assert.equal(sw.acks.length, 0, "pending claim is not proof of display");
  rejectDisplay(new Error("display-blocked"));
  await rejected;
  assert.equal(sw.database.records.size, 0, "failed display releases its claim");
  failFirst = false;
  await sw.push(pushPayload());
  assert.equal(sw.shown.length, 1);
  assert.equal(sw.acks.length, 1);
});

test("completed display survives worker restart and permits ACK without another alert", async () => {
  const database = deviceDatabase();
  const first = serviceWorker({ database });
  await first.push(pushPayload());
  const restarted = serviceWorker({ database });
  await restarted.push(pushPayload());
  assert.equal(first.shown.length, 1);
  assert.equal(restarted.shown.length, 0);
  assert.equal(restarted.acks.length, 1);
});

test("legacy claim alone is deduped without inventing display acknowledgement", async () => {
  const database = deviceDatabase();
  database.records.set("daily|CLOSE|p1", { id: "daily|CLOSE|p1", seen_at: NOW - 60_000 });
  const sw = serviceWorker({ database });
  await sw.push(pushPayload());
  assert.equal(sw.shown.length, 0);
  assert.equal(sw.acks.length, 0);
});

test("visible existing notification is sufficient evidence for ACK", async () => {
  const sw = serviceWorker({ existing: [{ tag: "daily|CLOSE|p1" }] });
  await sw.push(pushPayload());
  assert.equal(sw.shown.length, 0);
  assert.equal(sw.acks.length, 1);
});

test("foreground and push share one confirmed display across contexts", async () => {
  const database = deviceDatabase();
  const browser = foreground("pl", { database });
  await browser.showNative("title", "body", closeEvent());
  const sw = serviceWorker({ database });
  await sw.push(pushPayload());
  assert.equal(browser.shown.length, 1);
  assert.equal(sw.shown.length, 0);
  assert.equal(sw.acks.length, 1);
  await browser.showNative("title", "body", closeEvent());
  assert.equal(browser.shown.length, 1);
});

test("foreground pending display cannot be ACKed by a simultaneous push", async () => {
  const database = deviceDatabase();
  let release;
  let started;
  const entered = new Promise((resolve) => { started = resolve; });
  const browser = foreground("pl", { database, show: async () => {
    started();
    await new Promise((resolve) => { release = resolve; });
  } });
  const showing = browser.showNative("title", "body", closeEvent());
  await entered;
  const sw = serviceWorker({ database });
  await sw.push(pushPayload());
  assert.equal(sw.acks.length, 0);
  release();
  await showing;
  await sw.push(pushPayload());
  assert.equal(sw.acks.length, 1);
  assert.equal(sw.shown.length, 0);
});

test("foreground recovers fresh CLOSE using observed_at rather than old entry time", async () => {
  const freshClose = closeEvent();
  delete freshClose.closed_at;
  const staleClose = { ...closeEvent(3 * 60 * 60_000), event_id: "stale-close", position_id: "stale" };
  const recovery = { ...closeEvent(), event_id: "recovery", position_id: "recovery", delivery_recovery: true };
  const browser = foreground("pl", { events: [freshClose, staleClose, recovery] });
  browser.local.set("brTradingNotificationsV1", JSON.stringify({ enabled: true }));
  browser.local.set("brTradingNotificationsCursorV1", "old-cursor");
  await browser.pollEvents({});
  assert.equal(browser.shown.length, 1);
  assert.equal(browser.shown[0].data.position_id, "p1");
  assert.doesNotMatch(browser.shown[0].body, /OPÓŹNIONE/);
});

test("first foreground poll seeds cursor without replaying historical alerts", async () => {
  const browser = foreground("pl", { events: [closeEvent()] });
  browser.local.set("brTradingNotificationsV1", JSON.stringify({ enabled: true }));
  await browser.pollEvents({});
  assert.equal(browser.shown.length, 0);
  assert.equal(browser.local.get("brTradingNotificationsCursorV1"), "close-p1");
});
