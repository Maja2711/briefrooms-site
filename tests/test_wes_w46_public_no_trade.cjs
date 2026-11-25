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

async function renderWeekly(overrideWeek = null, lang = 'pl') {
  const week = overrideWeek || load('data/investments/weekly/2026-W46.json');
  const correction = load('data/investments/public_quarantine.json');
  const live = load('data/investments/live_prices.json');
  const app = { innerHTML: '' };
  const updated = {
    textContent: '',
    classList: { add() {}, toggle() {} },
    setAttribute() {},
  };
  const fixtures = {
    [`/data/investments/weekly/${week.week_id}.json`]: week,
    '/data/investments/public_quarantine.json': correction,
    '/data/investments/live_prices.json': live,
  };
  const document = {
    hidden: false,
    getElementById: (id) => ({ app, updated })[id] || null,
    dispatchEvent() {},
    addEventListener() {},
  };
  const window = { BR_WEEKLY: { lang } };
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

function closedEurFixture({pending = true, future = false, direction = 'short', blocked = false, bad = false} = {}) {
  // Fixed regression data representing the real W48 execution, independent
  // of subsequent scheduled production changes.
  const week = {week_id: '2026-W48', method_version: '5.9.1', instruments: [
    {instrument_id:'eurusd', label_pl:'EUR/USD', label_en:'EUR/USD', direction:'short',
     trade_status:pending?'pending':'closed', wes_status:'no_trade_monitoring_trigger',
     entry_price:1.0984183549880981, exit_price:1.0974539518356323,
     entry_captured_at:'2026-11-23T10:47:47+01:00',
     exit_captured_at:bad?'2026-11-22T18:35:00+01:00':'2026-11-24T18:35:00+01:00',
     result_value:9.64403152,result_units:9.64403152,result_percent:0.0878,notional_eur:10000,
     risk_plan:{direction:'short',stop_loss_price:1.10602325,take_profit_price:1.08191835,reward_to_risk:2.1697},
     next_entry_status:blocked?'blocked_by_central_bank_event_risk':'waiting_for_market_entry',
     pending_entry_decision:pending?{decided_at:'2026-11-25T05:33:37+01:00',
       entry_not_before:'2026-11-25T05:33:37+01:00',decision:{direction},
       entry_price_plan:{target_price:1.10103,execution_mode:'market_now',
         expires_at:future?'2099-01-01T00:00:00Z':'2020-01-01T00:00:00Z'}}:null,
     wes_entry_authorization:{directional_admission_passed:true,expires_at:future?'2099-01-01T00:00:00Z':'2020-01-01T00:00:00Z'}},
    {instrument_id:'sp500_futures',label_pl:'S&P 500 FUTURES',direction:'neutral',trade_status:'no_trade'},
    {instrument_id:'btcusd',label_pl:'BTC/USD',direction:'neutral',trade_status:'no_trade'}]};
  return week;
}
const eurCard = app => app.innerHTML.match(/<article class="card short" data-executed-state="closed">[\s\S]*?<\/article>/)?.[0] || '';
test('W48 expired reentry keeps actual closed position and separate plan in PL and EN', async () => {
  for (const lang of ['pl','en']) {
    const {app} = await renderWeekly(closedEurFixture({direction:'long'}),lang);
    const card = eurCard(app);
    assert.ok(card,app.innerHTML);
    assert.match(card, /<h3>SHORT<\/h3>/);
    assert.match(card, /<dd>1[,.]09842<\/dd>/);
    assert.match(card, /<dd>1[,.]09745<\/dd>/);
    assert.match(card, /<dt>Status<\/dt><dd>(zamknięta|closed)<\/dd>/);
    assert.match(card, /\+9[,.]64 USD/);
    assert.match(card, /<dt>(Ponowne wejście|Re-entry)<\/dt><dd>(Plan wejścia wygasł|Entry plan expired)/);
    assert.doesNotMatch(card, /Poprzedni wynik|Previous result/);
    assert.match(card, /1[,.]10602/);
    assert.match(card, /1[,.]08192/);
  }
});
test('live MARKET, CB block and monitoring never replace EURUSD closed state', async () => {
  for (const [config, expected] of [
    [{future:true},/Oczekiwanie na potwierdzone wykonanie EPE/],
    [{future:true,blocked:true},/BLOKADA — ryzyko FED\/ECB/],
    [{pending:false},/Obserwacja — oczekiwanie na nowy sygnał/],
  ]) {
    const {app} = await renderWeekly(closedEurFixture(config));
    const card = eurCard(app);
    assert.ok(card);
    assert.match(card, /<dt>Status<\/dt><dd>zamknięta<\/dd>/);
    assert.match(card, expected);
  }
});
test('genuine invalid chronology remains withheld', async () => {
  const {app} = await renderWeekly(closedEurFixture({bad:true}));
  assert.equal(eurCard(app),'');
  assert.match(app.innerHTML,/exit_before_entry/);
});
test('WES overlay cannot replace closed EURUSD direction or status', () => {
  let touched = false;
  const card = {getAttribute:()=> 'closed',querySelector:()=>{touched=true;throw Error('closed card mutated');}};
  let listener;
  vm.runInNewContext(fs.readFileSync(path.join(ROOT,'scripts/investments-wes-public.js'),'utf8'), {
    window:{BR_WEEKLY:{lang:'pl'}},document:{querySelectorAll:()=>[card],addEventListener:(_,fn)=>{listener=fn;}}
  });
  listener({detail:{instruments:[{wes_status:'no_trade_monitoring_trigger'}]}});
  assert.equal(touched,false);
});
