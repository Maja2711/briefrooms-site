const test = require('node:test');
const assert = require('node:assert/strict');
const floor = require('../scripts/home-card-floor.js');

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

  assert.deepEqual(rows.map(item => item.title), [
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

  assert.deepEqual(rows.map(item => item.link), [selected.link, reserve.link]);
});
