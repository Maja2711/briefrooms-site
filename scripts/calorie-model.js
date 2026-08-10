(function (root, factory) {
  'use strict';

  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.CalorieModel = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const ACTIVITY_FACTORS = Object.freeze({
    sedentary: 1.2,
    light: 1.375,
    moderate: 1.55,
    high: 1.725,
    'very-high': 1.9
  });

  const GOAL_FACTORS = Object.freeze({
    'loss-15': 0.85,
    'loss-10': 0.9,
    maintain: 1,
    'gain-10': 1.1
  });

  const PROTEIN_FACTORS = Object.freeze([1.2, 1.6, 2]);
  const FAT_PERCENTAGES = Object.freeze([25, 30, 35]);

  function assertFinite(value, name) {
    if (!Number.isFinite(value)) throw new TypeError(name + ' must be a finite number.');
  }

  function roundTo(value, step) {
    return Math.round(value / step) * step;
  }

  function calculateRestingEnergy(input) {
    const age = Number(input.age);
    const weightKg = Number(input.weightKg);
    const heightCm = Number(input.heightCm);
    const sex = input.sex;

    assertFinite(age, 'Age');
    assertFinite(weightKg, 'Weight');
    assertFinite(heightCm, 'Height');
    if (age < 18 || age > 80) throw new RangeError('Age is outside the supported range.');
    if (weightKg < 30 || weightKg > 300) throw new RangeError('Weight is outside the supported range.');
    if (heightCm < 120 || heightCm > 230) throw new RangeError('Height is outside the supported range.');
    if (sex !== 'female' && sex !== 'male') throw new RangeError('Unsupported sex.');

    const sexConstant = sex === 'male' ? 5 : -161;
    return 10 * weightKg + 6.25 * heightCm - 5 * age + sexConstant;
  }

  function calculateCaloriesAndMacros(input) {
    const restingEnergyRaw = calculateRestingEnergy(input);
    const weightKg = Number(input.weightKg);
    const activityFactor = ACTIVITY_FACTORS[input.activity];
    const goalFactor = GOAL_FACTORS[input.goal];
    const proteinPerKg = Number(input.proteinPerKg);
    const fatPercent = Number(input.fatPercent);

    if (!activityFactor) throw new RangeError('Unsupported activity level.');
    if (!goalFactor) throw new RangeError('Unsupported goal.');
    if (!PROTEIN_FACTORS.includes(proteinPerKg)) throw new RangeError('Unsupported protein target.');
    if (!FAT_PERCENTAGES.includes(fatPercent)) throw new RangeError('Unsupported fat target.');

    const maintenanceCaloriesRaw = restingEnergyRaw * activityFactor;
    const targetCalories = roundTo(maintenanceCaloriesRaw * goalFactor, 10);
    const proteinGrams = Math.round(weightKg * proteinPerKg);
    const proteinCalories = proteinGrams * 4;
    const fatCalories = targetCalories * fatPercent / 100;
    const fatGrams = Math.round(fatCalories / 9);
    const carbohydrateCalories = targetCalories - proteinCalories - fatCalories;

    if (carbohydrateCalories < 0) {
      throw new RangeError('Selected protein and fat targets exceed the calorie target.');
    }

    const carbohydrateGrams = Math.round(carbohydrateCalories / 4);
    const proteinPercent = proteinCalories / targetCalories * 100;
    const carbohydratePercent = carbohydrateCalories / targetCalories * 100;

    return Object.freeze({
      restingEnergy: Math.round(restingEnergyRaw),
      restingEnergyRaw,
      maintenanceCalories: roundTo(maintenanceCaloriesRaw, 10),
      maintenanceCaloriesRaw,
      targetCalories,
      activityFactor,
      goalFactor,
      macros: Object.freeze({
        protein: Object.freeze({
          grams: proteinGrams,
          calories: proteinCalories,
          percent: proteinPercent,
          perKg: proteinPerKg
        }),
        fat: Object.freeze({
          grams: fatGrams,
          calories: Math.round(fatCalories),
          percent: fatPercent
        }),
        carbohydrate: Object.freeze({
          grams: carbohydrateGrams,
          calories: Math.round(carbohydrateCalories),
          percent: carbohydratePercent
        })
      }),
      withinGeneralAmdr: proteinPercent >= 10 && proteinPercent <= 35 &&
        fatPercent >= 20 && fatPercent <= 35 &&
        carbohydratePercent >= 45 && carbohydratePercent <= 65,
      belowRestingEnergy: targetCalories < restingEnergyRaw
    });
  }

  return Object.freeze({
    ACTIVITY_FACTORS,
    GOAL_FACTORS,
    PROTEIN_FACTORS,
    FAT_PERCENTAGES,
    calculateRestingEnergy,
    calculateCaloriesAndMacros
  });
});
