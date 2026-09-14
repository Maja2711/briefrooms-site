const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync('scripts/investments-weekly-freshness-public.js', 'utf8');

function load({ now, nativeFetch }) {
  const listeners = new Map();
  const cards = { innerHTML: '', attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
  const document = {
    documentElement: { lang: 'pl' },
    addEventListener(type, fn) { listeners.set(type, fn); },
    querySelector(selector) { return selector === '#app .cards' ? cards : null; },
  };
  const window = {
    BR_WEEKLY: { lang: 'pl' },
    BR_WEEKLY_FRESHNESS_NOW: now,
    fetch: nativeFetch,
  };
  vm.runInContext(script, vm.createContext({ window, document, Date, console }));
  return { window, document, cards, listeners };
}

test('Monday public target is the current ISO week', () => {
  const { window } = load({
    now: '2026-09-14T12:00:00+02:00',
    nativeFetch: async () => ({ ok: true, json: async () => ({}) }),
  });
  assert.equal(window.BR_WEEKLY_FRESHNESS.targetWeekId, '2026-W38');
});

test('Sunday public target advances to the following trading week', () => {
  const { window } = load({
    now: '2026-09-13T12:00:00+02:00',
    nativeFetch: async () => ({ ok: true, json: async () => ({}) }),
  });
  assert.equal(window.BR_WEEKLY_FRESHNESS.targetWeekId, '2026-W38');
});

test('missing target fetch becomes explicit MISSING_DECISION instead of stale fallback', async () => {
  const { window } = load({
    now: '2026-09-14T12:00:00+02:00',
    nativeFetch: async () => ({ ok: false, status: 404, json: async () => ({}) }),
  });
  const response = await window.fetch('/data/investments/weekly/2026-W38.json?v=1');
  assert.equal(response.ok, true);
  const data = await response.json();
  assert.equal(data.week_id, '2026-W38');
  assert.equal(data.decision_state, 'MISSING_DECISION');
  assert.deepEqual(data.instruments, []);
  assert.equal(Object.hasOwn(data, 'trade_status'), false);
});

test('render event forces the public target instead of leaving previous week selected', () => {
  const { window, listeners } = load({
    now: '2026-09-14T12:00:00+02:00',
    nativeFetch: async () => ({ ok: true, json: async () => ({}) }),
  });
  let selected = null;
  window.BR_WEEKLY_SELECT = (weekId) => { selected = weekId; };
  listeners.get('br:weekly-rendered')({ detail: { week_id: '2026-W37' } });
  assert.equal(selected, '2026-W38');
});

test('explicit missing state is rendered as MISSING_DECISION, never no-position', () => {
  const { cards, listeners } = load({
    now: '2026-09-14T12:00:00+02:00',
    nativeFetch: async () => ({ ok: true, json: async () => ({}) }),
  });
  listeners.get('br:weekly-rendered')({
    detail: {
      week_id: '2026-W38',
      decision_state: 'MISSING_DECISION',
      public_message_pl: 'Brak decyzji testowej.',
    },
  });
  assert.match(cards.innerHTML, /MISSING_DECISION/);
  assert.match(cards.innerHTML, /Brak decyzji testowej/);
  assert.doesNotMatch(cards.innerHTML, /bez pozycji/i);
  assert.equal(cards.attrs['data-weekly-decision-state'], 'MISSING_DECISION');
});
