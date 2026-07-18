const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync('scripts/investments-weekly-public.js', 'utf8');
const week = JSON.parse(fs.readFileSync('data/investments/weekly/2026-W29.json', 'utf8'));

function signed(value, digits) {
  return (value >= 0 ? '+' : '') + Number(value).toLocaleString('pl-PL', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function expectedResult(item) {
  const parts = [`${signed(item.result_value, 2)} USD`];
  if (item.instrument_id === 'eurusd') parts.push(`${signed(item.result_units, 1)} pips`);
  if (item.instrument_id === 'sp500_futures') parts.push(`${signed(item.result_units, 2)} pkt`);
  parts.push(`${signed(item.result_percent, 2)}%`);
  return parts.join(' · ');
}

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

async function renderWithLive(updatedAt) {
  const classes = new Set();
  const elements = {
    updated: {
      textContent: '',
      classList: {
        add(value) { classes.add(value); },
        toggle(value, enabled) { enabled ? classes.add(value) : classes.delete(value); },
      },
      setAttribute() {},
    },
    app: { innerHTML: '' },
  };
  const live = {
    updated_at: updatedAt,
    prices: {
      eurusd: { price: 1.14456 },
      sp500_futures: { price: 7607.5 },
      btcusd: { price: 64946.88 },
    },
  };
  const context = vm.createContext({
    console,
    document: { getElementById: (id) => elements[id] || null },
    fetch: async (url) => {
      if (url.startsWith('/data/investments/live_prices.json')) {
        return { ok: true, json: async () => live };
      }
      if (url.startsWith(`/data/investments/weekly/${week.week_id}.json`)) {
        return { ok: true, json: async () => week };
      }
      return { ok: false, json: async () => ({}) };
    },
    setTimeout,
    window: { BR_WEEKLY: { lang: 'pl' } },
  });
  vm.runInContext(script, context);
  await new Promise((resolve) => setTimeout(resolve, 25));
  return { elements, classes };
}

test('shows normalized investment results with units and notional', async () => {
  const { elements } = await renderWithLive(new Date().toISOString());
  const html = elements.app.innerHTML;
  assert.match(html, /<th>Tydzień<\/th>/);
  assert.match(html, /<dt>Nominał<\/dt>/);
  assert.match(html, /10(?:\s|&nbsp;|\u00a0)000 EUR/);
  for (const item of week.instruments.filter((entry) => entry.exit_price)) {
    assert.match(html, new RegExp(escapeRegex(expectedResult(item))));
  }
});

test('hides current prices when live data is stale', async () => {
  const { elements, classes } = await renderWithLive('2000-01-01T00:00:00Z');
  assert.match(elements.updated.textContent, /Dane rynkowe są opóźnione/);
  assert.equal(classes.has('stale'), true);
  assert.doesNotMatch(elements.app.innerHTML, /1,14456/);
  assert.doesNotMatch(elements.app.innerHTML, /7607,50/);
});
