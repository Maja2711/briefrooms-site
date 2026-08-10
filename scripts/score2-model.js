(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.Score2Model = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const MG_DL_PER_MMOL_L = 38.67;

  const RECALIBRATION = Object.freeze({
    low: Object.freeze({
      male: Object.freeze({ scale1: -0.5699, scale2: 0.7476 }),
      female: Object.freeze({ scale1: -0.7380, scale2: 0.7019 })
    }),
    moderate: Object.freeze({
      male: Object.freeze({ scale1: -0.1565, scale2: 0.8009 }),
      female: Object.freeze({ scale1: -0.3143, scale2: 0.7701 })
    }),
    high: Object.freeze({
      male: Object.freeze({ scale1: 0.3207, scale2: 0.9360 }),
      female: Object.freeze({ scale1: 0.5710, scale2: 0.9369 })
    }),
    'very-high': Object.freeze({
      male: Object.freeze({ scale1: 0.5836, scale2: 0.8294 }),
      female: Object.freeze({ scale1: 0.9412, scale2: 0.8329 })
    })
  });

  const SEX_COEFFICIENTS = Object.freeze({
    male: Object.freeze({
      age: 0.3742,
      smoker: 0.6012,
      sbp: 0.2777,
      totalCholesterol: 0.1458,
      hdl: -0.2698,
      ageSmoker: -0.0755,
      ageSbp: -0.0255,
      ageTotalCholesterol: -0.0281,
      ageHdl: 0.0426,
      baselineSurvival: 0.9605
    }),
    female: Object.freeze({
      age: 0.4648,
      smoker: 0.7744,
      sbp: 0.3131,
      totalCholesterol: 0.1002,
      hdl: -0.2606,
      ageSmoker: -0.1088,
      ageSbp: -0.0277,
      ageTotalCholesterol: -0.0226,
      ageHdl: 0.0613,
      baselineSurvival: 0.9776
    })
  });

  function assertFiniteNumber(value, name) {
    if (!Number.isFinite(value)) throw new TypeError(name + ' must be a finite number.');
  }

  function toMmolL(value, unit) {
    assertFiniteNumber(value, 'Cholesterol');
    if (unit === 'mmol-l') return value;
    if (unit === 'mg-dl') return value / MG_DL_PER_MMOL_L;
    throw new RangeError('Unsupported cholesterol unit.');
  }

  function categoryFor(age, riskPercent) {
    assertFiniteNumber(age, 'Age');
    assertFiniteNumber(riskPercent, 'Risk');

    if (age < 40 || age > 69) throw new RangeError('SCORE2 is intended for ages 40–69.');

    const lower = age < 50 ? 2.5 : 5;
    const upper = age < 50 ? 7.5 : 10;

    if (riskPercent < lower) return { key: 'low-moderate', lower, upper };
    if (riskPercent < upper) return { key: 'high', lower, upper };
    return { key: 'very-high', lower, upper };
  }

  function calculateScore2(input) {
    const age = Number(input.age);
    const sbp = Number(input.sbp);
    const totalCholesterol = toMmolL(Number(input.totalCholesterol), input.unit || 'mmol-l');
    const hdl = toMmolL(Number(input.hdl), input.unit || 'mmol-l');
    const smoker = input.smoker === true || input.smoker === 1 || input.smoker === '1' ? 1 : 0;
    const sex = input.sex;
    const region = input.region;

    assertFiniteNumber(age, 'Age');
    assertFiniteNumber(sbp, 'Systolic blood pressure');
    if (!SEX_COEFFICIENTS[sex]) throw new RangeError('Unsupported sex.');
    if (!RECALIBRATION[region]) throw new RangeError('Unsupported SCORE2 risk region.');
    if (age < 40 || age > 69) throw new RangeError('SCORE2 is intended for ages 40–69.');
    if (sbp < 100 || sbp > 200) throw new RangeError('Systolic blood pressure is outside the supported range.');
    if (totalCholesterol < 2.5 || totalCholesterol > 10.5) throw new RangeError('Total cholesterol is outside the supported range.');
    if (hdl < 0.5 || hdl > 3.1) throw new RangeError('HDL cholesterol is outside the supported range.');
    if (hdl >= totalCholesterol) throw new RangeError('HDL must be lower than total cholesterol.');

    const c = SEX_COEFFICIENTS[sex];
    const scale = RECALIBRATION[region][sex];
    const ageCentered = (age - 60) / 5;
    const sbpCentered = (sbp - 120) / 20;
    const totalCentered = totalCholesterol - 6;
    const hdlCentered = (hdl - 1.3) / 0.5;

    const linearPredictor =
      c.age * ageCentered +
      c.smoker * smoker +
      c.sbp * sbpCentered +
      c.totalCholesterol * totalCentered +
      c.hdl * hdlCentered +
      c.ageSmoker * ageCentered * smoker +
      c.ageSbp * ageCentered * sbpCentered +
      c.ageTotalCholesterol * ageCentered * totalCentered +
      c.ageHdl * ageCentered * hdlCentered;

    const derivationRisk = 1 - Math.pow(c.baselineSurvival, Math.exp(linearPredictor));
    const calibratedRisk = 1 - Math.exp(
      -Math.exp(scale.scale1 + scale.scale2 * Math.log(-Math.log(1 - derivationRisk)))
    );
    const riskPercentRaw = calibratedRisk * 100;
    const riskPercent = Math.round(riskPercentRaw * 10) / 10;

    return Object.freeze({
      riskPercent,
      riskPercentRaw,
      category: categoryFor(age, riskPercentRaw),
      totalCholesterolMmolL: totalCholesterol,
      hdlMmolL: hdl,
      nonHdlMmolL: totalCholesterol - hdl
    });
  }

  return Object.freeze({
    MG_DL_PER_MMOL_L,
    calculateScore2,
    categoryFor,
    toMmolL
  });
});
