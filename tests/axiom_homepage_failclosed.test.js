'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(
  path.join(__dirname, '../scripts/home-intelligence-layout.js'), 'utf8'
);

function element(tag) {
  return {
    tag, className: '', children: [], parentNode: null, textContent: '',
    setAttribute() {},
    append(...items) {
      for (const item of items) {
        item.parentNode = this;
        this.children.push(item);
      }
    },
    appendChild(item) { this.append(item); return item; },
    remove() {
      if (!this.parentNode) return;
      const siblings = this.parentNode.children;
      const index = siblings.indexOf(this);
      if (index >= 0) siblings.splice(index, 1);
      this.parentNode = null;
    },
    querySelector(selector) {
      if (!selector.startsWith('.')) return null;
      const className = selector.slice(1);
      const descendants = [...this.children];
      while (descendants.length) {
        const child = descendants.shift();
        if (child.className?.split(' ').includes(className)) return child;
        descendants.push(...child.children);
      }
      return null;
    }
  };
}

function run(fetcher) {
  const head = element('main');
  const styles = element('head');
  const document = {
    documentElement: { lang: 'pl' },
    body: element('body'),
    head: styles,
    getElementById() { return null; },
    querySelector(selector) { return selector === '.main-head' ? head : null; },
    createElement: element
  };
  const calls = [];
  vm.runInNewContext(script, {
    document, window: {},
    Date, Intl, console,
    fetch: (...args) => {
      calls.push(args[0]);
      return fetcher(...args);
    }
  });
  return { head, calls };
}

const today = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Europe/Warsaw',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit'
}).format(new Date());

const valid = {
  date: today,
  author: 'AXIOM',
  brand: 'BriefRooms',
  pl: '„Nowa, potwierdzona myśl opublikowana dzisiaj.”',
  en: '“A new, verified thought published today.”'
};

const flush = () => new Promise(resolve => setImmediate(resolve));

test('no archived quote flashes while the fresh feed is loading', () => {
  const { head, calls } = run(() => new Promise(() => {}));
  assert.equal(head.querySelector('.axiom-thought'), null);
  assert.equal(calls.length, 1);
});

test('renders canonical AXIOM data only for the Warsaw publication date', async () => {
  const { head, calls } = run(async () => ({ ok: true, json: async () => valid }));
  await flush();
  const card = head.querySelector('.axiom-thought');
  assert.ok(card);
  assert.equal(card.querySelector('.axiom-thought__quote').textContent, valid.pl);
  assert.equal(calls.length, 1);
});

test('stale canonical data never falls back to the reserve or historical seed', async () => {
  const stale = { ...valid, date: '2026-09-15' };
  const { head, calls } = run(async () => ({ ok: true, json: async () => stale }));
  await flush();
  assert.equal(head.querySelector('.axiom-thought'), null);
  assert.equal(calls.length, 1);
  assert.ok(!calls[0].includes('reserve'));
});

test('unavailable feed does not display an old quote', async () => {
  const { head } = run(async () => { throw Error('offline'); });
  await flush();
  assert.equal(head.querySelector('.axiom-thought'), null);
});

test('partial response never disguises a historical sentence as current', async () => {
  const { head } = run(async () => ({ ok: true, json: async () => ({ ...valid, en: '' }) }));
  await flush();
  assert.equal(head.querySelector('.axiom-thought'), null);
});
