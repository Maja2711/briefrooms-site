'use strict';

import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {runInNewContext} from 'node:vm';

const source = readFileSync(new URL('../scripts/news-live.js', import.meta.url), 'utf8');
const anchor = "  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});";
assert.ok(source.includes(anchor), 'news live runtime entrypoint changed; test hooks must be checked');
const instrumented = source.replace(anchor,
  "  globalThis.__NEWS_POLICY_TEST__ = {isCurrentDesk, isCurrentSource, isFreshNewsStory, isCurrentHomepageStory, cardExpiryTimestamp};\n" + anchor
);

function runtime(lang) {
  const sandbox = {
    document: {
      documentElement: {lang},
      readyState: 'loading',
      addEventListener() {},
    },
    URL,
    Date,
    Intl,
    console,
    location: {href: 'https://briefrooms.com/' + lang + '/aktualnosci.html'},
  };
  runInNewContext(instrumented, sandbox, {filename: 'scripts/news-live.js'});
  return sandbox.__NEWS_POLICY_TEST__;
}

const now = Date.parse('2026-10-16T09:00:00Z');
function article({published, firstSeen = now - 3600_000, expires = now + 23 * 3600_000}) {
  return {
    published_at: published,
    news_first_seen_at: new Date(firstSeen).toISOString(),
    news_expires_at: new Date(expires).toISOString(),
  };
}

test('current-news sections are strict; Science Health AI are exceptions in both languages', () => {
  for (const lang of ['pl', 'en']) {
    const api = runtime(lang);
    for (const id of ['polityka', 'ekonomia', 'sport', 'world-news', 'business']) {
      assert.equal(api.isCurrentDesk(id), true, lang + '/' + id);
    }
    for (const id of ['nauka', 'zdrowie', 'ai-technologia-krypto',
      'science', 'health', 'ai-technology-crypto']) {
      assert.equal(api.isCurrentDesk(id), false, lang + '/' + id);
    }
  }
});

test('12 October Sport article cannot appear on 16 October', () => {
  const api = runtime('pl');
  const old = article({published: '2026-10-12T08:52:00Z'});
  assert.equal(api.isFreshNewsStory(old, true, now), false);
  assert.equal(api.isFreshNewsStory(old, false, now), true);
});

test('PL current news rejects previous Warsaw calendar day even inside 24 hours', () => {
  const api = runtime('pl');
  const instant = Date.parse('2026-10-15T22:15:00Z');
  const oldWarsawDay = '2026-10-15T21:30:00Z';
  const row = article({
    published: oldWarsawDay,
    firstSeen: instant - 600_000,
    expires: instant + 23 * 3600_000,
  });
  assert.equal(api.isFreshNewsStory(row, true, instant), false);
  assert.equal(api.isFreshNewsStory(row, false, instant), true);
});

test('exception material older than a month is allowed only inside first 24h BRs exposure', () => {
  const api = runtime('pl');
  const old = article({published: '2026-09-01T08:00:00Z'});
  assert.equal(api.isFreshNewsStory(old, false, now), true);
  assert.equal(api.isFreshNewsStory(old, false, now + 24 * 3600_000), false);
});

test('forged expiry beyond 24 hours does not extend an exception', () => {
  const api = runtime('en');
  const tooLong = article({
    published: '2026-10-01T08:00:00Z',
    expires: now + 30 * 3600_000,
  });
  assert.equal(api.isFreshNewsStory(tooLong, false, now), false);
});

test('unknown or future source timestamps fail closed in exception desks', () => {
  const api = runtime('pl');
  assert.equal(api.isFreshNewsStory(article({published: ''}), false, now), false);
  assert.equal(api.isFreshNewsStory(article({published: new Date(now + 30 * 60_000).toISOString()}), false, now), false);
});

test('homepage metadata controls the correct freshness profile', () => {
  const api = runtime('pl');
  assert.equal(api.isCurrentHomepageStory({news_freshness_profile: 'editorial_exception'}), false);
  assert.equal(api.isCurrentHomepageStory({news_freshness_profile: 'current'}), true);
  assert.equal(api.isCurrentHomepageStory({homepage_lane: 'sport'}), true);
  assert.equal(api.isCurrentHomepageStory({}), true);
});

test('older exception card expiry tracks first publication in BRs, not original source date', () => {
  const api = runtime('pl');
  const first = '2026-10-16T09:00:00Z';
  const expire = '2026-10-17T09:00:00Z';
  const card = {
    dataset: {
      homeFreshnessProfile: 'editorial_exception',
      homePublishedAt: '2026-01-01T10:00:00Z',
      homeFirstSeenAt: first,
      homeExpiresAt: expire,
    },
  };
  assert.equal(api.cardExpiryTimestamp(card, 'homePublishedAt', 'homeFirstSeenAt', 'homeExpiresAt'), Date.parse(expire));
});
