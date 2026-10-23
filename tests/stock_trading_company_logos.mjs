import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { runInNewContext } from 'node:vm';

const rootDir = process.cwd();
const script = readFileSync(join(rootDir, 'scripts/stock-trading-company-logos-v2.js'), 'utf8');

class Classes {
  constructor(names = []) { this.items = new Set(names); }
  contains(name) { return this.items.has(name); }
  add(name) { this.items.add(name); }
  remove(name) { this.items.delete(name); }
  toggle(name, force) {
    if (force === undefined) force = !this.contains(name);
    if (force) this.add(name);
    else this.remove(name);
    return force;
  }
}
class Image {
  constructor(src) {
    this.src = src;
    this.listeners = {};
  }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  fire(type) { assert.ok(this.listeners[type], 'missing ' + type + ' event'); this.listeners[type](); }
}
class Mark {
  constructor(ticker, market) {
    this.dataset = {};
    this.classList = new Classes(['str-company-mark', market === 'US' ? 'is-us' : 'is-gpw']);
    this.ticker = ticker;
    this._html = '';
    this.image = null;
  }
  set innerHTML(value) {
    this._html = value;
    const match = /<img\b[^>]*\bsrc="([^"]+)"/.exec(value);
    this.image = match ? new Image(match[1]) : null;
  }
  get innerHTML() { return this._html; }
  querySelector(selector) {
    if (selector === '.br-company-logo-img') return this.image;
    if (selector === 'strong') return { textContent: this.ticker };
    return null;
  }
}
class Position {
  constructor(ticker, name, market, kind = 'str-position') {
    this.ticker = ticker;
    this.name = name;
    this.kind = kind;
    this.mark = new Mark(ticker, market);
    this.classList = new Classes([kind]);
  }
  querySelector(selector) {
    if (selector === '.str-company-mark') return this.mark;
    if (selector === '.str-company-name h3') return { textContent: this.ticker };
    if (selector === '.str-company-name span') return { textContent: this.name };
    return null;
  }
}

const positions = [
  new Position('ASO', 'Academy Sports and Outdoors, Inc.', 'US'),
  new Position('WMG', 'Warner Music Group Corp.', 'US', 'str-overview-position'),
  new Position('SAP', 'SAP SE ADS', 'US'),
  new Position('LPP', 'LPP Spółka Akcyjna', 'GPW'),
  new Position('SAP', 'Polska spółka o takim tickerze', 'GPW'),
  new Position('ZZZNEW', 'Unmapped USA issuer', 'US'),
];

const domRoot = {
  querySelectorAll(selector) {
    assert.equal(selector, '.str-position,.str-overview-position');
    return positions;
  }
};
const context = {
  document: {
    getElementById(id) {
      assert.equal(id, 'stock-trading-portfolio-root');
      return domRoot;
    },
    addEventListener() {}
  },
  window: { addEventListener() {} },
  MutationObserver: class { observe() {} },
  requestAnimationFrame(callback) { callback(); return 0; }
};
runInNewContext(script, context, { filename: 'stock-trading-company-logos-v2.js' });

const expected = [
  ['ASO', 'academy.com'],
  ['WMG', 'wmg.com'],
  ['SAP', 'sap.com'],
  ['LPP', 'lppsa.com']
];
for (const [index, pair] of expected.entries()) {
  const [ticker, domain] = pair;
  const mark = positions[index].mark;
  assert.ok(mark.classList.contains('br-company-logo-v2'), ticker + ': component not upgraded');
  assert.ok(mark.image, ticker + ': expected image, got letter fallback');
  assert.equal(mark.image.src, 'https://logo.clearbit.com/' + domain + '?size=192');
  assert.ok(mark.innerHTML.includes('<strong>' + ticker + '</strong>'));
  assert.equal(mark.classList.contains('is-logo-fallback'), false);
}

const us = positions[0].mark;
us.image.fire('error');
assert.equal(us.image.src, 'https://www.google.com/s2/favicons?sz=128&domain_url=https://academy.com');
us.image.fire('error');
assert.equal(us.image.src, 'https://icons.duckduckgo.com/ip3/academy.com.ico');
us.image.fire('error');
assert.ok(us.classList.contains('is-logo-fallback'), 'must expose letters when all providers fail');
assert.ok(us.innerHTML.includes('br-company-logo-fallback'));
us.image.fire('load');
assert.equal(us.classList.contains('is-logo-fallback'), false, 'loaded logo should clear fallback');

for (const position of positions.slice(-2)) {
  assert.equal(position.mark.image, null, 'unknown/GPW ambiguous ticker must not fetch a US image');
  assert.ok(position.mark.classList.contains('is-logo-fallback'));
}
for (const route of ['pl/inwestycje/stock-trading.html', 'en/investing/stock-trading.html']) {
  const html = readFileSync(join(rootDir, route), 'utf8');
  assert.ok(html.includes('/scripts/stock-trading-company-logos-v2.js?v=20261022-logo-cache-1'));
  assert.ok(!html.includes('/scripts/stock-trading-company-logos-v2.js?v=20260918-4'));
}
console.log('PASS US logos: ASO, WMG, SAP; GPW isolated; source fallback; PL/EN cache version');


const cachePositions = [
  new Position('ABCD', 'Example issuer', 'US'),
  new Position('ABCD', 'Different Polish issuer', 'GPW')
];
const cacheContext = {
  document: {getElementById() {return {querySelectorAll(){return cachePositions;}};},addEventListener() {}},
  window: {addEventListener() {}},
  MutationObserver: class {observe() {}},
  requestAnimationFrame(callback) {callback(); return 0;},
  fetch(url, options) {
    assert.equal(url, '/data/investments/company_logo_cache.json');
    assert.equal(options.cache, 'no-cache');
    return Promise.resolve({ok:true, json:async()=>({
      schema_version:1,
      entries:{
        'US:ABCD':{market:'US',ticker:'ABCD',path:'/assets/company-logos/us/abcd.png'},
        'GPW:ABCD':{market:'GPW',ticker:'ABCD',path:'/assets/company-logos/us/abcd.png'}
      }
    })});
  }
};
runInNewContext(script,cacheContext);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(cachePositions[0].mark.image.src,'/assets/company-logos/us/abcd.png');
assert.equal(cachePositions[1].mark.image,null,'must not use US logo on GPW');
cachePositions[0].mark.image.fire('error');
assert.ok(cachePositions[0].mark.classList.contains('is-logo-fallback'));
console.log('PASS logo cache: async upgrade, scoped, error fallback');
