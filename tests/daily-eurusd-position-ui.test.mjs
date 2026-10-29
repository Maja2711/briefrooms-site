import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const js = readFileSync("scripts/daily-eurusd-spot-public.js", "utf8");
const pl = readFileSync("pl/inwestycje/daily-trading.html", "utf8");
const en = readFileSync("en/investing/daily-trading.html", "utf8");

async function render(payload, history = {trades: []}, lang = "pl") {
  const root = {innerHTML: ""};
  const document = {
    documentElement: {lang},
    getElementById: id => id === "eurusd-daily-root" ? root : null,
    createElement: () => ({textContent: ""}),
    head: {appendChild() {}},
    dispatchEvent() {}
  };
  const fetch = async url => ({
    ok: true,
    json: async () => url.includes("eurusd_daily_spot.json")
      ? payload : url.includes("eurusd_daily_history.json") ? history : {prices: {}}
  });
  runInNewContext(js, {document, fetch, CustomEvent: class {
    constructor(name) {this.type=name;}
  }});
  await new Promise(resolve => setImmediate(resolve));
  return root.innerHTML;
}

test("FLAT displays exactly no-open-position status, never gate explanations or closed position", async () => {
  const html = await render({
    status: "NO_TRADE", direction: "FLAT", score: 54,
    metadata: {
      candidate: {direction:"FLAT", gate_reasons:["belief_score_neutral"], score:54},
      last_trade: {direction:"SHORT",result_percent:-0.5,entry:1.12345,exit_price:1.12555}
    }
  });
  assert.match(html, /Brak otwartej pozycji/);
  assert.doesNotMatch(html, /Dlaczego brak pozycji|Bieżący kandydat|belief_score_neutral|score 54|confidence|CLOSED_TIME|ZAMKNIĘCIE POZYCJI/);
  assert.doesNotMatch(html, /brfx-plan-four/);
  assert.match(html, /Historia EUR\/USD/);
});

test("OPEN uses only the real position direction, entry price and timestamp", async () => {
  const html = await render({
    status:"OPEN",
    metadata:{position:{
      status:"OPEN", direction:"LONG",
      entry:1.12345, stop:1.12000, target:1.13000,
      mark_price:1.12450,
      opened_at:"2026-10-29T10:35:00Z",
      expires_at:"2026-10-30T13:35:00Z"
    }}
  });
  assert.match(html, /<strong>OPEN<\/strong>/);
  assert.match(html, /brfx-position-side">LONG/);
  assert.match(html, /1,12345/);
  assert.match(html, /2026/);
  assert.match(html, /10:35/);
  assert.doesNotMatch(html, /Bieżący kandydat|Dlaczego brak pozycji|confidence|score/);
  assert.match(html, /brfx-plan-four/);
});

test("OPEN missing entry price is a dash, never fabricated zero", async () => {
  const html = await render({
    status:"OPEN",metadata:{position:{
      status:"OPEN",direction:"SHORT",entry:null,
      opened_at:null,stop:null,target:null,mark_price:null
    }}
  });
  assert.match(html, /<strong>OPEN<\/strong>/);
  assert.doesNotMatch(html, /0,00000/);
  assert.doesNotMatch(html, /-100,00%/);
  assert.match(html, /Wejście: <b>—<\/b>/);
});

test("EN uses mirrored labels", async () => {
  const html = await render({status:"NO_TRADE",metadata:{}}, undefined, "en");
  assert.match(html, /No open position/);
  assert.doesNotMatch(html, /Why no position|Current candidate/);
});

test("Daily does not load Fed/ECB source-document panel, Weekly untouched", () => {
  for (const page of [pl,en]) {
    assert.doesNotMatch(page, /<script src="\/scripts\/eurusd-cb-risk-public\.js/);
    assert.match(page, /daily-eurusd-spot-public\.js\?v=10-simple-status/);
  }
});
