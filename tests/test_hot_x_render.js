const assert = require('assert');
const path = require('path');

let language = 'pl';
global.document = {
  documentElement: { getAttribute: () => language },
  readyState: 'loading',
  addEventListener: () => {},
};
global.window = {
  scrollY: 0,
  scrollTo: () => {},
  matchMedia: () => ({ matches: false }),
};

require(path.resolve(__dirname, '..', 'scripts', 'hot-x-render.js'));
const hot = window.BriefRoomsHotX;
const longPl = 'Komentarz BriefRooms wyjaśnia kontekst wydarzenia, podaje najważniejsze fakty i opisuje możliwe konsekwencje dla odbiorcy bez mieszania języków.';
const longEn = 'The BriefRooms comment explains the event context, gives the most important facts and outlines possible consequences without mixing languages.';

function item(index) {
  return {
    category: `category-${index}`,
    label_pl: index === 0 ? 'MAKRO' : `TEMAT ${index}`,
    label_en: index === 0 ? 'MACRO' : `TOPIC ${index}`,
    title_pl: `Polski temat numer ${index}`,
    title_en: `English topic number ${index}`,
    comment_pl: longPl,
    comment_en: longEn,
    tweet_url: `https://x.com/briefrooms/status/${100 + index}?utm_source=test`,
    image: '/assets/hot-x/topic-news.svg',
  };
}

function fakeToggle() {
  const classes = new Set();
  const feed = {
    classList: {
      contains: value => classes.has(value),
      toggle: (value, enabled) => enabled ? classes.add(value) : classes.delete(value),
    },
    closest: () => ({ getBoundingClientRect: () => ({ top: 0 }) }),
  };
  const attributes = { 'aria-expanded': 'false' };
  const button = {
    textContent: '',
    addEventListener: (_name, callback) => { button.click = callback; },
    setAttribute: (name, value) => { attributes[name] = value; },
    getAttribute: name => attributes[name],
  };
  return { feed, button, classes };
}

const selected = hot.usableItems(Array.from({ length: 10 }, (_, index) => item(index)));
assert.strictEqual(selected.length, 10, 'ten unique cards are accepted');

let labels = hot.labels();
const markup = selected.map((value, index) => hot.cardHtml(value, index, labels)).join('');
assert.strictEqual((markup.match(/hot-x-extra/g) || []).length, 7, 'seven cards are hidden before expansion');
assert.ok(!selected.slice(0, 3).map((value, index) => hot.cardHtml(value, index, labels)).join('').includes('hot-x-extra'), 'the first three cards are visible');
assert.ok(hot.moreButtonHtml(labels).includes('aria-expanded="false"'), 'the button starts collapsed');
assert.ok(hot.moreButtonHtml(labels).includes('Więcej z X'), 'the Polish button starts with the correct text');
assert.ok(markup.includes('Konkretny post'), 'direct posts receive the Polish type label');
assert.ok(markup.includes('Otwórz post na X →'), 'direct post CTA is correct');

const plToggle = fakeToggle();
hot.attachToggle(plToggle.feed, plToggle.button, labels);
plToggle.button.click();
assert.ok(plToggle.classes.has('hot-x-expanded'));
assert.strictEqual(plToggle.button.getAttribute('aria-expanded'), 'true');
assert.strictEqual(plToggle.button.textContent, 'Pokaż mniej');
plToggle.button.click();
assert.ok(!plToggle.classes.has('hot-x-expanded'));
assert.strictEqual(plToggle.button.getAttribute('aria-expanded'), 'false');
assert.strictEqual(plToggle.button.textContent, 'Więcej z X');

const missingPl = item(20);
delete missingPl.comment_pl;
assert.strictEqual(hot.usableItems([missingPl]).length, 0, 'PL never falls back to EN');

language = 'en';
labels = hot.labels();
assert.strictEqual(labels.more, 'More from X');
assert.strictEqual(labels.less, 'Show less');
assert.strictEqual(labels.postType, 'Specific post');

const enToggle = fakeToggle();
hot.attachToggle(enToggle.feed, enToggle.button, labels);
enToggle.button.click();
assert.strictEqual(enToggle.button.getAttribute('aria-expanded'), 'true');
assert.strictEqual(enToggle.button.textContent, 'Show less');
enToggle.button.click();
assert.strictEqual(enToggle.button.textContent, 'More from X');

const missingEn = item(21);
delete missingEn.comment_en;
assert.strictEqual(hot.usableItems([missingEn]).length, 0, 'EN never falls back to PL');

const duplicates = [item(30), item(31), item(32)];
duplicates.forEach((value, index) => {
  value.title_pl = 'Ten sam temat';
  value.title_en = 'The same topic';
  value.search_url = `https://x.com/search?q=same%20topic&src=tracking-${index}&f=top`;
});
assert.strictEqual(hot.usableItems(duplicates).length, 1, 'three duplicate topics collapse to one');

const emergencyFeed = {
  classList: { remove: () => {} },
  setAttribute: (name, value) => { emergencyFeed[name] = value; },
  querySelector: () => null,
  innerHTML: '',
};
document.querySelector = () => emergencyFeed;
global.fetch = async () => ({ ok: true, json: async () => ({ items: selected }) });

hot.renderEmergency([], []).then(rendered => {
  assert.ok(rendered, 'emergency data prevents an empty first visit');
  assert.strictEqual(emergencyFeed['data-hot-x-count'], '10');
  console.log('Hot X renderer tests passed: PL/EN 3 -> 10 -> 3, labels, aria, strict language and emergency fallback.');
}).catch(error => {
  console.error(error);
  process.exitCode = 1;
});
