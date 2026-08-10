(function () {
  'use strict';

  const model = window.CalorieModel;
  const form = document.getElementById('calorie-form');
  if (!model || !form) return;

  const lang = document.documentElement.lang === 'pl' ? 'pl' : 'en';
  const locale = lang === 'pl' ? 'pl-PL' : 'en-GB';
  const errorBox = document.getElementById('calorie-error');
  const resultBox = document.getElementById('calorie-results');

  const T = lang === 'pl' ? {
    required: 'Uzupełnij wszystkie pola kalkulatora.',
    notEligible: 'W tej sytuacji ogólny kalkulator może podać niewłaściwy cel. Ustal zapotrzebowanie indywidualnie z lekarzem lub dietetykiem klinicznym.',
    age: 'Kalkulator obsługuje osoby dorosłe w wieku 18–80 lat.',
    weight: 'Podaj masę ciała od 30 do 300 kg.',
    height: 'Podaj wzrost od 120 do 230 cm.',
    macro: 'Wybrane ustawienia makroskładników nie mieszczą się w obliczonym limicie kalorii. Zmniejsz białko lub udział tłuszczu.',
    generic: 'Nie udało się obliczyć wyniku. Sprawdź dane i spróbuj ponownie.',
    calories: 'kcal/dzień',
    grams: 'g/dzień',
    goals: {
      'loss-15': 'redukcja −15%',
      'loss-10': 'łagodna redukcja −10%',
      maintain: 'utrzymanie masy',
      'gain-10': 'nadwyżka +10%'
    },
    activities: {
      sedentary: 'mała aktywność',
      light: 'lekka aktywność',
      moderate: 'umiarkowana aktywność',
      high: 'duża aktywność',
      'very-high': 'bardzo duża aktywność'
    },
    profile: (d, r) => 'Profil: ' + fmt(d.age, 0) + ' lat, ' + fmt(d.weightKg, 1) + ' kg, ' + fmt(d.heightCm, 0) + ' cm, ' + T.activities[d.activity] + ' (PAL ' + fmt(r.activityFactor, 3) + '), cel: ' + T.goals[d.goal] + '.',
    macroBalanceOk: 'Wybrany podział mieści się w ogólnych zakresach AMDR dla dorosłych: białko 10–35%, tłuszcz 20–35%, węglowodany 45–65% energii.',
    macroBalanceOutside: 'Ten podział wychodzi poza co najmniej jeden ogólny zakres AMDR. Może być celowy, ale warto sprawdzić go z dietetykiem.',
    belowResting: 'Uwaga: wybrany cel jest niższy niż oszacowane zapotrzebowanie spoczynkowe. Nie stosuj go długoterminowo bez indywidualnej konsultacji.',
    adjustment: (factor) => factor === 1 ? 'bez korekty' : (factor < 1 ? '−' : '+') + fmt(Math.abs(factor - 1) * 100, 0) + '% względem utrzymania'
  } : {
    required: 'Complete every field in the calculator.',
    notEligible: 'A general calculator may give an unsuitable target in this situation. Estimate your needs individually with a doctor or registered dietitian.',
    age: 'The calculator supports adults aged 18–80 years.',
    weight: 'Enter a body weight from 30 to 300 kg.',
    height: 'Enter a height from 120 to 230 cm.',
    macro: 'The selected macro settings exceed the calorie target. Reduce the protein target or fat share.',
    generic: 'The result could not be calculated. Check the details and try again.',
    calories: 'kcal/day',
    grams: 'g/day',
    goals: {
      'loss-15': '15% calorie deficit',
      'loss-10': 'gentle 10% deficit',
      maintain: 'weight maintenance',
      'gain-10': '10% calorie surplus'
    },
    activities: {
      sedentary: 'low activity',
      light: 'light activity',
      moderate: 'moderate activity',
      high: 'high activity',
      'very-high': 'very high activity'
    },
    profile: (d, r) => 'Profile: age ' + fmt(d.age, 0) + ', ' + fmt(d.weightKg, 1) + ' kg, ' + fmt(d.heightCm, 0) + ' cm, ' + T.activities[d.activity] + ' (PAL ' + fmt(r.activityFactor, 3) + '), goal: ' + T.goals[d.goal] + '.',
    macroBalanceOk: 'The selected split is within the general adult AMDR ranges: protein 10–35%, fat 20–35% and carbohydrate 45–65% of energy.',
    macroBalanceOutside: 'This split falls outside at least one general AMDR range. That may be intentional, but consider checking it with a registered dietitian.',
    belowResting: 'Caution: the selected target is below estimated resting energy needs. Do not use it long term without individual professional guidance.',
    adjustment: (factor) => factor === 1 ? 'no adjustment' : (factor < 1 ? '−' : '+') + fmt(Math.abs(factor - 1) * 100, 0) + '% versus maintenance'
  };

  function fmt(value, digits) {
    return Number(value).toLocaleString(locale, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    });
  }

  function parseNumber(id) {
    const value = document.getElementById(id).value.trim().replace(',', '.');
    return value === '' ? NaN : Number(value);
  }

  function setText(id, text) {
    document.getElementById(id).textContent = text;
  }

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
    resultBox.hidden = true;
  }

  function macroText(macro) {
    return fmt(macro.grams, 0) + ' ' + T.grams;
  }

  function macroMeta(macro) {
    return fmt(macro.calories, 0) + ' kcal · ' + fmt(macro.percent, 0) + '%';
  }

  form.addEventListener('reset', function () {
    window.setTimeout(function () {
      errorBox.hidden = true;
      resultBox.hidden = true;
    }, 0);
  });

  form.addEventListener('submit', function (event) {
    event.preventDefault();

    const eligibility = form.elements.eligibility.value;
    const data = {
      age: parseNumber('age'),
      sex: document.getElementById('sex').value,
      weightKg: parseNumber('weight'),
      heightCm: parseNumber('height'),
      activity: document.getElementById('activity').value,
      goal: document.getElementById('goal').value,
      proteinPerKg: parseNumber('protein'),
      fatPercent: parseNumber('fat')
    };

    if (!eligibility || !data.sex || !data.activity || !data.goal ||
        [data.age, data.weightKg, data.heightCm, data.proteinPerKg, data.fatPercent].some((value) => !Number.isFinite(value))) {
      showError(T.required);
      return;
    }
    if (eligibility === 'yes') {
      showError(T.notEligible);
      return;
    }

    try {
      const result = model.calculateCaloriesAndMacros(data);
      errorBox.hidden = true;
      setText('target-calories', fmt(result.targetCalories, 0));
      setText('resting-calories', fmt(result.restingEnergy, 0) + ' ' + T.calories);
      setText('maintenance-calories', fmt(result.maintenanceCalories, 0) + ' ' + T.calories);
      setText('goal-adjustment', T.adjustment(result.goalFactor));
      setText('protein-grams', macroText(result.macros.protein));
      setText('protein-meta', macroMeta(result.macros.protein));
      setText('fat-grams', macroText(result.macros.fat));
      setText('fat-meta', macroMeta(result.macros.fat));
      setText('carbohydrate-grams', macroText(result.macros.carbohydrate));
      setText('carbohydrate-meta', macroMeta(result.macros.carbohydrate));
      setText('calorie-profile', T.profile(data, result));
      setText('macro-balance-note', result.withinGeneralAmdr ? T.macroBalanceOk : T.macroBalanceOutside);

      const warning = document.getElementById('resting-warning');
      warning.textContent = result.belowRestingEnergy ? T.belowResting : '';
      warning.hidden = !result.belowRestingEnergy;
      resultBox.className = 'calorie-results' + (result.withinGeneralAmdr ? '' : ' has-balance-warning');
      resultBox.hidden = false;
      resultBox.scrollIntoView({
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'start'
      });
    } catch (error) {
      const message = /Age/.test(error.message) ? T.age
        : /Weight/.test(error.message) ? T.weight
          : /Height/.test(error.message) ? T.height
            : /exceed/.test(error.message) ? T.macro
              : T.generic;
      showError(message);
    }
  });
})();
