(function () {
  'use strict';

  const model = window.Score2Model;
  const form = document.getElementById('score2-form');
  if (!model || !form) return;

  const lang = document.documentElement.lang === 'pl' ? 'pl' : 'en';
  const locale = lang === 'pl' ? 'pl-PL' : 'en-GB';
  const errorBox = document.getElementById('score2-error');
  const resultBox = document.getElementById('score2-results');
  const unitSelect = document.getElementById('cholesterol-unit');
  const totalInput = document.getElementById('total-cholesterol');
  const hdlInput = document.getElementById('hdl-cholesterol');

  const T = lang === 'pl' ? {
    required: 'Uzupełnij wszystkie pola kalkulatora.',
    notEligible: 'Zwykły SCORE2 nie jest przeznaczony do tej sytuacji. Ryzyko powinien ocenić lekarz z użyciem właściwego modelu i danych klinicznych.',
    age: 'SCORE2 jest przeznaczony dla osób w wieku 40–69 lat.',
    sbp: 'Podaj skurczowe ciśnienie tętnicze od 100 do 200 mmHg.',
    total: 'Podaj cholesterol całkowity w obsługiwanym zakresie.',
    hdl: 'Podaj cholesterol HDL w obsługiwanym zakresie.',
    relation: 'HDL musi być niższy niż cholesterol całkowity. Sprawdź wartości i jednostkę.',
    generic: 'Nie udało się obliczyć wyniku. Sprawdź dane i spróbuj ponownie.',
    categories: {
      'low-moderate': 'małe do umiarkowanego',
      high: 'duże',
      'very-high': 'bardzo duże'
    },
    region: {
      low: 'małego ryzyka',
      moderate: 'umiarkowanego ryzyka',
      high: 'wysokiego ryzyka (Polska)',
      'very-high': 'bardzo wysokiego ryzyka'
    },
    smoker: { yes: 'osoba paląca', no: 'osoba niepaląca' },
    threshold: (c) => c.key === 'low-moderate'
      ? 'Poniżej ' + fmt(c.lower, 1) + '% — próg małego do umiarkowanego ryzyka dla tej grupy wieku.'
      : c.key === 'high'
        ? 'Od ' + fmt(c.lower, 1) + '% do poniżej ' + fmt(c.upper, 1) + '% — przedział dużego ryzyka dla tej grupy wieku.'
        : 'Co najmniej ' + fmt(c.upper, 1) + '% — próg bardzo dużego ryzyka dla tej grupy wieku.',
    profile: (d) => d.age + ' lat, ' + (d.sex === 'male' ? 'mężczyzna' : 'kobieta') + ', ' + T.smoker[d.smoker ? 'yes' : 'no'] + ', ciśnienie skurczowe ' + fmt(d.sbp, 0) + ' mmHg, region ' + T.region[d.region] + '.',
    nonHdl: (mmol, mg) => 'Wyliczony cholesterol nie-HDL: ' + fmt(mg, 0) + ' mg/dl (' + fmt(mmol, 2) + ' mmol/l).'
  } : {
    required: 'Complete every field in the calculator.',
    notEligible: 'Standard SCORE2 is not intended for this situation. A clinician should assess risk with the appropriate model and clinical information.',
    age: 'SCORE2 is intended for people aged 40–69 years.',
    sbp: 'Enter systolic blood pressure from 100 to 200 mmHg.',
    total: 'Enter total cholesterol within the supported range.',
    hdl: 'Enter HDL cholesterol within the supported range.',
    relation: 'HDL must be lower than total cholesterol. Check the values and unit.',
    generic: 'The result could not be calculated. Check the data and try again.',
    categories: {
      'low-moderate': 'low-to-moderate',
      high: 'high',
      'very-high': 'very high'
    },
    region: {
      low: 'low-risk',
      moderate: 'moderate-risk',
      high: 'high-risk (includes Poland)',
      'very-high': 'very-high-risk'
    },
    smoker: { yes: 'current smoker', no: 'non-smoker' },
    threshold: (c) => c.key === 'low-moderate'
      ? 'Below ' + fmt(c.lower, 1) + '% — the low-to-moderate-risk threshold for this age group.'
      : c.key === 'high'
        ? 'From ' + fmt(c.lower, 1) + '% to below ' + fmt(c.upper, 1) + '% — the high-risk range for this age group.'
        : 'At least ' + fmt(c.upper, 1) + '% — the very-high-risk threshold for this age group.',
    profile: (d) => 'Age ' + d.age + ', ' + (d.sex === 'male' ? 'male' : 'female') + ', ' + T.smoker[d.smoker ? 'yes' : 'no'] + ', systolic blood pressure ' + fmt(d.sbp, 0) + ' mmHg, ' + T.region[d.region] + ' region.',
    nonHdl: (mmol, mg) => 'Calculated non-HDL cholesterol: ' + fmt(mg, 0) + ' mg/dL (' + fmt(mmol, 2) + ' mmol/L).'
  };

  function fmt(value, digits) {
    return Number(value).toLocaleString(locale, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    });
  }

  function parseNumber(input) {
    const value = input.value.trim().replace(',', '.');
    return value === '' ? NaN : Number(value);
  }

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
    resultBox.hidden = true;
  }

  function setText(id, text) {
    document.getElementById(id).textContent = text;
  }

  function categoryClass(key) {
    return key === 'low-moderate' ? 'risk-low' : key === 'high' ? 'risk-high' : 'risk-very-high';
  }

  function updateUnitHints() {
    const isMg = unitSelect.value === 'mg-dl';
    totalInput.placeholder = isMg ? (lang === 'pl' ? 'np. 200' : 'e.g. 200') : (lang === 'pl' ? 'np. 5,2' : 'e.g. 5.2');
    hdlInput.placeholder = isMg ? (lang === 'pl' ? 'np. 55' : 'e.g. 55') : (lang === 'pl' ? 'np. 1,4' : 'e.g. 1.4');
    document.querySelectorAll('[data-chol-unit]').forEach((node) => {
      node.textContent = isMg ? (lang === 'pl' ? 'mg/dl' : 'mg/dL') : (lang === 'pl' ? 'mmol/l' : 'mmol/L');
    });
  }

  unitSelect.addEventListener('change', function () {
    const previous = unitSelect.dataset.previousUnit || 'mg-dl';
    const next = unitSelect.value;
    if (previous !== next) {
      [totalInput, hdlInput].forEach((input) => {
        const value = parseNumber(input);
        if (!Number.isFinite(value)) return;
        const converted = next === 'mmol-l'
          ? value / model.MG_DL_PER_MMOL_L
          : value * model.MG_DL_PER_MMOL_L;
        input.value = next === 'mmol-l' ? converted.toFixed(2) : converted.toFixed(0);
      });
    }
    unitSelect.dataset.previousUnit = next;
    updateUnitHints();
    resultBox.hidden = true;
  });

  form.addEventListener('reset', function () {
    window.setTimeout(function () {
      errorBox.hidden = true;
      resultBox.hidden = true;
      unitSelect.dataset.previousUnit = unitSelect.value;
      updateUnitHints();
    }, 0);
  });

  form.addEventListener('submit', function (event) {
    event.preventDefault();

    const eligibility = form.elements.eligibility.value;
    const data = {
      age: parseNumber(document.getElementById('age')),
      sex: document.getElementById('sex').value,
      smoker: document.getElementById('smoker').value === '1',
      sbp: parseNumber(document.getElementById('sbp')),
      totalCholesterol: parseNumber(totalInput),
      hdl: parseNumber(hdlInput),
      unit: unitSelect.value,
      region: document.getElementById('risk-region').value
    };

    if (!eligibility || !data.sex || document.getElementById('smoker').value === '' || !data.region ||
        [data.age, data.sbp, data.totalCholesterol, data.hdl].some((value) => !Number.isFinite(value))) {
      showError(T.required);
      return;
    }
    if (eligibility === 'yes') {
      showError(T.notEligible);
      return;
    }

    try {
      const result = model.calculateScore2(data);
      errorBox.hidden = true;
      resultBox.className = 'score2-results ' + categoryClass(result.category.key);
      setText('score2-risk-value', fmt(result.riskPercent, 1) + '%');
      setText('score2-category', T.categories[result.category.key]);
      setText('score2-threshold', T.threshold(result.category));
      setText('score2-profile', T.profile(data));
      setText('score2-non-hdl', T.nonHdl(result.nonHdlMmolL, result.nonHdlMmolL * model.MG_DL_PER_MMOL_L));
      resultBox.hidden = false;
      resultBox.scrollIntoView({
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'start'
      });
    } catch (error) {
      const message = /ages 40/.test(error.message) ? T.age
        : /blood pressure/.test(error.message) ? T.sbp
          : /Total cholesterol/.test(error.message) ? T.total
            : /HDL cholesterol/.test(error.message) ? T.hdl
              : /HDL must/.test(error.message) ? T.relation
                : T.generic;
      showError(message);
    }
  });

  unitSelect.dataset.previousUnit = unitSelect.value;
  updateUnitHints();
})();
