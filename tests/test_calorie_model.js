'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const {
  calculateRestingEnergy,
  calculateCaloriesAndMacros
} = require('../scripts/calorie-model.js');

test('uses the published Mifflin-St Jeor equations for women and men', () => {
  assert.equal(calculateRestingEnergy({ age: 30, weightKg: 80, heightCm: 180, sex: 'male' }), 1780);
  assert.equal(calculateRestingEnergy({ age: 40, weightKg: 60, heightCm: 165, sex: 'female' }), 1270.25);
});

test('calculates maintenance calories and a complete macro split', () => {
  const result = calculateCaloriesAndMacros({
    age: 30,
    weightKg: 80,
    heightCm: 180,
    sex: 'male',
    activity: 'moderate',
    goal: 'maintain',
    proteinPerKg: 1.6,
    fatPercent: 30
  });

  assert.equal(result.restingEnergy, 1780);
  assert.equal(result.maintenanceCalories, 2760);
  assert.equal(result.targetCalories, 2760);
  assert.equal(result.macros.protein.grams, 128);
  assert.equal(result.macros.fat.grams, 92);
  assert.equal(result.macros.carbohydrate.grams, 355);
  assert.equal(result.withinGeneralAmdr, true);
});

test('applies a selected calorie goal without changing the maintenance estimate', () => {
  const result = calculateCaloriesAndMacros({
    age: 40,
    weightKg: 60,
    heightCm: 165,
    sex: 'female',
    activity: 'sedentary',
    goal: 'loss-15',
    proteinPerKg: 1.2,
    fatPercent: 25
  });

  assert.equal(result.maintenanceCalories, 1520);
  assert.equal(result.targetCalories, 1300);
  assert.equal(result.macros.protein.grams, 72);
  assert.equal(result.macros.fat.grams, 36);
  assert.equal(result.macros.carbohydrate.grams, 172);
});

test('rejects incomplete or unsupported physiological inputs and settings', () => {
  const valid = {
    age: 40,
    weightKg: 60,
    heightCm: 165,
    sex: 'female',
    activity: 'moderate',
    goal: 'maintain',
    proteinPerKg: 1.6,
    fatPercent: 30
  };

  assert.throws(() => calculateCaloriesAndMacros({ ...valid, age: 17 }), /Age/);
  assert.throws(() => calculateCaloriesAndMacros({ ...valid, weightKg: 0 }), /Weight/);
  assert.throws(() => calculateCaloriesAndMacros({ ...valid, heightCm: 300 }), /Height/);
  assert.throws(() => calculateCaloriesAndMacros({ ...valid, activity: 'unknown' }), /activity/);
  assert.throws(() => calculateCaloriesAndMacros({ ...valid, proteinPerKg: 7 }), /protein/);
});
