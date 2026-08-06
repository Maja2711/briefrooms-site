'use strict';

const assert = require('node:assert/strict');
const reports = require('../scripts/portfolio-10k-material-reports-public.js');

const position = {
  id: 'googl',
  review_flag: 'HOLD',
  model_score: 80,
  positive_signals: ['price_above_ma200'],
  risk_signals: ['material_news_headline_requires_review']
};

const input = [
  {
    id: 'price-alert',
    position_id: 'googl',
    type: 'PRICE_ALERT',
    category: 'DAILY_MOVE',
    event_date: '2026-08-05',
    title_pl: 'Jednodniowy ruch kursu',
    title_en: 'One-day price move',
    model_action: 'HOLD'
  },
  {
    id: 'regulatory-event',
    position_id: 'googl',
    type: 'REGULATORY',
    category: 'VERIFIED_SOURCE_EVENT',
    methodology_version: 'analysis-news-v2',
    event_date: '2026-07-28',
    published_at: '2026-07-28T07:00:00Z',
    title_pl: 'Istotne zdarzenie regulacyjne',
    title_en: 'Material regulatory event',
    summary_pl: 'Zweryfikowane zdarzenie Reutersa.',
    summary_en: 'A verified Reuters event.',
    model_action: 'THESIS_REVIEW',
    sources: [{label: 'Reuters', url: 'https://example.com/reuters'}]
  }
];

const selected = reports.reportsForPosition(input, 'googl');
assert.equal(selected.length, 1);
assert.equal(selected[0].id, 'regulatory-event');
assert.equal(reports.isPublicMaterialReport(input[0]), false);
assert.equal(reports.isPublicMaterialReport(input[1]), true);

const reconciled = reports.reportForCurrentDecision(input[1], position);
assert.equal(reconciled.model_action, 'HOLD');
assert.equal(reconciled.decision_inputs.review_flag, 'HOLD');
assert.equal(reconciled.decision_inputs.model_score, 80);

const htmlPl = reports.renderForPosition({reports: input, position, lang: 'pl'});
assert.match(htmlPl, /Istotne zdarzenie regulacyjne/);
assert.match(htmlPl, /Trzymaj/);
assert.doesNotMatch(htmlPl, /Jednodniowy ruch kursu/);
assert.doesNotMatch(htmlPl, /ALERT CENOWY/);

const htmlEn = reports.renderForPosition({reports: input, position, lang: 'en'});
assert.match(htmlEn, /Material regulatory event/);
assert.match(htmlEn, />Hold</);
assert.doesNotMatch(htmlEn, /One-day price move/);

console.log('Portfolio 10K public material-report filtering: PASS');
