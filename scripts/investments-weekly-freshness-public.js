(() => {
  'use strict';

  const MISSING = 'MISSING_DECISION';
  const nativeFetch = window.fetch.bind(window);
  const injectedNow = window.BR_WEEKLY_FRESHNESS_NOW;
  const now = injectedNow ? new Date(injectedNow) : new Date();

  function isoWeek(date) {
    const x = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
    const day = x.getUTCDay() || 7;
    x.setUTCDate(x.getUTCDate() + 4 - day);
    const year = new Date(Date.UTC(x.getUTCFullYear(), 0, 1));
    const week = Math.ceil((((x - year) / 86400000) + 1) / 7);
    return `${x.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
  }

  function publicTargetWeek(date) {
    const target = new Date(date.getTime());
    // Monday-Saturday: current ISO week. Sunday: governed forecast for the
    // following trading week is due, so the next ISO week is the public target.
    if (target.getDay() === 0) target.setDate(target.getDate() + 1);
    return isoWeek(target);
  }

  const targetWeekId = publicTargetWeek(now);
  const targetPath = `/data/investments/weekly/${targetWeekId}.json`;
  let syntheticMissingUsed = false;
  let selectingTarget = false;

  function missingPayload() {
    return {
      week_id: targetWeekId,
      decision_state: MISSING,
      public_status: MISSING,
      forecast_status: 'missing_decision',
      model_status: 'weekly_decision_missing_fail_closed',
      missing_decision: true,
      generated_by: 'investments-weekly-freshness-public',
      public_message_pl: 'Brak wygenerowanej decyzji dla tego tygodnia. System nie zastępuje jej danymi z poprzedniego tygodnia.',
      public_message_en: 'The decision for this week has not been generated. The system does not substitute data from the previous week.',
      instruments: [],
    };
  }

  function responseForMissing() {
    const payload = missingPayload();
    return {
      ok: true,
      status: 200,
      statusText: 'Synthetic fail-closed weekly state',
      json: async () => payload,
      text: async () => JSON.stringify(payload),
    };
  }

  window.fetch = async function weeklyFreshnessFetch(input, init) {
    const url = typeof input === 'string' ? input : String(input?.url || input || '');
    const isTarget = url.split('?', 1)[0] === targetPath;
    if (!isTarget) return nativeFetch(input, init);
    try {
      const response = await nativeFetch(input, init);
      if (response && response.ok) return response;
    } catch (_) {
      // Fall through to the explicit fail-closed state.
    }
    syntheticMissingUsed = true;
    return responseForMissing();
  };

  function localMessage(week) {
    const lang = (window.BR_WEEKLY?.lang || document.documentElement.lang || 'pl').toLowerCase();
    if (lang.startsWith('en')) {
      return week.public_message_en || 'The decision for this week has not been generated. The previous week is not used as a substitute.';
    }
    return week.public_message_pl || 'Brak wygenerowanej decyzji dla tego tygodnia. Poprzedni tydzień nie jest używany jako zastępstwo.';
  }

  function decorateMissing(week) {
    if (week?.decision_state !== MISSING && week?.public_status !== MISSING && week?.forecast_status !== 'missing_decision') return;
    const cards = document.querySelector('#app .cards');
    if (!cards) return;
    const message = localMessage(week);
    cards.innerHTML = `<div class="weekly-missing-decision" role="status" aria-live="polite"><strong>${MISSING}</strong><p>${message}</p></div>`;
    cards.setAttribute('data-weekly-decision-state', MISSING);
  }

  document.addEventListener('br:weekly-rendered', (event) => {
    const week = event?.detail || {};
    if (week.week_id !== targetWeekId && !selectingTarget && typeof window.BR_WEEKLY_SELECT === 'function') {
      selectingTarget = true;
      try {
        window.BR_WEEKLY_SELECT(targetWeekId);
      } finally {
        selectingTarget = false;
      }
      return;
    }
    if (week.week_id === targetWeekId) decorateMissing(week);
  });

  window.BR_WEEKLY_FRESHNESS = {
    targetWeekId,
    targetPath,
    MISSING_DECISION: MISSING,
    publicTargetWeek,
    missingPayload,
    syntheticMissingUsed: () => syntheticMissingUsed,
  };
})();
