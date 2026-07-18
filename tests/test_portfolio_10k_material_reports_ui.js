'use strict';

const assert = require('node:assert/strict');
const renderer = require('../scripts/portfolio-10k-material-reports-public.js');

function report(id, publishedAt) {
  return {
    id, position_id:'googl', symbol:'GOOGL.US', published_at:publishedAt,
    event_date:publishedAt.slice(0,10), type:'EARNINGS', severity:'HIGH', impact:'POSITIVE',
    title_pl:'Wyniki Alphabet', title_en:'Alphabet results',
    summary_pl:'Przychody wzrosły.', summary_en:'Revenue increased.',
    thesis_effect_pl:null, thesis_effect_en:null, model_action:'HOLD',
    quote:null, position_snapshot:null,
    sources:[{label:'Alphabet IR',url:'https://abc.xyz/investor/'}],
  };
}

const emptyPl = renderer.renderForPosition({reports:[],position:{id:'googl'},lang:'pl'});
assert.match(emptyPl, /Istotne raporty/);
assert.match(emptyPl, /Brak nowych istotnych wydarzeń/);
const emptyEn = renderer.renderForPosition({reports:[],position:{id:'googl'},lang:'en'});
assert.match(emptyEn, /Material reports/);
assert.match(emptyEn, /No new material events/);

const reports = [1,2,3,4,5].map(day => report(`r${day}`, `2026-07-${20+day}T20:00:00Z`));
const selected = renderer.reportsForPosition(reports, 'googl');
assert.equal(selected[0].id, 'r5');
const history = renderer.renderForPosition({reports,position:{id:'googl'},lang:'en'});
assert.equal((history.match(/<article class="material-report/g) || []).length, 5);
assert.equal((history.split('<details class="material-reports__history">').shift().match(/<article class="material-report/g) || []).length, 3);
assert.match(history, /Older reports \(2\)/);

const unsafe = report('unsafe','2026-07-22T20:00:00Z');
unsafe.title_en = '<img src=x onerror=alert(1)>';
unsafe.summary_en = '<script>alert(1)</script>';
unsafe.sources = [{label:'<b>source</b>',url:'javascript:alert(1)'}];
const escaped = renderer.renderReport(unsafe,'en');
assert.doesNotMatch(escaped, /<img|<script|javascript:/);
assert.match(escaped, /&lt;img/);
assert.doesNotMatch(escaped, /material-report__sources/);

const safe = report('safe','2026-07-22T20:00:00Z');
const safeHtml = renderer.renderReport(safe,'en');
assert.match(safeHtml, /target="_blank"/);
assert.match(safeHtml, /rel="noopener noreferrer"/);
assert.doesNotMatch(safeHtml, /undefined|null/);

const full = report('full','2026-07-22T20:00:00Z');
full.quote = {value:107,currency:'USD',kind:'LAST',market:'NASDAQ',quoted_at:'2026-07-22T20:00:00Z',source:'market'};
full.position_snapshot = {
  quantity:2, cost_basis_local:200, market_value_local:214, unrealized_pnl_local:14,
  unrealized_pnl_percent:0.07, position_currency:'USD', cost_basis_pln:804,
  market_value_pln:877.4, unrealized_pnl_pln:73.4, instrument_effect_pln:56, fx_effect_pln:21.4,
};
const fullHtml = renderer.renderReport(full,'en');
assert.match(fullHtml, /material-report__quote/);
assert.match(fullHtml, /NASDAQ/);
assert.match(fullHtml, /Instrument effect/);
assert.match(fullHtml, /FX effect/);

for (const kind of ['BID','ASK','LAST','CLOSE','INDICATIVE']) {
  const current = report(kind,'2026-07-22T20:00:00Z');
  current.quote = {value:107,currency:'USD',kind,market:null,quoted_at:'2026-07-22T20:00:00Z',source:'market'};
  const html = renderer.renderReport(current,'en');
  assert.match(html, new RegExp(kind));
  if (kind === 'INDICATIVE') assert.match(html, /indicative price/);
}

assert.equal(renderer.safeHttpsUrl('data:text/html,boom'), null);
assert.equal(renderer.safeHttpsUrl('javascript:alert(1)'), null);
assert.match(renderer.safeHttpsUrl('https://example.com/a'), /^https:/);

console.log('Portfolio 10K material-report UI tests passed');
