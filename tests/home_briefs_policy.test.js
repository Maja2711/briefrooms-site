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

test('homepage card contract is exactly twelve', () => {
  const rows = Array.from({ length: 16 }, (_, index) =>
    item('pl', String(index + 1), index % 2 ? 'Ekonomia' : 'Polityka', (index + 1) * 60 * 1000)
  );
  assert.equal(home.CARD_LIMIT, 12);
  assert.equal(home.selectApproved(rows, 'pl', NOW).length, 12);
});

test('legacy homepage renderer preserves feed editorial order', () => {
  const rows = [
    item('pl', '1', 'Polityka / Kraj', 40 * 60 * 1000),
    item('pl', '2', 'Geopolityka', 30 * 60 * 1000),
    item('pl', '3', 'Ekonomia / Biznes', 20 * 60 * 1000),
    item('pl', '4', 'Nauka / Technologie', 10 * 60 * 1000)
  ];
  const selected = home.selectApproved(rows, 'pl', NOW);
  assert.deepEqual(selected.map(row => row.title), rows.map(row => row.title));
});

test('homepage accepts exactly 24 hours and rejects anything older or undated', () => {
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
