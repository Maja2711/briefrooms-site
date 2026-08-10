'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const { calculateScore2, categoryFor, toMmolL, MG_DL_PER_MMOL_L } = require('../scripts/score2-model.js');

const table4Profile = {
  age: 50,
  smoker: true,
  sbp: 140,
  totalCholesterol: 6.3,
  hdl: 1.4,
  unit: 'mmol-l'
};

test('matches the SCORE2 supplementary Table 4 examples in every region', () => {
  const expected = {
    low: { male: 6.3, female: 4.3 },
    moderate: { male: 8.1, female: 5.2 },
    high: { male: 8.8, female: 7.1 },
    'very-high': { male: 15.1, female: 14.1 }
  };

  for (const [region, bySex] of Object.entries(expected)) {
    for (const [sex, risk] of Object.entries(bySex)) {
      assert.equal(calculateScore2({ ...table4Profile, region, sex }).riskPercent, risk);
    }
  }
});

test('mg/dL and mmol/L inputs produce the same result', () => {
  const mmol = calculateScore2({ ...table4Profile, region: 'high', sex: 'male' });
  const mg = calculateScore2({
    ...table4Profile,
    region: 'high',
    sex: 'male',
    unit: 'mg-dl',
    totalCholesterol: 6.3 * MG_DL_PER_MMOL_L,
    hdl: 1.4 * MG_DL_PER_MMOL_L
  });
  assert.equal(mg.riskPercent, mmol.riskPercent);
  assert.ok(Math.abs(toMmolL(193.35, 'mg-dl') - 5) < 1e-12);
});

test('uses age-specific ESC risk categories', () => {
  assert.equal(categoryFor(49, 2.49).key, 'low-moderate');
  assert.equal(categoryFor(49, 2.5).key, 'high');
  assert.equal(categoryFor(49, 7.5).key, 'very-high');
  assert.equal(categoryFor(50, 4.99).key, 'low-moderate');
  assert.equal(categoryFor(50, 5).key, 'high');
  assert.equal(categoryFor(69, 10).key, 'very-high');
});

test('rejects ages and measurements outside the supported calculator range', () => {
  const valid = { ...table4Profile, region: 'high', sex: 'female' };
  assert.throws(() => calculateScore2({ ...valid, age: 39 }), /ages 40/);
  assert.throws(() => calculateScore2({ ...valid, age: 70 }), /ages 40/);
  assert.throws(() => calculateScore2({ ...valid, sbp: 99 }), /blood pressure/);
  assert.throws(() => calculateScore2({ ...valid, hdl: 7 }), /HDL cholesterol/);
  assert.throws(() => calculateScore2({ ...valid, totalCholesterol: 1.2 }), /Total cholesterol/);
});
