import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

function loadCommonJsBrowserModule(relativeUrl) {
  const source = readFileSync(new URL(relativeUrl, import.meta.url), 'utf8');
  const sandbox = { module: { exports: {} }, exports: {}, console, URL, setTimeout, clearTimeout };
  runInNewContext(source, sandbox, { filename: relativeUrl });
  return sandbox.module.exports;
}

const floor = loadCommonJsBrowserModule('../scripts/home-card-floor.js');


function story(title, slug) {
  return {
    title,
    link: 'https://example.com/' + slug,
    image: 'https://example.com/' + slug + '.jpg',
    published_at: '2026-09-19T10:00:00+00:00',
    homepage_first_seen_at: '2026-09-19T10:00:00+00:00',
    source: 'Nauka w Polsce'
  };
}

test('runtime floor uses only approved homepage and reserve candidates', () => {
  const chosenNoise = story(
    'Eksperci: zmiany prawne to najskuteczniejszy sposób walki z hałasem w naszym otoczeniu',
    'noise-law'
  );
  const reserveWater = story(
    'Prof. Rybicki: wyzwaniem są zarówno niedobory wody, jak i jej nadmiar',
    'water'
  );
  const rawSectionNoise = story(
    'Skąd się bierze hałas w miastach?',
    'noise-city'
  );

  const rows = floor.candidateStories({
    home: [chosenNoise],
    home_reserve: [reserveWater],
    sections: {
      zdrowie: [rawSectionNoise]
    }
  });

  assert.deepEqual(Array.from(rows, item => item.title), [
    chosenNoise.title,
    reserveWater.title
  ]);
  assert.equal(rows.some(item => item.title === rawSectionNoise.title), false);
});

test('runtime floor deduplicates identical stories across home and reserve', () => {
  const selected = story('Selected story', 'same');
  const duplicate = {...selected, title: 'Same link with different title'};
  const reserve = story('Reserve story', 'reserve');

  const rows = floor.candidateStories({
    home: [selected],
    home_reserve: [duplicate, reserve]
  });

  assert.deepEqual(Array.from(rows, item => item.link), [selected.link, reserve.link]);
});

test('floor honors evergreen publication older than 24h but only within first BRs exposure day', () => {
  const now = Date.parse('2026-10-16T09:00:00Z');
  const item = {
    ...story('Important older science', 'older-science'),
    published_at: '2026-10-02T09:00:00Z',
    news_first_seen_at: '2026-10-16T08:00:00Z',
    news_expires_at: '2026-10-17T08:00:00Z',
    news_freshness_profile: 'editorial_exception'
  };
  assert.equal(floor.isEligibleStory(item, 'pl', now), true);
  assert.equal(floor.isEligibleStory(item, 'pl', Date.parse('2026-10-17T08:00:01Z')), false);
  assert.equal(floor.isEligibleStory({...item, news_freshness_profile: 'current'}, 'pl', now), false);
});

test('floor keeps previous-day Polish news until actual 24h expiry', () => {
  const now = Date.parse('2026-10-15T22:15:00Z');
  const item = {
    ...story('Polski mecz w Wuhan', 'old-wuhan'),
    published_at: '2026-10-15T21:30:00Z',
    news_first_seen_at: '2026-10-15T22:00:00Z',
    news_expires_at: '2026-10-16T22:00:00Z',
    news_freshness_profile: 'current',
  };
  assert.equal(floor.isEligibleStory(item, 'pl', now), true);
  assert.equal(floor.isEligibleStory({...item, news_freshness_profile: undefined}, 'pl', now), true);
  assert.equal(floor.isEligibleStory(item, 'pl', Date.parse('2026-10-16T22:00:01Z')), false);
});

test('floor does not accept missing or reset first-seen or overlong 24h exposure', () => {
  const now = Date.parse('2026-10-16T09:00:00Z');
  const item = {
    ...story('Older AI research', 'ai-research'),
    published_at: '2026-10-01T10:00:00Z',
    news_first_seen_at: '2026-10-16T08:00:00Z',
    news_expires_at: '2026-10-17T08:00:00Z',
    news_freshness_profile: 'editorial_exception'
  };
  assert.equal(floor.isEligibleStory({...item, news_first_seen_at: undefined}, 'pl', now), false);
  assert.equal(floor.isEligibleStory({...item, news_expires_at: '2026-10-18T08:00:00Z'}, 'pl', now), false);
});
