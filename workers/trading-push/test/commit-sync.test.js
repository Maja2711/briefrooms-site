import test from "node:test";
import assert from "node:assert/strict";
import {
  weeklyCommitSnapshot,
  stockCommitSnapshot,
  transitionDescriptors,
  notificationPayload,
} from "../src/index.js";

test("Weekly commit diff emits exact OPEN/CLOSE transitions for EURUSD BTC and SPX", () => {
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
