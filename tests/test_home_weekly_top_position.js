'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const widget = require('../scripts/home-weekly-top-position.js');

function open(id, direction, conviction, entry, tp, sl, score) {
  return {
    instrument_id: id,
    label_pl: id,
    label_en: id,
    direction,
    score,
    trade_status: 'open',
    entry_price: entry,
    risk_plan: {
      direction,
      take_profit_price: tp,
      stop_loss_price: sl
    },
    continuous_entry_decision: { conviction }
  };
}

function daily(decision, outcomeStatus = 'PENDING') {
  return {
    date: '2026-08-19',
    decision,
    selection: {
      symbol: decision === 'TRADE' ? 'MRK' : 'PKO.WA',
      ticker: decision === 'TRADE' ? 'MRK' : 'PKO',
      name: decision === 'TRADE' ? 'Merck' : 'PKO BP',
      score: 80.14,
      reference_price: 151.3,
      entry_zone: [150.85, 152.21],
      stop: 147.48,
      target: 158.17,
      valid_until: '2026-08-21'
    },
    outcome: { status: outcomeStatus }
  };
}

test('Warsaw ISO week resolves W33 on 10 August 2026', () => {
  assert.equal(widget.isoWeekId(new Date('2026-08-10T09:00:00Z')), '2026-W33');
});

test('top weekly position is selected by conviction, not exploration utility', () => {
  const eur = open('eurusd', 'long', 5.9764, 1.15567, 1.16685, 1.14883, 28);
  const spx = open('sp500_futures', 'long', 12.45, 7787, 7986.13, 7665.31, 83);
  const btc = open('btcusd', 'short', 6.45, 65100, 62000, 67000, -43);
  assert.equal(widget.selectTopPosition([eur, spx, btc]).instrument_id, 'sp500_futures');
});

test('closed or incomplete tickets are never promoted to homepage', () => {
  const closed = open('sp500_futures', 'long', 20, 7787, 7986, 7665, 83);
  closed.trade_status = 'closed';
  const missingRisk = open('btcusd', 'short', 15, 65000, 62000, 67000, -43);
  delete missingRisk.risk_plan.stop_loss_price;
  const valid = open('eurusd', 'long', 5, 1.15, 1.17, 1.14, 28);
  assert.equal(widget.selectTopPosition([closed, missingRisk, valid]).instrument_id, 'eurusd');
});

test('EURUSD keeps five decimals while index and crypto keep two', () => {
  const eur = open('eurusd', 'long', 5, 1.155668497, 1.16685153, 1.14883443, 28);
  const spx = open('sp500_futures', 'long', 12, 7787, 7986.125846, 7665.311982, 83);
  assert.equal(widget.formatPrice(eur.entry_price, eur, 'en'), '1.15567');
  assert.equal(widget.formatPrice(spx.entry_price, spx, 'en'), '7,787.00');
});

test('weekly signal always has priority over a daily recommendation', () => {
  const weekly = open('sp500_futures', 'long', 12.45, 7787, 7986.13, 7665.31, 83);
  const selected = widget.chooseSignal([weekly], daily('TRADE'), 'en');
  assert.equal(selected.kind, 'weekly');
  assert.equal(selected.instrument_id, 'sp500_futures');
});

test('English homepage falls back to active US Daily Stock when no weekly position is open', () => {
  const selected = widget.chooseSignal([], daily('TRADE'), 'en');
  assert.equal(selected.kind, 'daily');
  assert.equal(selected.ticker, 'MRK');
  assert.deepEqual(selected.entry_zone, [150.85, 152.21]);
});

test('Polish homepage accepts GPW daily trade but never promotes a resolved trade', () => {
  const active = widget.dailySignal(daily('TRANSAKCJA'), 'pl');
  assert.equal(active.ticker, 'PKO');
  assert.equal(widget.dailySignal(daily('TRANSAKCJA', 'RESOLVED'), 'pl'), null);
});

test('wrong-market daily decision is not promoted on the other language homepage', () => {
  assert.equal(widget.dailySignal(daily('TRADE'), 'pl'), null);
  assert.equal(widget.dailySignal(daily('TRANSAKCJA'), 'en'), null);
});