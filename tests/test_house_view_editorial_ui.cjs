/* DOM smoke test for the frozen November v2 snapshot and editorial v2.1 overlay.
 * Does not require a browser, network access, or modifications to the archive. */
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const script = fs.readFileSync('scripts/house-view-monthly.js', 'utf8');
const raw = fs.readFileSync('data/investments/house_view_monthly_archive/2026-11.json', 'utf8');
const frozen = JSON.parse(raw);
assert.equal(frozen.month, '2026-11');
assert.equal(frozen.schema_version, '2.0');
assert.equal(frozen.inputs.spx_futures_20d_return_pct, -5.82);

class FakeElement {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.className = '';
    this.textContent = '';
    this.open = false;
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...nodes) { this.children = [...nodes]; }
}

const visible = (element) => {
  if (element.tagName === 'details' && !element.open) {
    return element.children.filter(child => child.tagName === 'summary').map(visible).join(' ');
  }
  return [element.textContent, ...element.children.map(visible)].filter(Boolean).join(' ');
};

async function render(lang, payload) {
  const mount = new FakeElement('div'), legacy = new FakeElement('details');
  const doc = {
    documentElement: { lang },
    createElement: tag => new FakeElement(tag),
    querySelector: selector => selector === '[data-house-view-monthly]' ? mount
      : selector === '[data-house-view-legacy]' ? legacy : null
  };
  let requested = null;
  const context = {
    document: doc, Date, Promise, URL, Number, Math, Object,
    fetch: async (url) => {
      requested = url;
      return { ok: true, json: async () => payload };
    }
  };
  vm.runInNewContext(script, context, { timeout: 2500 });
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.match(requested, /^\/data\/investments\/house_view_monthly_current\.json\?v=/);
  return { text: visible(mount), mount, legacy };
}

(async () => {
  const archiveBefore = JSON.stringify(frozen);
  const pl = await render('pl', frozen);
  assert.match(pl.text, /S&P 500 — w horyzoncie 1M przeważa ryzyko dalszej korekty/);
  assert.match(pl.text, /S&P 500 futures straciły 5,82% w ostatnich 20 sesjach/);
  assert.match(pl.text, /US 10Y: 4,86%; Brent: 107,03 USD\/bbl/);
  assert.match(pl.text, /Nie podnosimy|nie podnosimy tezy długoterminowej/);
  assert.match(pl.text, /Co zmieni naszą tezę\?/);
  assert.match(pl.text, /Warunki odbicia/);
  assert.match(pl.text, /Interpretacja rynkowa v2\.1/);
  assert.doesNotMatch(pl.text, /structural_caution|GSE, FSE i BRACE są odczytywane|Brak aktualnych, potwierdzonych danych indeksowych|ABSTAIN/);
  assert.doesNotMatch(pl.text, /Bilans czynników: -3|Siła oceny \(limit heurystyczny\): 3\/10/);
  assert.equal(pl.legacy.open, false);
  const details = pl.mount.children[0].children.filter(x => x.tagName === 'details');
  assert.equal(details.length, 2);
  assert.equal(details.every(x => !x.open), true);
  assert.equal(JSON.stringify(frozen), archiveBefore, 'UI must not mutate frozen source');

  const en = await render('en', frozen);
  assert.match(en.text, /correction or weak consolidation risk dominates/);
  assert.match(en.text, /S&P 500 futures lost 5\.82% over 20 sessions/);
  assert.match(en.text, /Earnings and valuations — conditions for a rally/);
  assert.match(en.text, /Methods, evidence and limitations/);
  assert.doesNotMatch(en.text, /structural_caution|ABSTAIN/);

  const rebound = JSON.parse(raw);
  rebound.views['1M'].bias = 'mild_positive';
  rebound.inputs.spx_futures_20d_return_pct = 4.3;
  const positive = await render('pl', rebound);
  assert.match(positive.text, /przewaga argumentów za umiarkowanym odbiciem/);
  assert.match(positive.text, /S&P 500 futures wzrosły 4,30%/);
  assert.doesNotMatch(positive.text, /futures straciły/);

  const unavailable = JSON.parse(raw);
  delete unavailable.inputs.core_pce_yoy_pct;
  const failed = await render('pl', unavailable);
  assert.match(failed.text, /Brak opublikowanej i zweryfikowanej analizy/);
  assert.equal(failed.legacy.open, true);
  console.log('House View investor-first PL/EN archive overlay and fail-closed DOM smoke tests: OK');
})().catch(err => { console.error(err); process.exitCode = 1; });
