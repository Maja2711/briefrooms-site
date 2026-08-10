const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync('scripts/investments-weekly-public.js', 'utf8');

function isoWeek(date) {
  const x = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const day = x.getUTCDay() || 7;
  x.setUTCDate(x.getUTCDate() + 4 - day);
  const year = new Date(Date.UTC(x.getUTCFullYear(), 0, 1));
  return `${x.getUTCFullYear()}-W${String(Math.ceil((((x - year) / 86400000) + 1) / 7)).padStart(2, '0')}`;
}

function validWeek() {
  return {
    week_id: isoWeek(new Date()),
    forecast_created_at: new Date().toISOString(),
    instruments: [{
      instrument_id: 'eurusd',
      symbol: 'EURUSD=X',
      label_pl: 'EUR/USD',
      label_en: 'EUR/USD',
      direction: 'short',
      trade_status: 'closed',
      entry_price: 1.15,
      entry_captured_at: '2026-07-31T10:00:00+02:00',
      exit_price: 1.14,
      exit_captured_at: '2026-07-31T11:00:00+02:00',
      exit_reason: 'take_profit',
      notional_eur: 10000,
      result_value: 100,
      result_units: 100,
      result_percent: 0.869565,
      risk_plan: {
        stop_loss_price: 1.16,
        take_profit_price: 1.14,
        reward_to_risk: 1,
      },
    }],
  };
}

async function renderWithLive({ updatedAt, week = validWeek(), quarantine = { records: [] } }) {
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
  const window = { BR_WEEKLY: { lang: 'pl' } };
  const context = vm.createContext({
    console,
    document: {
      hidden: false,
      getElementById: (id) => elements[id] || null,
      addEventListener() {},
      dispatchEvent() {},
    },
    fetch: async (url) => {
      if (url.startsWith('/data/investments/live_prices.json')) return { ok: true, json: async () => live };
      if (url.startsWith('/data/investments/public_quarantine.json')) return { ok: true, json: async () => quarantine };
      if (url.startsWith(`/data/investments/weekly/${week.week_id}.json`)) return { ok: true, json: async () => week };
      return { ok: false, json: async () => ({}) };
    },
    setTimeout,
    setInterval() { return 1; },
    CustomEvent: class CustomEvent {
      constructor(type, init) { this.type = type; this.detail = init?.detail; }
    },
    window,
  });
  vm.runInContext(script, context);
  await new Promise((resolve) => setTimeout(resolve, 30));
  return { elements, classes, window };
}

test('shows normalized investment results with units and notional', async () => {
  const { elements } = await renderWithLive({ updatedAt: new Date().toISOString() });
  const html = elements.app.innerHTML;
  assert.match(html, /<th>Tydzień<\/th>/);
  assert.match(html, /<dt>Nominał<\/dt>/);
  assert.match(html, /10(?:\s|&nbsp;|\u00a0)000 EUR/);
  assert.match(html, /\+100,00 USD · \+100,0 pips · \+0,87%/);
  assert.doesNotMatch(html, /DANE W AUDYCIE/);
});

test('publishes current BTC percentage result units', async () => {
  const week = validWeek();
  week.method_version = '5.0.0-experimental';
  week.instruments = [{
    instrument_id: 'btcusd',
    symbol: 'BTC-USD',
    label_pl: 'BTC/USD',
    label_en: 'BTC/USD',
    direction: 'short',
    trade_status: 'closed',
    entry_price: 200,
    entry_captured_at: '2026-08-03T10:00:00+02:00',
    exit_price: 190,
    exit_captured_at: '2026-08-07T22:00:00+02:00',
    exit_reason: 'scheduled_week_close',
    notional_usd: 10000,
    result_value: 500,
    result_units: 5,
    result_percent: 5,
  }];
  const { elements } = await renderWithLive({ updatedAt: new Date().toISOString(), week });
  assert.doesNotMatch(elements.app.innerHTML, /DANE W AUDYCIE/);
  assert.match(elements.app.innerHTML, /\+500,00 USD · \+5,00%/);
});

test('preserves reconstructed BTC price-move result units', async () => {
  const week = validWeek();
  week.method_version = '5.0.1-reconstructed';
  week.instruments = [{
    instrument_id: 'btcusd',
    symbol: 'BTC-USD',
    label_pl: 'BTC/USD',
    label_en: 'BTC/USD',
    direction: 'short',
    trade_status: 'closed',
    entry_price: 200,
    entry_captured_at: '2026-07-27T10:00:00+02:00',
    exit_price: 190,
    exit_captured_at: '2026-07-31T22:00:00+02:00',
    exit_reason: 'scheduled_week_close',
    notional_usd: 10000,
    result_value: 500,
    result_units: 10,
    result_percent: 5,
  }];
  const { elements } = await renderWithLive({ updatedAt: new Date().toISOString(), week });
  assert.doesNotMatch(elements.app.innerHTML, /DANE W AUDYCIE/);
  assert.match(elements.app.innerHTML, /\+500,00 USD · \+5,00%/);
});

test('marks stored current prices as delayed when live data is stale', async () => {
  const { elements, classes } = await renderWithLive({ updatedAt: '2000-01-01T00:00:00Z' });
  assert.match(elements.updated.textContent, /Dane rynkowe są opóźnione/);
  assert.equal(classes.has('stale'), true);
  assert.match(elements.app.innerHTML, /1,14456/);
});

test('withholds a record when exit precedes entry', async () => {
  const week = validWeek();
  week.instruments[0].exit_captured_at = '2026-07-30T09:00:00+02:00';
  const { elements, window } = await renderWithLive({ updatedAt: new Date().toISOString(), week });
  assert.match(elements.app.innerHTML, /DANE W AUDYCIE/);
  assert.match(elements.app.innerHTML, /Wynik wstrzymany/);
  assert.match(elements.app.innerHTML, /\+0,00 USD/);
  assert.deepEqual(Array.from(window.BR_WEEKLY_INTEGRITY.integrityIssues(week.instruments[0])), ['exit_before_entry']);
});

test('honors explicit public quarantine even when arithmetic is valid', async () => {
  const week = validWeek();
  const quarantine = {
    records: [{
      week_id: week.week_id,
      instrument_id: 'eurusd',
      public_status: 'withheld',
      reason: 'Manual reconciliation pending.',
    }],
  };
  const { elements } = await renderWithLive({ updatedAt: new Date().toISOString(), week, quarantine });
  assert.match(elements.app.innerHTML, /DANE W AUDYCIE/);
  assert.match(elements.app.innerHTML, /Manual reconciliation pending/);
  assert.doesNotMatch(elements.app.innerHTML, /\+100,00 USD · \+100,0 pips/);
});

test('publishes an explicitly approved arithmetic stop settlement', async () => {
  const week = validWeek();
  week.instruments[0].exit_captured_at = '2026-07-30T09:00:00+02:00';
  const quarantine = {
    records: [{
      week_id: week.week_id,
      instrument_id: 'eurusd',
      public_status: 'withheld',
      manual_public_result: true,
      entry_price: 1.15,
      exit_price: 1.16,
      result_value: -100,
      result_units: -100,
      result_percent: -0.869565,
      reason: 'Approved arithmetic settlement.',
    }],
  };
  const { elements } = await renderWithLive({ updatedAt: new Date().toISOString(), week, quarantine });
  assert.doesNotMatch(elements.app.innerHTML, /DANE W AUDYCIE/);
  assert.match(elements.app.innerHTML, /-100,00 USD · -100,0 pips · -0,87%/);
  assert.match(elements.app.innerHTML, /<strong>0 \/ 1<\/strong>/);
});

test('planned directional entry is not withheld before entry deadline', async () => {
  const week = validWeek();
  week.method_version = '5.3.0-experimental';
  week.market_window = { entry_latest_local: '2099-08-10T10:00:00+02:00' };
  week.instruments = [{
    instrument_id: 'sp500_futures',
    symbol: 'ES=F',
    label_pl: 'S&P 500 FUTURES',
    label_en: 'S&P 500 FUTURES',
    direction: 'long',
    trade_status: 'planned',
    entry_price: null,
    pending_entry_decision: {
      decided_at: '2026-08-10T08:58:59+02:00',
      entry_not_before: '2026-08-10T08:58:59+02:00',
      decision: { direction: 'long' },
    },
  }];
  const { elements, window } = await renderWithLive({ updatedAt: new Date().toISOString(), week });
  assert.doesNotMatch(elements.app.innerHTML, /DANE W AUDYCIE/);
  assert.match(elements.app.innerHTML, /oczekuje na otwarcie/);
  assert.deepEqual(Array.from(window.BR_WEEKLY_INTEGRITY.integrityIssues(week.instruments[0], week.method_version, week)), []);
});

test('planned directional entry is withheld after entry deadline', async () => {
  const week = validWeek();
  week.method_version = '5.3.0-experimental';
  week.market_window = { entry_latest_local: '2000-08-10T10:00:00+02:00' };
  week.instruments = [{
    instrument_id: 'sp500_futures',
    symbol: 'ES=F',
    label_pl: 'S&P 500 FUTURES',
    label_en: 'S&P 500 FUTURES',
    direction: 'long',
    trade_status: 'planned',
    entry_price: null,
    pending_entry_decision: {
      decided_at: '2026-08-10T08:58:59+02:00',
      entry_not_before: '2026-08-10T08:58:59+02:00',
      decision: { direction: 'long' },
    },
  }];
  const { elements, window } = await renderWithLive({ updatedAt: new Date().toISOString(), week });
  assert.match(elements.app.innerHTML, /DANE W AUDYCIE/);
  assert.deepEqual(Array.from(window.BR_WEEKLY_INTEGRITY.integrityIssues(week.instruments[0], week.method_version, week)), ['directional_missing_entry']);
});
