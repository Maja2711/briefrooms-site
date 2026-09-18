'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const widget = require('../scripts/home-weekly-top-position.js');

function weekly(id, direction = 'long', conviction = 10, entry = 100, tp = 110, sl = 95, score = 50) {
  return {
    instrument_id: id,
    label_pl: id,
    label_en: id,
    symbol: id,
    direction,
    score,
    trade_status: 'open',
    entry_price: entry,
    risk_plan: { direction, take_profit_price: tp, stop_loss_price: sl },
    continuous_entry_decision: { conviction }
  };
}

function stock(ticker, thesis, entryScore, openedAt) {
  return {
    ticker,
    symbol: ticker,
    name: ticker + ' Inc.',
    status: 'OPEN',
    entry: 100,
    stop: 95,
    target: 110,
    thesis_score: thesis,
    entry_score: entryScore,
    opened_at: openedAt || '2026-09-18T10:00:00Z'
  };
}

function portfolio(items) {
  return {
    markets: {
      US: { open_positions: items || [] },
      GPW: { open_positions: [] }
    }
  };
}

test('Warsaw ISO week resolves W38 on 18 September 2026', () => {
  assert.equal(widget.isoWeekId(new Date('2026-09-18T18:00:00Z')), '2026-W38');
});

test('weekly universe is hard-limited to EURUSD, S&P 500 futures and BTCUSD', () => {
  assert.equal(widget.isCanonicalWeeklyInstrument({ instrument_id: 'eurusd' }), true);
  assert.equal(widget.isCanonicalWeeklyInstrument({ instrument_id: 'sp500_futures' }), true);
  assert.equal(widget.isCanonicalWeeklyInstrument({ instrument_id: 'btcusd' }), true);
  assert.equal(widget.isCanonicalWeeklyInstrument({ instrument_id: 'equity.pl.dnp' }), false);
  assert.equal(widget.isCanonicalWeeklyInstrument({ instrument_id: 'dnp' }), false);
});

test('Dino cannot be promoted from weekly even when malformed data marks it open', () => {
  const dino = weekly('dnp', 'long', 999, 36, 39, 35, 999);
  const eur = weekly('eurusd', 'long', 5, 1.15567, 1.16685, 1.14883, 28);
  assert.equal(widget.selectTopWeeklyPosition([dino, eur]).instrument_id, 'eurusd');
});

test('closed, planned and incomplete weekly tickets are never promoted', () => {
  const eur = weekly('eurusd');
  eur.trade_status = 'no_trade';
  const spx = weekly('sp500_futures');
  spx.trade_status = 'planned';
  const btc = weekly('btcusd', 'short');
  btc.trade_status = 'closed';
  assert.equal(widget.selectTopWeeklyPosition([eur, spx, btc]), null);
});

test('active canonical weekly position has priority over Stock Trading portfolio', () => {
  const spx = weekly('sp500_futures', 'long', 12, 7800, 8000, 7680, 80);
  const selected = widget.chooseSignal([spx], portfolio([stock('MPC', 100, 93)]), 'pl');
  assert.equal(selected.kind, 'weekly');
  assert.equal(selected.instrument_id, 'sp500_futures');
});

test('Stock Trading portfolio is the only fallback when no canonical weekly position is open', () => {
  const selected = widget.chooseSignal([], portfolio([
    stock('NTRA', 100, 87),
    stock('MPC', 100, 93),
    stock('NET', 96, 86)
  ]), 'pl');
  assert.equal(selected.kind, 'stock');
  assert.equal(selected.ticker, 'MPC');
});

test('closed Stock Trading positions are not promoted', () => {
  const closed = stock('DNP', 999, 999);
  closed.status = 'CLOSED';
  assert.equal(widget.selectTopStockPosition(portfolio([closed])), null);
});

test('Stock Trading ranking uses thesis score, then entry score', () => {
  const selected = widget.selectTopStockPosition(portfolio([
    stock('AAA', 90, 99),
    stock('BBB', 100, 80),
    stock('CCC', 100, 90)
  ]));
  assert.equal(selected.ticker, 'CCC');
});

test('direction is derived safely from entry, stop and target when absent', () => {
  assert.equal(widget.stockDirection(stock('AAA', 100, 90)), 'long');
  const short = stock('BBB', 100, 90);
  short.stop = 105;
  short.target = 90;
  assert.equal(widget.stockDirection(short), 'short');
});

test('EURUSD keeps five decimals while stock and other weekly instruments use two', () => {
  const eur = widget.weeklySignal(weekly('eurusd', 'long', 5, 1.155668497, 1.16685153, 1.14883443, 28), 'en');
  const spx = widget.weeklySignal(weekly('sp500_futures', 'long', 12, 7787, 7986.125846, 7665.311982, 83), 'en');
  const mpc = widget.stockSignal(Object.assign(stock('MPC', 100, 93), { _market: 'US', entry: 421.82000732, stop: 407.9741874, target: 477.203287 }), 'en');
  assert.equal(widget.formatPrice(eur.entry_price, eur, 'en'), '1.15567');
  assert.equal(widget.formatPrice(spx.entry_price, spx, 'en'), '7,787.00');
  assert.equal(widget.formatPrice(mpc.entry_price, mpc, 'en'), '421.82');
});