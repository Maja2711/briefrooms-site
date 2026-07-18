'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const homepage = require('../scripts/home-briefs.js');

function approved(lang = 'en') {
  return {
    title: lang === 'pl' ? 'Zatwierdzony brief' : 'Approved brief',
    full_brief: lang === 'pl' ? 'Pełne zatwierdzone streszczenie.' : 'A full approved summary.',
    source: 'Example News',
    category: 'World',
    link: `https://example.com/${lang}/story`,
    image: 'https://images.example.com/story.jpg',
    permalink: lang === 'pl'
      ? '/pl/briefy/zatwierdzony-aaaaaaaaaaaa.html'
      : '/en/briefs/approved-aaaaaaaaaaaa.html',
    comment_quality_status: 'passed_strict_v7',
    comment_quality_version: 7,
    summary_basis: 'article_text_ai_reviewed',
    comment_generation_status: 'ai_review_approved'
  };
}

function fakeDocument(staticUpdatedAt) {
  const container = {
    dataset: { homeUpdatedAt: staticUpdatedAt },
    replaceChildren() {
      throw new Error('static cards must not be replaced in this test');
    }
  };
  const label = { textContent: 'Static date' };
  return {
    container,
    label,
    getElementById(id) {
      return id === 'latest-briefs' ? container : id === 'updated-at' ? label : null;
    }
  };
}

test('strict approval and language separation are enforced', () => {
  const en = approved('en');
  assert.equal(homepage.selectApproved([en], 'en').length, 1);
  assert.equal(homepage.selectApproved([en], 'pl').length, 0);
  for (const [field, value] of [
    ['comment_quality_status', 'rejected'],
    ['comment_quality_version', 6],
    ['summary_basis', 'rss'],
    ['comment_generation_status', 'pending'],
    ['full_brief', '']
  ]) {
    assert.equal(homepage.isApproved({ ...en, [field]: value }), false, field);
  }
});

test('unsafe URLs are rejected', () => {
  assert.equal(homepage.safeHttpUrl('javascript:alert(1)'), '');
  assert.equal(homepage.safeHttpUrl('data:text/html,bad'), '');
  assert.equal(homepage.safeHttpUrl('https://example.com/image.jpg'), 'https://example.com/image.jpg');
  assert.equal(homepage.safePermalink('/en/briefs/good-aaaaaaaaaaaa.html', 'en'), '/en/briefs/good-aaaaaaaaaaaa.html');
  assert.equal(homepage.safePermalink('/pl/briefy/good-aaaaaaaaaaaa.html', 'en'), '');
});

test('failed JSON request leaves static cards and date untouched', async () => {
  const document = fakeDocument('2026-07-18T10:00:00+00:00');
  let warnings = 0;
  let renders = 0;
  const changed = await homepage.loadHome({
    document,
    lang: 'en',
    fetchImpl: async () => { throw new Error('offline'); },
    renderer: () => { renders += 1; return true; },
    console: { warn: () => { warnings += 1; } }
  });
  assert.equal(changed, false);
  assert.equal(renders, 0);
  assert.equal(warnings, 1);
  assert.equal(document.label.textContent, 'Static date');
  assert.equal(document.container.dataset.homeUpdatedAt, '2026-07-18T10:00:00+00:00');
});

test('older or equally dated JSON does not replace static cards', async () => {
  const document = fakeDocument('2026-07-18T10:00:00+00:00');
  let renders = 0;
  const changed = await homepage.loadHome({
    document,
    lang: 'en',
    fetchImpl: async () => ({
      ok: true,
      json: async () => ({ updated_at: '2026-07-18T10:00:00+00:00', latest: [approved('en')] })
    }),
    renderer: () => { renders += 1; return true; },
    console: { warn() {} }
  });
  assert.equal(changed, false);
  assert.equal(renders, 0);
  assert.equal(document.label.textContent, 'Static date');
});

test('newer approved JSON may refresh the static cards', async () => {
  const document = fakeDocument('2026-07-18T10:00:00+00:00');
  let renders = 0;
  const changed = await homepage.loadHome({
    document,
    lang: 'en',
    fetchImpl: async () => ({
      ok: true,
      json: async () => ({ updated_at: '2026-07-19T11:00:00+00:00', latest: [approved('en')] })
    }),
    renderer: (_document, items, lang) => {
      renders += 1;
      assert.equal(items.length, 1);
      assert.equal(lang, 'en');
      return true;
    },
    console: { warn() {} }
  });
  assert.equal(changed, true);
  assert.equal(renders, 1);
  assert.equal(document.container.dataset.homeUpdatedAt, '2026-07-19T11:00:00+00:00');
  assert.match(document.label.textContent, /^Update: /);
});
