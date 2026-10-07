import test from "node:test";
import assert from "node:assert/strict";
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
