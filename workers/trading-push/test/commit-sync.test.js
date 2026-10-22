import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import webpush from "web-push";
import {
  dailyCommitSnapshot,
  weeklyCommitSnapshot,
  stockCommitSnapshot,
  transitionDescriptors,
  diffPersistedOutboxEvents,
  notificationPayload,
  eventToPushLatencyMs,
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
  assert.equal(pl.body, "ZAMKNIĘTY LONG · BTC/USD @ 125000,00");
  assert.equal(pl.url, "/pl/inwestycje/pozycje-tygodniowe.html");
  assert.equal(en.body, "CLOSED LONG · BTC/USD @ 125000.00");
  assert.equal(en.url, "/en/investing/open-weekly-positions.html");
});


test("PL and EN routes remain correct for Daily Weekly and Stock channels", () => {
  const fixtures = [
    {
      event: { event_id: "d1", engine: "daily", event_type: "OPEN", position_id: "d", instrument: "EUR/USD", direction: "SHORT", entry: 1.12 },
      plUrl: "/pl/inwestycje/daily-trading.html",
      enUrl: "/en/investing/daily-trading.html",
      plAction: "OTWARTY SHORT",
      enAction: "OPENED SHORT",
    },
    {
      event: { event_id: "w1", engine: "weekly", event_type: "OPEN", position_id: "w", instrument: "S&P 500 futures", direction: "LONG", entry: 6800 },
      plUrl: "/pl/inwestycje/pozycje-tygodniowe.html",
      enUrl: "/en/investing/open-weekly-positions.html",
      plAction: "OTWARTY LONG",
      enAction: "OPENED LONG",
    },
    {
      event: { event_id: "s1", engine: "stock", event_type: "CLOSE", position_id: "s", instrument: "AAPL", direction: "LONG", entry: 250, exit_price: 255, exit_reason: "TIME_EXIT" },
      plUrl: "/pl/inwestycje/stock-trading.html",
      enUrl: "/en/investing/stock-trading.html",
      plAction: "POZYCJA ZAMKNIĘTA",
      enAction: "POSITION CLOSED",
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


test("XP profit protected by a stop is labeled CLOSED, not STOP LOSS", () => {
  const event = {
    event_id: "xp-close", engine: "stock", event_type: "CLOSE",
    position_id: "us:20261001T144705:XP", instrument: "XP",
    direction: "LONG", entry: 21.19499969, exit_price: 30.9362493,
    exit_reason: "stop_loss", r_multiple: 12.2127,
  };
  const pl = JSON.parse(notificationPayload(event, "pl"));
  const en = JSON.parse(notificationPayload(event, "en"));
  assert.equal(pl.body, "POZYCJA ZAMKNIĘTA · XP @ 30,94 · Wynik cenowy: +45,96%");
  assert.equal(en.body, "POSITION CLOSED · XP @ 30.94 · Price return: +45.96%");
  assert.equal(pl.data.exit_reason, "stop_loss");
  assert.doesNotMatch(pl.body, /STOP LOSS|OTWARTY LONG|ZAMKNIĘTY LONG/);
  assert.equal(pl.event_id, event.event_id);
});

test("Stock OPEN never advertises a LONG or SHORT lifecycle", () => {
  const event = { event_id: "s-open", engine: "stock", event_type: "OPEN", position_id: "p", instrument: "XP", direction: "LONG", entry: 21.195 };
  assert.equal(JSON.parse(notificationPayload(event, "pl")).body, "POZYCJA OTWARTA · XP @ 21,20");
  assert.equal(JSON.parse(notificationPayload(event, "en")).body, "POSITION OPENED · XP @ 21.20");
});

test("Daily and Weekly directional close do not replace lifecycle by SL/TP", () => {
  for (const [engine, instrument, direction, reason, label] of [
    ["daily", "EUR/USD", "SHORT", "STOP_LOSS", "ZAMKNIĘTY SHORT"],
    ["weekly", "BTC/USD", "LONG", "TAKE_PROFIT", "ZAMKNIĘTY LONG"],
    ["weekly", "S&P 500 futures", "SHORT", "STOP_LOSS", "ZAMKNIĘTY SHORT"],
  ]) {
    const event = { event_id: "x", engine, event_type: "CLOSE", position_id: "p", instrument, direction, entry: 1, exit_price: 2, exit_reason: reason };
    const push = JSON.parse(notificationPayload(event, "pl"));
    assert.equal(push.body, `${label} · ${instrument} @ ${instrument === "EUR/USD" ? "2,0000" : "2,00"}`);
    assert.equal(push.data.exit_reason, reason);
  }
});

test("EUR/USD push uses four decimals in both languages and preserves original entry", () => {
  const event = { event_id: "eur-open", engine: "daily", event_type: "OPEN", position_id: "eur", instrument: "EUR/USD", direction: "LONG", entry: 1.12345678 };
  assert.equal(JSON.parse(notificationPayload(event, "pl")).body, "OTWARTY LONG · EUR/USD @ 1,1235");
  assert.equal(JSON.parse(notificationPayload(event, "en")).body, "OPENED LONG · EUR/USD @ 1.1235");
  assert.equal(event.entry, 1.12345678);
});

test("Event-to-provider latency uses the persisted lifecycle timestamp", () => {
  const now = Date.parse("2026-10-16T10:28:28.000Z");
  assert.equal(eventToPushLatencyMs({
    event_type: "CLOSE", closed_at: "2026-10-16T10:28:27.000Z",
    observed_at: "2026-10-16T10:29:00.000Z",
  }, now), 1000);
  assert.equal(eventToPushLatencyMs({
    event_type: "OPEN", opened_at: "2026-10-16T10:28:20.000Z",
  }, now), 8000);
  assert.equal(eventToPushLatencyMs({ event_type: "CLOSE" }, now), null);
});

test("Push formats ASO price with two decimals without mutating event precision", () => {
  const event = { event_id: "aso-open", engine: "stock", event_type: "OPEN", position_id: "aso", instrument: "ASO", direction: "LONG", entry: 58.26499939 };
  assert.equal(JSON.parse(notificationPayload(event, "pl")).body, "POZYCJA OTWARTA · ASO @ 58,26");
  assert.equal(JSON.parse(notificationPayload(event, "en")).body, "POSITION OPENED · ASO @ 58.26");
  assert.equal(event.entry, 58.26499939);
});

test("Unknown direction and missing exit price never manufacture trade details", () => {
  const event = { event_id: "unknown", engine: "weekly", event_type: "CLOSE", position_id: "u", instrument: "BTC/USD", entry: 100 };
  assert.equal(JSON.parse(notificationPayload(event, "pl")).body, "POZYCJA ZAMKNIĘTA · BTC/USD");
  const stock = { ...event, engine: "stock", instrument: "ABC", direction: "LONG" };
  assert.equal(JSON.parse(notificationPayload(stock, "pl")).body, "POZYCJA ZAMKNIĘTA · ABC");
  assert.equal(JSON.parse(notificationPayload({ ...stock, entry: 0, exit_price: 10 }, "pl")).body, "POZYCJA ZAMKNIĘTA · ABC @ 10,00");
});

test("Foreground and push formatters stay byte-equivalent ignoring indent", () => {
  const backend = readFileSync(new URL("../src/index.js", import.meta.url), "utf8");
  const foreground = readFileSync(new URL("../../../scripts/trading-notifications.js", import.meta.url), "utf8");
  const extract = (source) => {
    source = source.replace(/\r\n/g, "\n");
    const marker = "function notificationText(event, language) {";
    const at = source.indexOf(marker);
    assert.ok(at >= 0, "notificationText function must exist in each delivery path");
    const LF = String.fromCharCode(10);
    const code = source.slice(at).split(LF + LF)[0];
    return code.split(LF).map((line) => line.trim()).join(LF);
  };
  assert.equal(extract(backend), extract(foreground));
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
  assert.equal(retryDelayMs(1), 2000);
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

test("Event delivery summary exposes aggregated states without subscription data or secrets", async () => {
  const storage = new FakeStorage();
  const id = "cc15f7da07b386d5d3563c7b";
  await storage.put(`event:${id}`, {
    event: { event_id: id, engine: "daily", event_type: "OPEN",
      opened_at: "2026-10-22T03:52:02Z" },
    stored_at: "2026-10-22T03:52:14Z",
  });
  await storage.put(`recipients:${id}`, {
    count: 2, initialized_at: "2026-10-22T03:52:14Z",
  });
  await storage.put(`delivery:${id}:secret-device`, {
    event_id: id, status: DELIVERY_STATUS.SENT_TO_PUSH,
    sent_to_push_at: "2026-10-22T03:52:15Z", ack_token: "never_expose_me",
  });
  const hub = new PushHub({ storage }, {});
  const data = await hub.eventDeliverySummary(id);
  assert.equal(data.ok, true);
  assert.equal(data.recipients_initial_count, 2);
  assert.equal(data.deliveries.SENT_TO_PUSH, 1);
  assert.equal(data.first_provider_accepted_at, "2026-10-22T03:52:15Z");
  assert.equal(data.event_at, "2026-10-22T03:52:02Z");
  assert.equal(JSON.stringify(data).includes("secret-device"), false);
  assert.equal(JSON.stringify(data).includes("never_expose_me"), false);
  assert.equal((await hub.eventDeliverySummary("invalid")).ok, false);
});

test("Delivery diagnostics correlate ACK with the SAME subscriber rather than unrelated first acceptance", async () => {
  const storage = new FakeStorage();
  const id = "cc15f7da07b386d5d3563c7b";
  await storage.put(`event:${id}`, { event: { event_id: id, engine: "daily", event_type: "OPEN", opened_at: "2026-10-22T03:52:02Z" } });
  await storage.put(`recipients:${id}`, { count: 4, initialized_at: "2026-10-22T03:52:12Z" });
  await storage.put(`delivery:${id}:early-device`, { status: DELIVERY_STATUS.SENT_TO_PUSH,
    sent_to_push_at: "2026-10-22T03:52:13Z", ack_token: "private-early" });
  await storage.put(`delivery:${id}:late-device`, { status: DELIVERY_STATUS.ACKED,
    sent_to_push_at: "2026-10-22T05:49:59Z", acked_at: "2026-10-22T05:50:01Z",
    ack_token: "private-late" });
  const d = await new PushHub({ storage }, {}).eventDeliverySummary(id);
  assert.equal(d.first_provider_accepted_at, "2026-10-22T03:52:13Z");
  assert.equal(d.first_device_ack_at, "2026-10-22T05:50:01Z");
  assert.equal(d.same_delivery_ack_count, 1);
  assert.equal(d.same_delivery_ack_min_ms, 2000);
  assert.equal(d.same_delivery_ack_max_ms, 2000);
  assert.equal(d.accepted_without_ack_over_10m, 1);
  assert.equal(JSON.stringify(d).includes("private-"), false);
  assert.equal(JSON.stringify(d).includes("early-device"), false);
});

test("Daily OPEN and CLOSE already accepted by push provider are never re-sent on stale commit fallback", async () => {
  for (const kind of ["OPEN", "CLOSE"]) {
    const storage = new FakeStorage();
    const hub = new PushHub({ storage }, {});
    const pid = "eurusd:20261015T180754Z:LONG";
    const at = "2026-10-15T18:07:54Z";
    const pos = {
      position_id: pid, direction: "LONG", opened_at: at, entry: 1.12178,
      instrument: "EUR/USD", engine: "daily",
      ...(kind === "CLOSE" ? {
        closed_at: "2026-10-15T19:10:00Z", exit_reason: "TAKE_PROFIT",
        exit_price: 1.12709, r_multiple: 1.67,
      } : {}),
    };
    const snapshot = {
      open: kind === "OPEN" ? new Map([[pid, pos]]) : new Map(),
      closed: kind === "CLOSE" ? new Map([[pid, pos]]) : new Map(),
    };
    const id = createHash("sha256").update(`daily|${kind}|${pid}`).digest("hex").slice(0, 24);
    assert.equal(await hub.confirmedDailyEventsAtSnapshot(snapshot), false);
    await storage.put(`event:${id}`, { event: {
      ...pos, event_id: id, event_type: kind,
    } });
    await storage.put(`recipients:${id}`, { count: 1 });
    await storage.put(`delivery:${id}:sub-a`, {
      status: DELIVERY_STATUS.SENT_TO_PUSH, event_id: id,
    });
    await storage.put(`event-complete:${id}`, true);
    assert.equal(await hub.confirmedDailyEventsAtSnapshot(snapshot), true);
    await storage.put(`delivery:${id}:sub-a`, { status: DELIVERY_STATUS.UNSENT });
    assert.equal(await hub.confirmedDailyEventsAtSnapshot(snapshot), false);
    await storage.put(`delivery:${id}:sub-a`, { status: DELIVERY_STATUS.ACKED });
    assert.equal(await hub.confirmedDailyEventsAtSnapshot(snapshot), true);
    await storage.put(`event:${id}`, { event: {
      ...pos, event_id: id, event_type: kind, entry: 1.999,
    } });
    assert.equal(await hub.confirmedDailyEventsAtSnapshot(snapshot), false);
  }
});

test("A missing Daily CLOSE record cannot be acknowledged from an OPEN push", async () => {
  const hub = new PushHub({ storage: new FakeStorage() }, {});
  assert.equal(await hub.confirmedDailyEventsAtSnapshot({ open: new Map(), closed: new Map() }), false);
});

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
