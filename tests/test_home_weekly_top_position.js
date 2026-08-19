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
    risk_plan: { direction, take_profit_price: tp, stop_loss_price: sl },
    continuous_entry_decision: { conviction }
  };
}

function daily(market, outcomeStatus = 'PENDING', date = '2026-08-19') {
  const us = market === 'us';
  return {
    date,
    decision: us ? 'TRADE' : 'TRANSAKCJA',
    selection: {
      symbol: us ? 'MRK' : 'PKO.WA',
      ticker: us ? 'MRK' : 'PKO',
      name: us ? 'Merck' : 'PKO BP',
      score: us ? 80.14 : 61.45,
      reference_price: us ? 151.3 : 109.46,
      entry_zone: us ? [150.85, 152.21] : [109.13, 110.12],
      stop: us ? 147.48 : 106.62,
      target: us ? 158.17 : 114.57,
      valid_until: '2026-08-21'
    },
    outcome: { status: outcomeStatus }
  };
}

const NOW = new Date('2026-08-19T14:20:00Z');

test('Warsaw ISO week resolves W33 on 10 August 2026', () => {
  assert.equal(widget.isoWeekId(new Date('2026-08-10T09:00:00Z')), '2026-W33');
});

test('top weekly position is selected by conviction, not exploration utility', () => {
  const eur = open('eurusd', 'long', 5.9764, 1.15567, 1.16685, 1.14883, 28);
  const spx = open('sp500_futures', 'long', 12.45, 7787, 7986.13, 7665.31, 83);
  const btc = open('btcusd', 'short', 6.45, 65100, 62000, 67000, -43);
  assert.equal(widget.selectTopPosition([eur, spx, btc]).instrument_id, 'sp500_futures');
});

test('closed or incomplete weekly tickets are never promoted to homepage', () => {
  const closed = open('sp500_futures', 'long', 20, 7787, 7986, 7665, 83);
  closed.trade_status = 'closed';
  const missingRisk = open('btcusd', 'short', 15, 65000, 62000, 67000, -43);
  delete missingRisk.risk_plan.stop_loss_price;
  const valid = open('eurusd', 'long', 5, 1.15, 1.17, 1.14, 28);
  assert.equal(widget.selectTopPosition([closed, missingRisk, valid]).instrument_id, 'eurusd');
});

test('EURUSD keeps five decimals while index and daily stocks keep two', () => {
  const eur = open('eurusd', 'long', 5, 1.155668497, 1.16685153, 1.14883443, 28);
  const spx = open('sp500_futures', 'long', 12, 7787, 7986.125846, 7665.311982, 83);
  const mrk = widget.dailySignal(daily('us'), 'us', NOW);
  assert.equal(widget.formatPrice(eur.entry_price, eur, 'en'), '1.15567');
  assert.equal(widget.formatPrice(spx.entry_price, spx, 'en'), '7,787.00');
  assert.equal(widget.formatPrice(mrk.entry_price, mrk, 'en'), '151.30');
});

test('weekly signal always has priority over daily recommendations', () => {
  const weekly = open('sp500_futures', 'long', 12.45, 7787, 7986.13, 7665.31, 83);
  const selected = widget.chooseSignal([weekly], { us: daily('us'), gpw: daily('gpw') }, 'en', NOW);
  assert.equal(selected.kind, 'weekly');
  assert.equal(selected.instrument_id, 'sp500_futures');
});

test('English homepage prefers active US Daily Stock when no weekly position is open', () => {
  const selected = widget.chooseSignal([], { us: daily('us'), gpw: daily('gpw') }, 'en', NOW);
  assert.equal(selected.kind, 'daily');
  assert.equal(selected.market, 'us');
  assert.equal(selected.ticker, 'MRK');
  assert.deepEqual(selected.entry_zone, [150.85, 152.21]);
});

test('Polish homepage prefers active GPW Daily Trade when available', () => {
  const selected = widget.chooseSignal([], { us: daily('us'), gpw: daily('gpw') }, 'pl', NOW);
  assert.equal(selected.market, 'gpw');
  assert.equal(selected.ticker, 'PKO');
});

test('Polish homepage falls back to active US trade after GPW trade is resolved', () => {
  const selected = widget.chooseSignal([], {
    gpw: daily('gpw', 'RESOLVED'),
    us: daily('us')
  }, 'pl', NOW);
  assert.equal(selected.market, 'us');
  assert.equal(selected.ticker, 'MRK');
});

test('resolved daily trades are never promoted as recommended', () => {
  assert.equal(widget.dailySignal(daily('gpw', 'RESOLVED'), 'gpw', NOW), null);
  assert.equal(widget.dailySignal(daily('us', 'RESOLVED'), 'us', NOW), null);
});

test('stale daily trade from a previous market date is never promoted', () => {
  assert.equal(widget.dailySignal(daily('gpw', 'PENDING', '2026-08-18'), 'gpw', NOW), null);
  assert.equal(widget.dailySignal(daily('us', 'PENDING', '2026-08-18'), 'us', NOW), null);
});

test('market decision types cannot be mixed between GPW and US feeds', () => {
  assert.equal(widget.dailySignal(daily('us'), 'gpw', NOW), null);
  assert.equal(widget.dailySignal(daily('gpw'), 'us', NOW), null);
});