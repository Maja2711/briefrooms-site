/* House View homepage projection regression: frozen truth, monthly rollover,
 * PL/EN parity, stale/future fail-closed behavior, and unique runtime wiring.
 */
'use strict';
const fs = require('node:fs');
const assert = require('node:assert/strict');

const model = require('../scripts/house-view-home-summary.js');
const raw = fs.readFileSync('data/investments/house_view_monthly_archive/2026-11.json','utf8');
const november = JSON.parse(raw);
const atNov = Date.parse('2026-11-15T10:00:00Z');
const pl = model.create(november,'pl',atNov);
const en = model.create(november,'en',atNov);

assert.equal(november.month,'2026-11');
assert.equal(november.as_of,'2026-11-10');
assert.equal(november.views['1M'].bias,'defensive');
assert.equal(november.views['6M'].bias,'neutral');
assert.equal(november.views['12M'].bias,'neutral');
assert.match(pl.title,/ryzyka korekty/);
assert.match(pl.desc,/20 sesji: -5,82%/);
assert.match(pl.desc,/4,86%/);
assert.match(pl.desc,/3,12%/);
assert.deepEqual(pl.metrics,[
  {label:'1M',value:'defensywny'},
  {label:'6M',value:'neutralny'},
  {label:'12M',value:'neutralny'}
]);
assert.match(pl.meta,/10\.11\.2026/);
assert.equal(pl.stale,false);
assert.match(en.title,/correction risk dominates/);
assert.match(en.desc,/20 sessions: -5\.82%/);
assert.deepEqual(en.metrics.map(v=>v.value),['defensive','neutral','neutral']);
assert.match(en.meta,/10\/11\/2026/);
assert.doesNotMatch(pl.title+' '+pl.desc,/Wzrost odporny|październik|structural_caution|ABSTAIN|GSE działa/);

const december = JSON.parse(raw);
december.month='2026-12';
december.as_of='2026-12-10';
december.views['1M'].bias='mild_positive';
december.views['6M'].bias='mild_positive';
december.views['12M'].bias='defensive';
december.inputs.spx_futures_20d_return_pct=5.37;
december.inputs.us_10y_pct=3.62;
const updated=model.create(december,'pl',Date.parse('2026-12-15T12:00:00Z'));
assert.match(updated.title,/umiarkowana przewaga scenariusza odbicia/i);
assert.match(updated.desc,/\+5,37%/);
assert.match(updated.desc,/3,62%/);
assert.deepEqual(updated.metrics.map(x=>x.value),['lekko pozytywny','lekko pozytywny','defensywny']);
assert.match(updated.meta,/10\.12\.2026/);
assert.notEqual(updated.title,pl.title);

const stale=model.create(november,'pl',Date.parse('2027-03-10T12:00:00Z'));
assert.equal(stale.stale,true);
assert.equal(stale.status,'ARCHIWUM');
assert.equal(stale.metrics.length,0);
assert.doesNotMatch(stale.desc,/-5,82%|4,86%/);
assert.match(stale.meta,/10\.11\.2026/);

const malformed=JSON.parse(raw);
delete malformed.views['6M'].bias;
assert.equal(model.create(malformed,'pl',atNov),null);
const absent=JSON.parse(raw);
delete absent.inputs.core_pce_yoy_pct;
assert.equal(model.create(absent,'pl',atNov),null);
const future=JSON.parse(raw);
future.as_of='2026-11-30';
assert.equal(model.create(future,'pl',atNov),null);
const mismatch=JSON.parse(raw);
mismatch.month='2026-10';
assert.equal(model.create(mismatch,'pl',atNov),null);
assert.equal(raw,fs.readFileSync('data/investments/house_view_monthly_archive/2026-11.json','utf8'));
const current = JSON.parse(fs.readFileSync('data/investments/house_view_monthly_current.json','utf8'));
assert.ok(model.create(current,'pl',Math.max(Date.now(),Date.parse(current.as_of+'T12:00:00Z'))));

const runtime=fs.readFileSync('scripts/home-lab.js','utf8');
assert.match(runtime,/house_view_monthly_current\.json/);
assert.match(runtime,/BriefRoomsHouseViewHome/);
assert.doesNotMatch(runtime,/doc\.querySelector\('\.lv-snapshot'\)/);
for(const path of ['pl/index.html','en/index.html']){
  const html=fs.readFileSync(path,'utf8');
  assert.equal((html.match(/\/scripts\/home-lab\.js\?v=20261115-1/g)||[]).length,1);
  assert.equal((html.match(/\/scripts\/house-view-home-summary\.js\?v=20261115-1/g)||[]).length,1);
  assert.doesNotMatch(html,/\/scripts\/home-lab-v5\.js/);
  assert.ok(html.indexOf('/scripts/house-view-home-summary.js')<html.indexOf('/scripts/home-lab.js'));
  assert.equal((html.match(/id="home-lab-root"/g)||[]).length,1);
}
console.log('House View homepage monthly source-of-truth, November rollover and PL/EN checks: OK');
