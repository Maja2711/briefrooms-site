// Node built-ins only: verifies actual rendered W46 publication, not just regexes.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const load = (p) => JSON.parse(fs.readFileSync(path.join(ROOT, p), 'utf8'));
const SCRIPT = fs.readFileSync(
  path.join(ROOT, 'scripts/investments-weekly-public.js'), 'utf8');

async function renderWeekly() {
  const week = load('data/investments/weekly/2026-W46.json');
  const correction = load('data/investments/public_quarantine.json');
  const live = load('data/investments/live_prices.json');
  const app = { innerHTML: '' };
  const updated = {
    textContent: '',
    classList: { add() {}, toggle() {} },
    setAttribute() {},
  };
  const fixtures = {
    '/data/investments/weekly/2026-W46.json': week,
    '/data/investments/public_quarantine.json': correction,
    '/data/investments/live_prices.json': live,
  };
  const document = {
    hidden: false,
    getElementById: (id) => ({ app, updated })[id] || null,
    dispatchEvent() {},
    addEventListener() {},
  };
  const window = { BR_WEEKLY: { lang: 'pl' } };
  const context = {
    window, document, Date, console,
    CustomEvent: class { constructor(type, opts) { this.type = type; this.detail = opts?.detail; } },
    setInterval() {},
    fetch: async (url) => {
      const key = String(url).split('?')[0];
      return {
        ok: Object.hasOwn(fixtures, key),
        json: async () => fixtures[key],
      };
    },
  };
  vm.runInNewContext(SCRIPT, context, {
    filename: 'investments-weekly-public.js',
    timeout: 3000,
  });
  await new Promise(setImmediate); // Let Promise.all over the weekly fixtures finish.
  return { week, correction, app, window };
}

test('W46 EURUSD is public NO TRADE, excluded from aggregate and history', async () => {
  const { week, correction, app, window } = await renderWeekly();
  const eur = week.instruments.find((row) => row.instrument_id === 'eurusd');
  assert.ok(eur);
  assert.equal(eur.trade_status, 'closed'); // Original immutable history unchanged.
  assert.equal(correction.records.length, 1);
  const state = window.BR_WEEKLY_INTEGRITY.auditState(
    week, eur, new Map([['2026-W46/eurusd', correction.records[0]]]));
  assert.equal(state.correctedNoTrade, true);
  assert.equal(state.withheld, true); // Excluded from all computed P/L totals.
  assert.match(app.innerHTML, /<h2>Wybrany tydzień: 2026-W46<\/h2>/);
  assert.match(app.innerHTML, /<h3>NO TRADE<\/h3>/);
  assert.match(app.innerHTML, /Błędne rozliczenie/);
  assert.doesNotMatch(app.innerHTML, /Wynik wstrzymany/);
  const body = app.innerHTML.match(/<tbody>([\s\S]*?)<\/tbody>/)?.[1] || '';
  assert.doesNotMatch(body, /2026-W46/); // No invalid EURUSD fill in history.
  assert.match(body, /colspan="6"/);
});

test('publishing NO TRADE is impossible without an actual pre-entry settlement', async () => {
  const { week, correction, window } = await renderWeekly();
  const eur = { ...week.instruments.find((x) => x.instrument_id === 'eurusd'),
    exit_captured_at: '2026-11-14T00:02:32+01:00' };
  const state = window.BR_WEEKLY_INTEGRITY.auditState(
    week, eur, new Map([['2026-W46/eurusd', correction.records[0]]]));
  assert.equal(state.correctedNoTrade, undefined);
  assert.equal(state.withheld, true);
});

test('W46 correction is instrument-scoped; BTC and S&P remain untouched', async () => {
  const { correction } = await renderWeekly();
  assert.deepEqual(correction.records.map(({ week_id, instrument_id }) =>
    `${week_id}/${instrument_id}`), ['2026-W46/eurusd']);
});
