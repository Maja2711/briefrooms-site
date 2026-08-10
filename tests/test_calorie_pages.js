'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');

const pages = {
  pl: read('pl/zdrowie/kalkulator-kalorii-makro.html'),
  en: read('en/health/calorie-macro-calculator.html')
};

test('both language versions contain the complete calculator interface', () => {
  const requiredIds = [
    'calorie-form', 'age', 'sex', 'weight', 'height', 'activity', 'goal', 'protein', 'fat',
    'calorie-error', 'calorie-results', 'target-calories', 'resting-calories',
    'maintenance-calories', 'protein-grams', 'fat-grams', 'carbohydrate-grams'
  ];

  for (const html of Object.values(pages)) {
    for (const id of requiredIds) assert.match(html, new RegExp('id="' + id + '"'));
    assert.match(html, /\/scripts\/calorie-model\.js/);
    assert.match(html, /\/scripts\/calorie-calculator\.js/);
    assert.match(html, /\/assets\/calorie-calculator\.css/);
    assert.match(html, /name="eligibility" value="no"/);
    assert.match(html, /name="eligibility" value="yes"/);
  }
});

test('PL and EN pages link to one another and back to their health hubs', () => {
  assert.match(pages.pl, /hreflang="en" href="https:\/\/briefrooms\.com\/en\/health\/calorie-macro-calculator\.html"/);
  assert.match(pages.en, /hreflang="pl" href="https:\/\/briefrooms\.com\/pl\/zdrowie\/kalkulator-kalorii-makro\.html"/);
  assert.match(pages.pl, /href="\/pl\/zdrowie\.html"/);
  assert.match(pages.en, /href="\/en\/health\.html"/);
});

test('both health hubs expose calculator tile 05 and the header maps language routes', () => {
  const plHub = read('pl/zdrowie.html');
  const enHub = read('en/health.html');
  const header = read('scripts/site-header.js');

  assert.match(plHub, /tile-number">05/);
  assert.match(plHub, /href="\/pl\/zdrowie\/kalkulator-kalorii-makro\.html"/);
  assert.match(enHub, /tile-number">05/);
  assert.match(enHub, /href="\/en\/health\/calorie-macro-calculator\.html"/);
  assert.match(header, /'\/pl\/zdrowie\/kalkulator-kalorii-makro\.html': '\/en\/health\/calorie-macro-calculator\.html'/);
  assert.match(header, /'\/en\/health\/calorie-macro-calculator\.html': '\/pl\/zdrowie\/kalkulator-kalorii-makro\.html'/);
});
