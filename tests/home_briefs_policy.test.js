'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const home = require('../scripts/home-briefs.js');

const NOW = Date.parse('2026-08-26T12:00:00Z');

function item(lang, id, category, ageMs) {
  const base = lang === 'en' ? '/en/briefs' : '/pl/briefy';
  return {
    category,
    title: `Story ${id}`,
    link: `https://example.com/${id}`,
    image: `https://example.com/${id}.jpg`,
    source: 'Example',
    full_brief: `Approved brief ${id}`,
    published_at: new Date(NOW - ageMs).toISOString(),
    permalink: `${base}/story-${id}-${id.padStart(12, 'a').slice(-12)}.html`,
    comment_quality_status: 'passed_strict_v7',
    comment_quality_version: 7,
    summary_basis: 'article_text_ai_reviewed',
    comment_generation_status: 'ai_review_approved'
  };
}

test('PL homepage promotes politics, economy and health ahead of other fresh news', () => {
  const rows = [
    item('pl', '1', 'Technologia', 5 * 60 * 1000),
    item('pl', '2', 'Zdrowie', 20 * 60 * 1000),
    item('pl', '3', 'Ekonomia / Biznes', 30 * 60 * 1000),
    item('pl', '4', 'Polityka / Kraj', 40 * 60 * 1000),
    item('pl', '5', 'Sport', 2 * 60 * 1000)
  ];
  const selected = home.selectApproved(rows, 'pl', NOW);
  assert.deepEqual(selected.slice(0, 3).map(row => home.topicForCategory(row.category)), [
    'politics', 'economy', 'health'
  ]);
});

test('EN homepage promotes politics/world, business and health', () => {
  const rows = [
    item('en', '1', 'Science', 1 * 60 * 1000),
    item('en', '2', 'Health', 20 * 60 * 1000),
    item('en', '3', 'Business', 30 * 60 * 1000),
    item('en', '4', 'World News', 40 * 60 * 1000),
    item('en', '5', 'Sport', 2 * 60 * 1000)
  ];
  const selected = home.selectApproved(rows, 'en', NOW);
  assert.deepEqual(selected.slice(0, 3).map(row => home.topicForCategory(row.category)), [
    'politics', 'economy', 'health'
  ]);
});

test('homepage accepts exactly 72 hours and rejects anything older or undated', () => {
  const exact = item('pl', '1', 'Polityka', home.HOME_MAX_AGE_MS);
  const stale = item('pl', '2', 'Ekonomia', home.HOME_MAX_AGE_MS + 1);
  const missing = item('pl', '3', 'Zdrowie', 1000);
  delete missing.published_at;

  const selected = home.selectApproved([stale, missing, exact], 'pl', NOW);
  assert.deepEqual(selected.map(row => row.title), [exact.title]);
  assert.equal(home.isFresh(exact, NOW), true);
  assert.equal(home.isFresh(stale, NOW), false);
  assert.equal(home.isFresh(missing, NOW), false);
});

test('future timestamps beyond clock tolerance are not eligible', () => {
  const future = item('en', '1', 'Health', -(11 * 60 * 1000));
  assert.equal(home.selectApproved([future], 'en', NOW).length, 0);
});
