import test from "node:test";
import assert from "node:assert/strict";
import webpush from "web-push";
import {
  dailyCommitSnapshot,
  weeklyCommitSnapshot,
  stockCommitSnapshot,
  transitionDescriptors,
  diffPersistedOutboxEvents,
  notificationPayload,
  storedCommitSnapshot,
  persistableCommitSnapshot,
  sameCommitSnapshot,
  isoWeekId,
  DELIVERY_STATUS,
  deliveryStorageKey,
  sameImmutableEvent,
  newDeliveryRecord,
  transitionDelivery,
  retryDelayMs,
  deliveryReadyForRetry,
  isSyntheticRecoveryEvent,
  PushHub,
} from "../src/index.js";

test("Persisted WES outbox preserves OPEN and CLOSE created in the same run", () => {
  const before = { schema_version: "briefrooms-wes-notification-outbox-v1", events: [] };
  const after = {
    schema_version: "briefrooms-wes-notification-outbox-v1",
    events: [
      {
        event_id: "open-event",
        engine: "weekly",
        event_type: "OPEN",
        position_id: "2026-W41:eurusd:2026-10-07T16:35:50+02:00",
        instrument: "EUR/USD",
        direction: "SHORT",
        entry: 1.12,
        opened_at: "2026-10-07T16:35:50+02:00",
        observed_at: "2026-10-07T16:35:50+02:00",
      },
      {
        event_id: "close-event",
        engine: "weekly",
        event_type: "CLOSE",
        position_id: "2026-W41:eurusd:2026-10-07T16:35:50+02:00",
        instrument: "EUR/USD",
        direction: "SHORT",
        entry: 1.12,
        opened_at: "2026-10-07T16:35:50+02:00",
        exit_price: 1.124,
        exit_reason: "stop_loss",
        closed_at: "2026-10-07T16:39:50+02:00",
        observed_at: "2026-10-07T16:39:50+02:00",
      },
    ],
  };
  assert.deepEqual(
    diffPersistedOutboxEvents(before, after, "weekly").map((x) => x.event_type),
    ["OPEN", "CLOSE"],
  );
});

test("WES outbox retry does not redispatch event IDs already present in parent commit", () => {
  const event = {
    event_id: "same-event",
    engine: "weekly",
    event_type: "OPEN",
    position_id: "p1",
  };
  assert.deepEqual(diffPersistedOutboxEvents({ events: [event] }, { events: [event] }, "weekly"), []);
});

test("Legacy snapshot transition helper remains covered for Daily/Stock compatibility", () => {
  const before = weeklyCommitSnapshot({
    week_id: "2026-W41",
    instruments: [
      { instrument_id: "eurusd", label_pl: "EUR/USD", trade_status: "open", direction: "short", entry_price: 1.12, entry_captured_at: "2026-10-05T08:00:00Z" },
      { instrument_id: "btcusd", label_pl: "BTC/USD", trade_status: "no_trade", direction: "neutral", entry_price: null },
      { instrument_id: "sp500_futures", label_pl: "S&P 500 futures", trade_status: "open", direction: "long", entry_price: 6800, entry_captured_at: "2026-10-05T08:05:00Z" },
    ],
  });
  const after = weeklyCommitSnapshot({
    week_id: "2026-W41",
    instruments: [
      { instrument_id: "eurusd", label_pl: "EUR/USD", trade_status: "closed", direction: "short", entry_price: 1.12, entry_captured_at: "2026-10-05T08:00:00Z", exit_price: 1.125, exit_reason: "stop_loss", exit_captured_at: "2026-10-05T09:00:00Z" },
      { instrument_id: "btcusd", label_pl: "BTC/USD", trade_status: "open", direction: "long", entry_price: 125000, entry_captured_at: "2026-10-05T09:00:00Z" },
      { instrument_id: "sp500_futures", label_pl: "S&P 500 futures", trade_status: "open", direction: "long", entry_price: 6800, entry_captured_at: "2026-10-05T08:05:00Z" },
    ],
  });

  const events = transitionDescriptors("weekly", before, after, "2026-10-05T09:00:01Z");
  assert.equal(events.length, 2);
  const close = events.find((x) => x.event_type === "CLOSE");
  const open = events.find((x) => x.event_type === "OPEN");
  assert.equal(close.instrument, "EUR/USD");
  assert.equal(close.exit_reason, "stop_loss");
  assert.equal(close.exit_price, 1.125);
  assert.equal(open.instrument, "BTC/USD");
  assert.equal(open.direction, "LONG");
  assert.equal(events.some((x) => x.instrument === "S&P 500 futures"), false);
});

test("Stock commit diff emits one close and one new admission without replaying unchanged positions", () => {
  const before = stockCommitSnapshot({
    updated_at: "2026-10-05T09:00:00Z",
    markets: {
      US: { open_positions: [
        { position_id: "us:a:AAA", status: "OPEN", ticker: "AAA", opened_at: "2026-10-05T08:00:00Z", entry: 10 },
        { position_id: "us:b:BBB", status: "OPEN", ticker: "BBB", opened_at: "2026-10-05T08:10:00Z", entry: 20 },
      ], closed_positions: [] },
    },
  });
  const after = stockCommitSnapshot({
    updated_at: "2026-10-05T09:05:00Z",
    markets: {
      US: {
        open_positions: [
          { position_id: "us:b:BBB", status: "OPEN", ticker: "BBB", opened_at: "2026-10-05T08:10:00Z", entry: 20 },
          { position_id: "us:c:CCC", status: "OPEN", ticker: "CCC", opened_at: "2026-10-05T09:05:00Z", entry: 30 },
        ],
        closed_positions: [
          { position_id: "us:a:AAA", status: "CLOSED", ticker: "AAA", opened_at: "2026-10-05T08:00:00Z", entry: 10, exit_price: 9.5, exit_reason: "stop_loss", closed_at: "2026-10-05T09:05:00Z" },
        ],
      },
    },
  });

  const events = transitionDescriptors("stock", before, after, "2026-10-05T09:05:01Z");
  assert.deepEqual(events.map((x) => [x.event_type, x.instrument]).sort(), [["CLOSE", "AAA"], ["OPEN", "CCC"]]);
  assert.equal(events.find((x) => x.event_type === "CLOSE").exit_price, 9.5);
});

test("Background push copy and destination are localized for PL and EN", () => {
  const event = {
    event_id: "e1",
    engine: "weekly",
    event_type: "CLOSE",
    position_id: "p1",
    instrument: "BTC/USD",
    direction: "LONG",
    entry: 120000,
    exit_price: 125000,
    exit_reason: "TAKE_PROFIT",
  };
  const pl = JSON.parse(notificationPayload(event, "pl", "https://push.example"));
  const en = JSON.parse(notificationPayload(event, "en", "https://push.example"));
  assert.match(pl.body, /^TP OSIĄGNIĘTY/);
  assert.equal(pl.url, "/pl/inwestycje/pozycje-tygodniowe.html");
  assert.match(en.body, /^TAKE PROFIT/);
  assert.equal(en.url, "/en/investing/open-weekly-positions.html");
});


test("PL and EN routes remain correct for Daily Weekly and Stock channels", () => {
  const fixtures = [
    {
      event: { event_id: "d1", engine: "daily", event_type: "OPEN", position_id: "d", instrument: "EUR/USD", direction: "SHORT", entry: 1.12 },
      plUrl: "/pl/inwestycje/daily-trading.html",
      enUrl: "/en/investing/daily-trading.html",
      plAction: "OTWARTO",
      enAction: "OPENED",
    },
    {
      event: { event_id: "w1", engine: "weekly", event_type: "OPEN", position_id: "w", instrument: "S&P 500 futures", direction: "LONG", entry: 6800 },
      plUrl: "/pl/inwestycje/pozycje-tygodniowe.html",
      enUrl: "/en/investing/open-weekly-positions.html",
      plAction: "OTWARTO",
      enAction: "OPENED",
    },
    {
      event: { event_id: "s1", engine: "stock", event_type: "CLOSE", position_id: "s", instrument: "AAPL", direction: "LONG", entry: 250, exit_price: 255, exit_reason: "TIME_EXIT" },
      plUrl: "/pl/inwestycje/stock-trading.html",
      enUrl: "/en/investing/stock-trading.html",
      plAction: "ZAMKNIĘTO",
      enAction: "CLOSED",
    },
  ];

  for (const row of fixtures) {
    const pl = JSON.parse(notificationPayload(row.event, "pl", "https://push.example"));
    const en = JSON.parse(notificationPayload(row.event, "en", "https://push.example"));
    assert.equal(pl.url, row.plUrl);
    assert.equal(en.url, row.enUrl);
    assert.match(pl.body, new RegExp(`^${row.plAction}`));
    assert.match(en.body, new RegExp(`^${row.enAction}`));
  }
});


test("Commit-bound CLOSE requires an explicit persisted closed record", () => {
  const before = weeklyCommitSnapshot({
    week_id: "2026-W41",
    instruments: [
      { instrument_id: "eurusd", label_pl: "EUR/USD", trade_status: "open", direction: "short", entry_price: 1.12, entry_captured_at: "2026-10-05T08:00:00Z" },
    ],
  });
  const after = weeklyCommitSnapshot({
    week_id: "2026-W41",
    instruments: [],
  });
  assert.deepEqual(transitionDescriptors("weekly", before, after, "2026-10-05T09:00:00Z"), []);
});

test("Weekly OPEN requires explicit active status and verified fill timestamp", () => {
  const empty = weeklyCommitSnapshot({ week_id: "2026-W41", instruments: [] });
  const incomplete = weeklyCommitSnapshot({
    week_id: "2026-W41",
    instruments: [
      { instrument_id: "btcusd", label_pl: "BTC/USD", trade_status: "", direction: "long", entry_price: 125000 },
    ],
  });
  assert.equal(incomplete.open.size, 0);
  assert.deepEqual(transitionDescriptors("weekly", empty, incomplete, "2026-10-05T09:00:00Z"), []);
});

test("Weekly pending LIMIT plan never emits OPEN without a verified fill", () => {
  const before = weeklyCommitSnapshot({
    week_id: "2026-W41",
    instruments: [
      { instrument_id: "eurusd", label_pl: "EUR/USD", trade_status: "no_trade", direction: "neutral", entry_price: null },
    ],
  });
  const after = weeklyCommitSnapshot({
    week_id: "2026-W41",
    instruments: [
      {
        instrument_id: "eurusd",
        label_pl: "EUR/USD",
        trade_status: "pending",
        direction: "short",
        entry_price: null,
        pending_entry_decision: {
          entry_price_plan: { execution_mode: "price_improving_limit", target_price: 1.12 },
        },
      },
    ],
  });
  assert.equal(after.open.size, 0);
  assert.deepEqual(transitionDescriptors("weekly", before, after, "2026-10-05T10:00:00Z"), []);
});


test("Commit snapshot fallback round-trips canonical OPEN state without inventing transitions", () => {
  const original = weeklyCommitSnapshot({
    week_id: "2026-W41",
    instruments: [
      {
        instrument_id: "eurusd",
        label_pl: "EUR/USD",
        trade_status: "open",
        direction: "short",
        entry_price: 1.11906898,
        entry_captured_at: "2026-10-07T16:35:50+02:00",
      },
    ],
  });
  const stored = persistableCommitSnapshot(original);
  const restored = storedCommitSnapshot(stored);
  assert.equal(restored.open.size, 1);
  assert.equal(restored.open.values().next().value.instrument, "EUR/USD");
  assert.deepEqual(transitionDescriptors("weekly", restored, original, "2026-10-07T16:36:00Z"), []);
});

test("ISO week fallback resolves the active WES week", () => {
  assert.equal(isoWeekId(new Date("2026-10-07T12:00:00Z")), "2026-W41");
});


test("Daily commit diff emits OPEN from persisted v1.9 position", () => {
  const before = dailyCommitSnapshot({
    status: "NO_TRADE",
    metadata: {},
  });
  const after = dailyCommitSnapshot({
    status: "OPEN",
    metadata: {
      position: {
        trade_id: "eurusd:20261007T180000Z:SHORT",
        status: "OPEN",
        direction: "SHORT",
        opened_at: "2026-10-07T18:00:00Z",
        entry: 1.1185,
      },
    },
  });
  const events = transitionDescriptors("daily", before, after, "2026-10-07T18:00:01Z");
  assert.equal(events.length, 1);
  assert.equal(events[0].event_type, "OPEN");
  assert.equal(events[0].position_id, "eurusd:20261007T180000Z:SHORT");
  assert.equal(events[0].instrument, "EUR/USD");
  assert.equal(events[0].direction, "SHORT");
  assert.equal(events[0].entry, 1.1185);
});

test("Daily commit diff emits CLOSE only from persisted last_trade metadata", () => {
  const before = dailyCommitSnapshot({
    status: "OPEN",
    metadata: {
      position: {
        trade_id: "eurusd:20261007T180000Z:SHORT",
        status: "OPEN",
        direction: "SHORT",
        opened_at: "2026-10-07T18:00:00Z",
        entry: 1.1185,
      },
    },
  });
  const after = dailyCommitSnapshot({
    status: "CLOSED_TP",
    metadata: {
      position: null,
      last_trade: {
        trade_id: "eurusd:20261007T180000Z:SHORT",
        direction: "SHORT",
        opened_at: "2026-10-07T18:00:00Z",
        entry: 1.1185,
        exit_reason: "TAKE_PROFIT",
        exit_price: 1.1120,
        closed_at: "2026-10-07T19:00:00Z",
        r_multiple: 1.8,
      },
    },
  });
  const events = transitionDescriptors("daily", before, after, "2026-10-07T19:00:01Z");
  assert.equal(events.length, 1);
  assert.equal(events[0].event_type, "CLOSE");
  assert.equal(events[0].exit_reason, "TAKE_PROFIT");
  assert.equal(events[0].exit_price, 1.1120);
  assert.equal(events[0].closed_at, "2026-10-07T19:00:00Z");
});


test("Fallback snapshot guard ignores timestamps but rejects stale trading state", () => {
  const flatA = dailyCommitSnapshot({ timestamp: "2026-10-07T18:00:00Z", metadata: {} });
  const flatB = dailyCommitSnapshot({ timestamp: "2026-10-07T18:00:05Z", metadata: {} });
  assert.equal(sameCommitSnapshot(flatA, flatB), true);

  const opened = dailyCommitSnapshot({
    timestamp: "2026-10-07T18:00:05Z",
    metadata: {
      position: {
        trade_id: "eurusd:20261007T180005Z:SHORT",
        status: "OPEN",
        direction: "SHORT",
        opened_at: "2026-10-07T18:00:05Z",
        entry: 1.1185,
      },
    },
  });
  assert.equal(sameCommitSnapshot(flatA, opened), false);
});


test("durable delivery key is unique per event and subscriber", () => {
  assert.equal(deliveryStorageKey("event-1", "sub-a"), "delivery:event-1:sub-a");
  assert.notEqual(deliveryStorageKey("event-1", "sub-a"), deliveryStorageKey("event-1", "sub-b"));
  assert.notEqual(deliveryStorageKey("event-1", "sub-a"), deliveryStorageKey("event-2", "sub-a"));
});

test("durable delivery follows UNSENT to SENDING to SENT_TO_PUSH to ACKED", () => {
  const base = newDeliveryRecord("event-1", "sub-a", "secret", "2026-10-07T10:00:00.000Z");
  assert.equal(base.status, DELIVERY_STATUS.UNSENT);
  const sending = transitionDelivery(base, DELIVERY_STATUS.SENDING, "2026-10-07T10:00:01.000Z", { attempts: 1 });
  const sent = transitionDelivery(sending, DELIVERY_STATUS.SENT_TO_PUSH, "2026-10-07T10:00:02.000Z", {
    sent_to_push_at: "2026-10-07T10:00:02.000Z",
  });
  const acked = transitionDelivery(sent, DELIVERY_STATUS.ACKED, "2026-10-07T10:00:03.000Z", {
    acked_at: "2026-10-07T10:00:03.000Z",
  });
  assert.equal(acked.status, DELIVERY_STATUS.ACKED);
  assert.throws(() => transitionDelivery(acked, DELIVERY_STATUS.SENDING), /invalid_delivery_transition/);
});

test("provider-accepted pushes cannot replay when browser ACK is absent", () => {
  const unsent = newDeliveryRecord("event-1", "sub-a", "secret", "2026-10-07T10:00:00.000Z");
  assert.equal(deliveryReadyForRetry(unsent, Date.parse("2026-10-07T10:00:01.000Z")), true);

  const sending = transitionDelivery(unsent, DELIVERY_STATUS.SENDING, "2026-10-07T10:00:00.000Z", { attempts: 1 });
  assert.equal(deliveryReadyForRetry(sending, Date.parse("2026-10-07T10:01:00.000Z")), false);
  assert.equal(deliveryReadyForRetry(sending, Date.parse("2026-10-07T10:02:01.000Z")), false);

  const sent = transitionDelivery(sending, DELIVERY_STATUS.SENT_TO_PUSH, "2026-10-07T10:00:00.000Z", {
    sent_to_push_at: "2026-10-07T10:00:00.000Z",
  });
  assert.equal(deliveryReadyForRetry(sent, Date.parse("2026-10-07T10:04:59.000Z")), false);
  assert.equal(deliveryReadyForRetry(sent, Date.parse("2026-10-07T10:05:01.000Z")), false);
  assert.equal(deliveryReadyForRetry(sent, Date.parse("2026-10-07T12:05:01.000Z")), false);
  assert.equal(deliveryReadyForRetry({ ...sent, next_retry_at: "2026-10-07T10:05:00.000Z" }, Date.parse("2026-10-07T12:05:01.000Z")), false);
  assert.equal(retryDelayMs(1), 30000);
  assert.ok(retryDelayMs(99) <= 15 * 60 * 1000);
});

test("immutable event can be re-ingested with transport metadata changes but not trading truth changes", () => {
  const a = {
    event_id: "e1", engine: "weekly", event_type: "OPEN", position_id: "p1",
    instrument: "EUR/USD", direction: "SHORT", entry: 1.12,
    opened_at: "2026-10-07T10:00:00Z", source: "commit",
  };
  const b = { ...a, source: "recovery", observed_at: "2026-10-07T10:00:05Z" };
  const conflict = { ...a, entry: 1.13 };
  assert.equal(sameImmutableEvent(a, b), true);
  assert.equal(sameImmutableEvent(a, conflict), false);
});

test("push payload carries delivery ACK contract", () => {
  const event = {
    event_id: "e1", engine: "weekly", event_type: "OPEN", position_id: "p1",
    instrument: "EUR/USD", direction: "SHORT", entry: 1.12,
  };
  const delivery = newDeliveryRecord("e1", "sub-a", "ack-secret");
  const payload = JSON.parse(notificationPayload(event, "pl", "https://push.example", delivery));
  assert.equal(payload.delivery_id, "e1:sub-a");
  assert.equal(payload.ack_token, "ack-secret");
  assert.equal(payload.ack_url, "https://push.example/ack");
  assert.equal(payload.data.delivery_id, "e1:sub-a");
});


class FakeStorage {
  constructor() { this.map = new Map(); }
  async get(key) { return this.map.get(key); }
  async put(key, value) { this.map.set(key, structuredClone(value)); }
  async delete(key) { this.map.delete(key); }
  async list({ prefix = "" } = {}) {
    return new Map([...this.map.entries()].filter(([key]) => String(key).startsWith(prefix)));
  }
}

test("PushHub persists immutable events and creates one delivery per eligible subscriber", async () => {
  const storage = new FakeStorage();
  await storage.put("sub:sub-a", {
    subscription: { endpoint: "https://push.example/a" },
    preferences: { channels: { weekly: true }, events: { open: true } },
    language: "pl",
  });
  await storage.put("sub:sub-b", {
    subscription: { endpoint: "https://push.example/b" },
    preferences: { channels: { weekly: true }, events: { open: true } },
    language: "en",
  });
  const hub = new PushHub({ storage }, {});
  const event = {
    event_id: "evt-1",
    engine: "weekly",
    event_type: "OPEN",
    position_id: "pos-1",
    instrument: "EUR/USD",
    direction: "SHORT",
    entry: 1.12,
    opened_at: "2026-10-07T10:00:00Z",
  };

  await hub.persistImmutableEvent(event);
  await hub.initializeRecipients(event);
  await hub.initializeRecipients(event);

  const deliveries = await storage.list({ prefix: "delivery:evt-1:" });
  assert.equal(deliveries.size, 2);
  assert.ok(deliveries.has("delivery:evt-1:sub-a"));
  assert.ok(deliveries.has("delivery:evt-1:sub-b"));

  await hub.persistImmutableEvent({ ...event, source: "recovery" });
  await assert.rejects(
    () => hub.persistImmutableEvent({ ...event, entry: 1.13 }),
    /immutable_event_conflict/,
  );
});

test("recovered CLOSE aliases cannot dispatch a second notification", async () => {
  const storage = new FakeStorage();
  const hub = new PushHub({ storage }, {});
  const original = {
    event_id: "abc123", engine: "daily", event_type: "CLOSE",
    position_id: "eurusd:1", instrument: "EUR/USD",
    direction: "SHORT", closed_at: "2026-10-02T14:05:00Z",
  };
  assert.equal(isSyntheticRecoveryEvent(original), false);
  assert.equal(isSyntheticRecoveryEvent({ ...original, event_id: "abc123-r1", delivery_recovery: true }), true);
  const result = await hub.dispatchEvents([{ ...original, event_id: "abc123-r1", delivery_recovery: true }]);
  assert.equal(result.sent, 0);
  assert.equal((await storage.list({ prefix: "event:" })).size, 0);
});

test("pending legacy recovery event is expired rather than resent", async () => {
  const storage = new FakeStorage();
  await storage.put("sub:sub-a", {
    subscription: { endpoint: "https://push.example/a" },
    preferences: { channels: { daily: true }, events: { close: true } },
    language: "pl",
  });
  const hub = new PushHub({ storage }, {});
  const recovery = {
    event_id: "abc123-r1", engine: "daily", event_type: "CLOSE",
    position_id: "eurusd:1", instrument: "EUR/USD", direction: "SHORT",
    closed_at: "2026-10-02T14:05:00Z", delivery_recovery: true,
  };
  await hub.persistImmutableEvent(recovery);
  await hub.initializeRecipients(recovery);
  const result = await hub.deliverPending({ eventIds: ["abc123-r1"] });
  assert.equal(result.sent, 0);
  assert.equal(result.duplicate_suppressed, 1);
  const row = await storage.get("delivery:abc123-r1:sub-a");
  assert.equal(row.status, DELIVERY_STATUS.EXPIRED);
  assert.equal(row.last_error, "synthetic_recovery_replay_suppressed");
});

test("legacy accepted-but-unacknowledged push is completed and retry timer is cleared", async () => {
  const storage = new FakeStorage();
  const hub = new PushHub({ storage }, {});
  const base = newDeliveryRecord("evt-accepted", "sub-a", "secret", "2026-10-07T10:00:00.000Z");
  const sending = transitionDelivery(base, DELIVERY_STATUS.SENDING, "2026-10-07T10:00:01.000Z", { attempts: 1 });
  await storage.put("delivery:evt-accepted:sub-a", transitionDelivery(sending, DELIVERY_STATUS.SENT_TO_PUSH, "2026-10-07T10:00:02.000Z", {
    sent_to_push_at: "2026-10-07T10:00:02.000Z",
    next_retry_at: "2026-10-07T10:05:02.000Z",
  }));
  await storage.put("recipients:evt-accepted", { initialized_at: "2026-10-07T10:00:00.000Z", count: 1 });
  assert.equal(await hub.refreshEventCompletion("evt-accepted"), true);
  const result = await hub.deliverPending();
  assert.equal(result.sent, 0);
  assert.equal(result.pending, 0);
  const delivered = await storage.get("delivery:evt-accepted:sub-a");
  assert.equal(delivered.status, DELIVERY_STATUS.SENT_TO_PUSH);
  assert.equal(delivered.next_retry_at, null);
  assert.equal(delivered.attempts, 1);
  assert.equal(await storage.get("event-complete:evt-accepted"), true);
});

test("lost device ACK cannot resend Allegro close even after retry deadline", async () => {
  const storage = new FakeStorage();
  const hub = new PushHub({ storage }, { PUBLIC_BASE_URL: "https://push.example" });
  const event = {
    event_id: "ale-close", engine: "stock", event_type: "CLOSE",
    position_id: "gpw:allegro:1", instrument: "ALE", direction: "LONG",
    closed_at: new Date(Date.now() - 6 * 60_000).toISOString(),
    exit_reason: "stop_loss",
  };
  await storage.put("sub:samsung", {
    subscription: { endpoint: "https://push.example/samsung" },
    preferences: { channels: { stock: true }, events: { close: true } },
    language: "pl",
  });
  await hub.persistImmutableEvent(event);
  await hub.initializeRecipients(event);
  const key = "delivery:ale-close:samsung";
  const record = await storage.get(key);
  const sentAt = new Date(Date.now() - 6 * 60_000).toISOString();
  const oneSent = transitionDelivery(
    transitionDelivery(record, DELIVERY_STATUS.SENDING, sentAt, { attempts: 1 }),
    DELIVERY_STATUS.SENT_TO_PUSH, sentAt, {
      sent_to_push_at: sentAt,
      next_retry_at: new Date(Date.now() - 60_000).toISOString(),
    },
  );
  await storage.put(key, oneSent);
  let pushes = 0;
  const originalPush = webpush.sendNotification;
  webpush.sendNotification = async (_subscriber, payload) => {
    const parsed = JSON.parse(payload);
    assert.equal(parsed.event_id, "ale-close");
    assert.equal(parsed.data.logical_event_key, "stock|CLOSE|gpw:allegro:1");
    assert.equal(parsed.delivery_id, "ale-close:samsung");
    pushes += 1;
    return { statusCode: 201 };
  };
  try {
    const first = await hub.deliverPending();
    assert.equal(first.sent, 0);
    assert.equal(first.awaiting_ack, 1);
    assert.equal((await storage.get(key)).attempts, 1);
    assert.equal((await storage.get(key)).next_retry_at, null);
    assert.equal((await hub.deliverPending()).sent, 0);
    assert.equal(pushes, 0);
    const ack = await hub.acknowledgeDelivery({
      delivery_id: "ale-close:samsung", ack_token: record.ack_token,
    });
    assert.equal(ack.delivery_status, DELIVERY_STATUS.ACKED);
    assert.equal((await hub.deliverPending()).sent, 0);
    assert.equal(pushes, 0);
  } finally {
    webpush.sendNotification = originalPush;
  }
});

test("stale SENDING gets closed as uncertain without repeating the push", async () => {
  const storage = new FakeStorage();
  const hub = new PushHub({ storage }, {});
  const base = newDeliveryRecord("evt-uncertain", "sub-a", "secret", "2026-10-07T10:00:00.000Z");
  await storage.put("delivery:evt-uncertain:sub-a", transitionDelivery(base, DELIVERY_STATUS.SENDING, "2026-10-07T10:00:01.000Z", { attempts: 1 }));
  const result = await hub.deliverPending();
  assert.equal(result.pending, 0);
  assert.equal(result.duplicate_suppressed, 1);
  const item = await storage.get("delivery:evt-uncertain:sub-a");
  assert.equal(item.status, DELIVERY_STATUS.EXPIRED);
  assert.equal(item.attempts, 1);
  assert.equal(item.last_error, "ambiguous_send_not_replayed");
});

test("PushHub ACK is authenticated and idempotent", async () => {
  const storage = new FakeStorage();
  const hub = new PushHub({ storage }, {});
  const delivery = newDeliveryRecord("evt-2", "sub-a", "ack-secret", "2026-10-07T10:00:00.000Z");
  const sending = transitionDelivery(delivery, DELIVERY_STATUS.SENDING, "2026-10-07T10:00:00.500Z", { attempts: 1 });
  await storage.put("delivery:evt-2:sub-a", transitionDelivery(sending, DELIVERY_STATUS.SENT_TO_PUSH, "2026-10-07T10:00:01.000Z", {
    sent_to_push_at: "2026-10-07T10:00:01.000Z",
  }));
  await storage.put("recipients:evt-2", { initialized_at: "2026-10-07T10:00:00.000Z", count: 1 });

  const denied = await hub.acknowledgeDelivery({ delivery_id: "evt-2:sub-a", ack_token: "wrong" });
  assert.equal(denied.status, 403);

  const first = await hub.acknowledgeDelivery({ delivery_id: "evt-2:sub-a", ack_token: "ack-secret" });
  const second = await hub.acknowledgeDelivery({ delivery_id: "evt-2:sub-a", ack_token: "ack-secret" });
  assert.equal(first.delivery_status, DELIVERY_STATUS.ACKED);
  assert.equal(second.delivery_status, DELIVERY_STATUS.ACKED);
  assert.equal((await storage.get("delivery:evt-2:sub-a")).status, DELIVERY_STATUS.ACKED);
});
