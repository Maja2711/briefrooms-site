import test from "node:test";
import assert from "node:assert/strict";
import { finiteNumber, yahooMinuteBars } from "../src/index.js";

test("finiteNumber rejects missing market values instead of fabricating zero", () => {
  assert.equal(finiteNumber(null), null);
  assert.equal(finiteNumber(undefined), null);
  assert.equal(finiteNumber(""), null);
  assert.equal(finiteNumber("   "), null);
  assert.equal(finiteNumber(0), 0);
  assert.equal(finiteNumber("1.12633"), 1.12633);
});

test("Yahoo null OHLC cells cannot become a false EURUSD TP/SL price", () => {
  const payload = {
    chart: {
      result: [{
        timestamp: [1791158400, 1791158460],
        indicators: {
          quote: [{
            open: [null, 1.1262],
            high: [null, null],
            low: [null, null],
            close: [null, 1.1263],
          }],
        },
      }],
    },
  };

  const rows = yahooMinuteBars(payload);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].close, 1.1263);
  assert.equal(rows[0].high, null);
  assert.equal(rows[0].low, null);
  assert.notEqual(rows[0].low, 0);
});
