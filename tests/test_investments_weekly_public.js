const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync('scripts/investments-weekly-public.js', 'utf8');
const week = JSON.parse(fs.readFileSync('data/investments/weekly/2026-W29.json', 'utf8'));

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
  assert.match(html, /-47,00 USD · -47,0 pips · -0,41%/);
  assert.match(html, /\+38,60 USD · \+29,25 pkt · \+0,39%/);
});

test('hides current prices when live data is stale', async () => {
  const { elements, classes } = await renderWithLive('2000-01-01T00:00:00Z');
  assert.match(elements.updated.textContent, /Dane rynkowe są opóźnione/);
  assert.equal(classes.has('stale'), true);
  assert.doesNotMatch(elements.app.innerHTML, /\+38,60 USD/);
});
