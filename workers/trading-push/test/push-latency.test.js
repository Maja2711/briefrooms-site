import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { notificationPayload, pushAckLagMetrics } from "../src/index.js";

test("provider acknowledgement backlog measures >2m and >10m without resending", () => {
  const now = Date.parse("2026-10-20T02:00:00Z");
  const rows = [
    { status: "SENT_TO_PUSH", sent_to_push_at: "2026-10-20T01:59:30Z" },
    { status: "SENT_TO_PUSH", sent_to_push_at: "2026-10-20T01:57:00Z" },
    { status: "SENT_TO_PUSH", sent_to_push_at: "2026-10-20T01:40:00Z" },
    { status: "ACKED", sent_to_push_at: "2026-10-20T01:00:00Z" },
    { status: "UNSENT", sent_to_push_at: "2026-10-20T01:00:00Z" },
  ];
  assert.deepEqual(pushAckLagMetrics(rows, now), {
    unacked_over_2m: 2,
    unacked_over_10m: 1,
    oldest_unacked_ms: 20 * 60_000,
  });
});

test("backend Web Push retains lifecycle time and true price precision", () => {
  const event = {
    event_id: "eur-timestamp", engine: "daily", event_type: "CLOSE",
    position_id: "p1", instrument: "EUR/USD", direction: "SHORT",
    entry: 1.11906898, exit_price: 1.12477137,
    closed_at: "2026-10-20T01:00:00Z",
  };
  const payload = JSON.parse(notificationPayload(event, "pl"));
  assert.equal(payload.event_at, "2026-10-20T01:00:00Z");
  assert.equal(payload.language, "pl");
  assert.match(payload.body, /EUR\/USD @ 1,1248/);
  assert.ok(Number.isFinite(Date.parse(payload.sent_at)));
  assert.equal(event.exit_price, 1.12477137);
});

async function renderInServiceWorker(payload) {
  let shown = null;
  let pending = null;
  const handlers = {};
  const sw = {
    addEventListener: (name, handler) => { handlers[name] = handler; },
    registration: {
      getNotifications: async () => [],
      showNotification: async (_title, options) => { shown = options; },
    },
  };
  const script = readFileSync(new URL("../../../br-trading-sw.js", import.meta.url), "utf8");
  runInNewContext(script, { self: sw, Date, console, setTimeout }, { filename: "br-trading-sw.js" });
  assert.equal(typeof handlers.push, "function");
  handlers.push({
    data: { json: () => payload },
    waitUntil: (promise) => { pending = promise; },
  });
  await pending;
  return shown;
}

test("Android push shown after 30m uses display timestamp but explicitly warns it is late", async () => {
  const old = new Date(Date.now() - 30 * 60_000).toISOString();
  const payload = {
    title: "BriefRooms · Weekly Trading", body: "ZAMKNIĘTY SHORT · EUR/USD @ 1,1248",
    event_id: "delayed", event_at: old, sent_at: old, language: "pl",
    data: { engine: "weekly", event_type: "CLOSE", position_id: "p2" },
  };
  const before = Date.now();
  const shown = await renderInServiceWorker(payload);
  assert.ok(shown.timestamp >= before && shown.timestamp <= Date.now());
  assert.match(shown.body, /OPÓŹNIONE 30 min/);
  assert.equal(shown.data.event_at, old);
  assert.equal(shown.data.sent_at, old);
});

test("on-time Web Push retains unchanged trading copy without delay warning", async () => {
  const now = new Date().toISOString();
  const shown = await renderInServiceWorker({
    title: "BriefRooms · Stock Trading", body: "POZYCJA OTWARTA · ASO @ 58,26",
    event_id: "fresh", event_at: now, sent_at: now, language: "pl",
    data: { engine: "stock", event_type: "OPEN", position_id: "aso" },
  });
  assert.equal(shown.body, "POZYCJA OTWARTA · ASO @ 58,26");
});
