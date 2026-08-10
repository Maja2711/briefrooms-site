(function () {
  'use strict';

  const CFG = window.BR_WEEKLY || {};
  const L = CFG.lang === 'en' ? 'en' : 'pl';
  const T = {
    pl: {
      updated: 'Aktualizacja', summary: 'Podsumowanie', current: 'Wybrany tydzień',
      open: 'Cena otwarcia', close: 'Cena zamknięcia', price: 'Cena teraz', priceTime: 'Stan na',
      status: 'Status', pnl: 'Wynik', notional: 'Nominał', sl: 'Stop Loss', tp: 'Take Profit',
      rr: 'Risk/Reward', closed: 'zamknięta', active: 'w trakcie', neutral: 'bez pozycji',
      planned: 'oczekuje na otwarcie', history: 'Historia pozycji', week: 'Tydzień',
      instrument: 'Instrument', position: 'Pozycja', result: 'Wynik', points: 'pkt',
      analysis: 'Opis', analysisMissing: 'Pozycja zostanie uzupełniona po wygenerowaniu sygnału i zarejestrowaniu ceny wejścia.',
      stale: 'Dane rynkowe są opóźnione. Pokazujemy ostatnią zapisaną cenę wraz z godziną jej aktualizacji.',
      loadError: 'Nie udało się pobrać aktualnych danych. Ostatnio zapisany widok pozostaje dostępny.',
      no: '—', audit: 'DANE W AUDYCIE', withheld: 'Wynik wstrzymany',
      auditText: 'Rekord nie przeszedł kontroli spójności.',
      total: 'Łączny wynik zamkniętych pozycji', wins: 'Zyskowne / stratne',
      next: 'następny tydzień', settled: 'wynik historyczny',
      legal: 'Treści mają charakter edukacyjny i analityczny. To nie jest rekomendacja inwestycyjna ani porada finansowa.'
    },
    en: {
      updated: 'Updated', summary: 'Summary', current: 'Selected week',
      open: 'Open price', close: 'Close price', price: 'Price now', priceTime: 'As of',
      status: 'Status', pnl: 'Result', notional: 'Notional', sl: 'Stop Loss', tp: 'Take Profit',
      rr: 'Risk/Reward', closed: 'closed', active: 'in progress', neutral: 'no position',
      planned: 'waiting for entry', history: 'Position history', week: 'Week',
      instrument: 'Instrument', position: 'Position', result: 'Result', points: 'pts',
      analysis: 'Description', analysisMissing: 'The position will be completed after a signal is generated and the entry price is recorded.',
      stale: 'Market data is delayed. The last stored price is shown together with its update time.',
      loadError: 'Current data could not be loaded. The last stored view remains available.',
      no: '—', audit: 'DATA UNDER AUDIT', withheld: 'Result withheld',
      auditText: 'The record failed the consistency check.',
      total: 'Total closed result', wins: 'Profitable / losing',
      next: 'next week', settled: 'historical result',
      legal: 'Content is educational and analytical. It is not investment advice or financial advice.'
    }
  }[L];

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
  const n = (value) => {
    const result = Number(value);
    return Number.isFinite(result) ? result : null;
  };
  const good = (value) => {
    const result = n(value);
    return result !== null && result > 0 ? result : null;
  };
  const parseTime = (value) => {
    if (!value) return null;
    const date = new Date(String(value));
    return Number.isNaN(date.getTime()) ? null : date;
  };
  const closeEnough = (a, b, tolerance) => a !== null && b !== null && Math.abs(a - b) <= tolerance;
  const label = (item) => item[L === 'pl' ? 'label_pl' : 'label_en'] || item.symbol || item.instrument_id;
  const dir = (item) => item.direction === 'short' ? 'short' : item.direction === 'long' ? 'long' : 'neutral';
  const dirText = (item) => dir(item) === 'neutral' ? (L === 'pl' ? 'NEUTRALNIE' : 'NEUTRAL') : dir(item).toUpperCase();

  function fmt(value, instrumentId) {
    const result = good(value);
    if (result === null) return T.no;
    const digits = instrumentId === 'eurusd' ? 5 : 2;
    return result.toLocaleString(L === 'pl' ? 'pl-PL' : 'en-US', {
      minimumFractionDigits: digits, maximumFractionDigits: digits,
    });
  }
  function money(value) {
    const result = n(value);
    return result === null ? T.no : `${result >= 0 ? '+' : ''}${result.toLocaleString(L === 'pl' ? 'pl-PL' : 'en-US', {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    })} USD`;
  }
  function signed(value, digits = 2) {
    const result = n(value);
    return result === null ? T.no : `${result >= 0 ? '+' : ''}${result.toLocaleString(L === 'pl' ? 'pl-PL' : 'en-US', {
      minimumFractionDigits: digits, maximumFractionDigits: digits,
    })}`;
  }
  function notional(item) {
    return good(item.instrument_id === 'eurusd' ? item.notional_eur : item.notional_usd) || 10000;
  }
  function notionalText(item) {
    const currency = item.instrument_id === 'eurusd' ? 'EUR' : 'USD';
    return `${notional(item).toLocaleString(L === 'pl' ? 'pl-PL' : 'en-US', { maximumFractionDigits: 0 })} ${currency}`;
  }
  function riskPlan(item) {
    const plan = item && typeof item.risk_plan === 'object' && item.risk_plan ? item.risk_plan : {};
    return {
      sl: good(plan.stop_loss_price ?? item.stop_loss_price),
      tp: good(plan.take_profit_price ?? item.take_profit_price),
      rr: n(plan.reward_to_risk ?? item.risk_distance?.reward_to_risk),
    };
  }
  function rrText(value) {
    const result = n(value);
    return result === null || result <= 0 ? T.no : `1:${result.toLocaleString(L === 'pl' ? 'pl-PL' : 'en-US', {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    })}`;
  }
  function majorVersion(value) {
    const parsed = Number.parseInt(String(value || '0').split('.', 1)[0], 10);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  function calculatedMetrics(item, mark, methodVersion = null) {
    const entry = good(item.entry_price);
    const market = good(mark);
    if (dir(item) === 'neutral' || entry === null || market === null) return null;
    const move = dir(item) === 'long' ? market - entry : entry - market;
    const percent = move / entry * 100;
    const value = item.instrument_id === 'eurusd' ? move * notional(item) : move / entry * notional(item);
    const legacyBtcPriceUnits = item.instrument_id === 'btcusd'
      && methodVersion !== null
      && (majorVersion(methodVersion) < 2 || String(methodVersion).includes('reconstructed'));
    const units = item.instrument_id === 'eurusd'
      ? move / 0.0001
      : item.instrument_id === 'btcusd' && !legacyBtcPriceUnits
        ? percent
        : move;
    return { value, units, percent };
  }
  function metrics(item, mark) {
    const calculated = calculatedMetrics(item, mark);
    if (good(item.exit_price) === null) return calculated;
    return {
      value: n(item.result_value) ?? calculated?.value ?? null,
      units: n(item.result_units) ?? calculated?.units ?? null,
      percent: n(item.result_percent) ?? calculated?.percent ?? null,
    };
  }
  function resultText(item, mark) {
    const result = metrics(item, mark);
    if (!result || result.value === null) return T.no;
    const parts = [money(result.value)];
    if (item.instrument_id === 'eurusd' && result.units !== null) parts.push(`${signed(result.units, 1)} pips`);
    if (item.instrument_id === 'sp500_futures' && result.units !== null) parts.push(`${signed(result.units, 2)} ${T.points}`);
    if (result.percent !== null) parts.push(`${signed(result.percent, 2)}%`);
    return parts.join(' · ');
  }
  function tone(value) {
    const result = n(value);
    return result === null || Math.abs(result) < 0.000001 ? 'neutral' : result > 0 ? 'positive' : 'negative';
  }
  function hasClose(item) {
    return dir(item) !== 'neutral' && good(item.entry_price) !== null && good(item.exit_price) !== null;
  }
  function status(item) {
    if (item.trade_status === 'planned' || item.forecast_status === 'scheduled') return T.planned;
    if (dir(item) === 'neutral') return T.neutral;
    return hasClose(item) ? T.closed : T.active;
  }
  function isoWeek(date) {
    const x = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
    const day = x.getUTCDay() || 7;
    x.setUTCDate(x.getUTCDate() + 4 - day);
    const year = new Date(Date.UTC(x.getUTCFullYear(), 0, 1));
    const week = Math.ceil((((x - year) / 86400000) + 1) / 7);
    return `${x.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
  }
  async function json(url) {
    const response = await fetch(`${url}?v=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(url);
    return response.json();
  }
  async function loadWeeks() {
    const ids = [];
    const next = new Date();
    next.setDate(next.getDate() + 7);
    ids.push(isoWeek(next));
    for (let i = 0; i < 26; i += 1) {
      const date = new Date();
      date.setDate(date.getDate() - i * 7);
      const id = isoWeek(date);
      if (!ids.includes(id)) ids.push(id);
    }
    const out = [];
    await Promise.all(ids.map((id) => json(`/data/investments/weekly/${id}.json`)
      .then((week) => out.push(week)).catch(() => {})));
    return out.filter((week) => week && week.week_id)
      .sort((a, b) => String(b.week_id).localeCompare(String(a.week_id)));
  }
  async function loadQuarantine() {
    try { return await json('/data/investments/public_quarantine.json'); }
    catch (_) { return { records: [] }; }
  }
  function quarantineMap(data) {
    const map = new Map();
    for (const row of Array.isArray(data?.records) ? data.records : []) {
      if (row?.public_status !== 'withheld') continue;
      map.set(`${row.week_id}/${row.instrument_id}`, row);
    }
    return map;
  }
  function plannedEntryIsValid(week, item, now = new Date()) {
    if (!week || dir(item) === 'neutral' || good(item.entry_price) !== null || item.trade_status !== 'planned') return false;
    const pending = item.pending_entry_decision;
    if (!pending || typeof pending !== 'object' || !pending.decision || typeof pending.decision !== 'object') return false;
    const decidedAt = parseTime(pending.decided_at);
    const entryNotBefore = parseTime(pending.entry_not_before);
    const latest = parseTime(week?.market_window?.entry_latest_local);
    if (!decidedAt || !entryNotBefore || !latest || entryNotBefore < decidedAt) return false;
    return now <= latest;
  }
  function integrityIssues(item, methodVersion = null, week = null) {
    const issues = [];
    const side = dir(item);
    const entry = good(item.entry_price);
    const exit = good(item.exit_price);
    const entryAt = parseTime(item.entry_captured_at);
    const exitAt = parseTime(item.exit_captured_at);
    const plan = riskPlan(item);

    if (side !== 'neutral' && entry === null && !plannedEntryIsValid(week, item)) issues.push('directional_missing_entry');
    if (entry !== null && !entryAt) issues.push('missing_entry_timestamp');
    if (exit !== null && !exitAt) issues.push('missing_exit_timestamp');
    if (entryAt && exitAt && exitAt < entryAt) issues.push('exit_before_entry');
    if (item.trade_status === 'open' && exit !== null) issues.push('open_has_exit');
    if (item.trade_status === 'closed' && exit === null) issues.push('closed_missing_exit');

    if (entry !== null && plan.sl !== null && plan.tp !== null) {
      if (side === 'long' && !(plan.sl < entry && entry < plan.tp)) issues.push('invalid_long_risk_order');
      if (side === 'short' && !(plan.tp < entry && entry < plan.sl)) issues.push('invalid_short_risk_order');
    }
    if (entry !== null && exit !== null && side !== 'neutral') {
      const expected = calculatedMetrics(item, exit, methodVersion);
      const unitTolerance = item.instrument_id === 'eurusd' ? 0.15 : 0.05;
      if (n(item.result_units) !== null && !closeEnough(n(item.result_units), expected.units, unitTolerance)) issues.push('result_units_mismatch');
      if (n(item.result_percent) !== null && !closeEnough(n(item.result_percent), expected.percent, 0.02)) issues.push('result_percent_mismatch');
      if (n(item.result_value) !== null && !closeEnough(n(item.result_value), expected.value, 0.05)) issues.push('result_value_mismatch');
    }
    return issues;
  }
  function approvedItem(item, row) {
    if (!row?.manual_public_result) return item;
    return {
      ...item,
      entry_price: row.entry_price ?? item.entry_price,
      exit_price: row.exit_price ?? item.exit_price,
      result_value: row.result_value ?? item.result_value,
      result_units: row.result_units ?? item.result_units,
      result_percent: row.result_percent ?? item.result_percent,
      trade_status: 'closed',
    };
  }
  function auditState(week, item, quarantine) {
    const explicit = quarantine.get(`${week.week_id}/${item.instrument_id}`) || null;
    if (explicit?.manual_public_result) return { withheld: false, reason: '', issues: [], item: approvedItem(item, explicit) };
    const issues = integrityIssues(item, week.method_version || null, week);
    if (explicit || issues.length) {
      return { withheld: true, reason: explicit?.reason || `${T.auditText} [${issues.join(', ')}]`, issues, item };
    }
    return { withheld: false, reason: '', issues: [], item };
  }
  function livePrice(live, id) {
    return good((((live || {}).prices || {})[id] || {}).price);
  }
  function liveTimestamp(live, id) {
    const record = (((live || {}).prices || {})[id] || {});
    return record.current_price_updated_at || record.timestamp || '';
  }
  function fmtTime(value) {
    if (!value) return '';
    const date = new Date(String(value));
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString(L === 'pl' ? 'pl-PL' : 'en-GB', {
      timeZone: 'Europe/Warsaw', day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  }
  function liveIsStale(live) {
    const date = new Date(String((live || {}).updated_at || ''));
    return Number.isNaN(date.getTime()) || Date.now() - date.getTime() > 45 * 60 * 1000;
  }
  function card(item, live, state) {
    const current = livePrice(live, item.instrument_id);
    const currentTime = fmtTime(liveTimestamp(live, item.instrument_id));
    if (state.withheld) {
      return `<article class="card ${esc(dir(item))} integrity-withheld"><div class="head"><div><p>${esc(label(item))}</p><h3>${esc(dirText(item))}</h3></div></div><dl class="grid"><div class="cell big"><dt>${T.pnl}</dt><dd class="neutral">${esc(T.withheld)}</dd></div><div class="cell"><dt>${T.status}</dt><dd>${esc(T.audit)}</dd></div><div class="cell big"><dt>${T.audit}</dt><dd>${esc(state.reason)}</dd></div></dl></article>`;
    }
    item = state.item;
    const neutral = dir(item) === 'neutral';
    const mark = hasClose(item) ? item.exit_price : current;
    const result = metrics(item, mark);
    const value = result?.value ?? null;
    const risk = riskPlan(item);
    const description = item[L === 'pl' ? 'rationale_pl' : 'rationale_en'];
    const text = Array.isArray(description) ? description.join(' ') : (description || T.analysisMissing);
    return `<article class="card ${esc(dir(item))}"><div class="head"><div><p>${esc(label(item))}</p><h3>${esc(dirText(item))}</h3></div></div><div class="now"><span>${T.price}</span><strong>${esc(fmt(current, item.instrument_id))}</strong><small>${currentTime ? esc(`${T.priceTime}: ${currentTime}`) : T.no}</small></div><dl class="grid"><div class="cell"><dt>${T.open}</dt><dd>${neutral ? T.no : esc(fmt(item.entry_price, item.instrument_id))}</dd></div><div class="cell"><dt>${T.close}</dt><dd>${hasClose(item) ? esc(fmt(item.exit_price, item.instrument_id)) : T.no}</dd></div><div class="cell"><dt>${T.notional}</dt><dd>${neutral ? T.no : esc(notionalText(item))}</dd></div><div class="cell"><dt>${T.status}</dt><dd>${esc(status(item))}</dd></div><div class="cell"><dt>${T.sl}</dt><dd>${neutral ? T.no : esc(fmt(risk.sl, item.instrument_id))}</dd></div><div class="cell"><dt>${T.tp}</dt><dd>${neutral ? T.no : esc(fmt(risk.tp, item.instrument_id))}</dd></div><div class="cell big"><dt>${T.rr}</dt><dd>${neutral ? T.no : esc(rrText(risk.rr))}</dd></div><div class="cell big"><dt>${T.pnl}</dt><dd class="${tone(value)}">${neutral ? T.no : esc(resultText(item, mark))}</dd></div><div class="cell big analysis"><dt>${T.analysis}</dt><dd>${esc(text)}</dd></div></dl></article>`;
  }

  let weeksCache = [];
  let quarantineCache = new Map();
  let liveCache = {};
  let selectedWeekId = null;

  function render() {
    if (!weeksCache.length) return;
    const selected = weeksCache.find((week) => week.week_id === selectedWeekId) || weeksCache[0];
    selectedWeekId = selected.week_id;

    let total = 0, wins = 0, losses = 0, closed = 0;
    const rows = [];
    for (const week of weeksCache) {
      for (const original of (week.instruments || [])) {
        const state = auditState(week, original, quarantineCache);
        if (state.withheld) continue;
        const item = state.item;
        if (!hasClose(item)) continue;
        const value = metrics(item, item.exit_price)?.value ?? null;
        if (value === null) continue;
        total += value; closed += 1;
        if (value > 0) wins += 1;
        if (value < 0) losses += 1;
        rows.push(`<tr><td>${esc(week.week_id)}</td><td>${esc(label(item))}</td><td>${esc(dirText(item))}</td><td>${esc(fmt(item.entry_price, item.instrument_id))}</td><td>${esc(fmt(item.exit_price, item.instrument_id))}</td><td class="${tone(value)}">${esc(resultText(item, item.exit_price))}</td></tr>`);
      }
    }

    const tabs = weeksCache.slice(0, 8).map((week, index) => {
      const active = week.week_id === selected.week_id;
      const suffix = index === 0 && week.forecast_status === 'scheduled' ? ` · ${T.next}` : '';
      return `<button type="button" class="week-tab ${active ? 'active' : ''}" aria-pressed="${active}" onclick="window.BR_WEEKLY_SELECT('${esc(week.week_id)}')">${esc(week.week_id + suffix)}</button>`;
    }).join('');

    const cards = (selected.instruments || []).map((item) =>
      card(item, liveCache, auditState(selected, item, quarantineCache))).join('');

    $('app').innerHTML = `<section class="panel"><div class="week-tabs">${tabs}</div></section><section class="panel"><h2>${T.summary}</h2><div class="summary-top"><div class="kpi"><span>${T.total}</span><strong class="${tone(total)}">${esc(money(total))}</strong></div><div class="kpi"><span>${T.wins}</span><strong>${wins} / ${losses}</strong><small>${closed ? Math.round(wins / closed * 100) : 0}%</small></div></div><details class="history"><summary>${T.history}</summary><table><thead><tr><th>${T.week}</th><th>${T.instrument}</th><th>${T.position}</th><th>${T.open}</th><th>${T.close}</th><th>${T.result}</th></tr></thead><tbody>${rows.join('') || `<tr><td colspan="6">${T.no}</td></tr>`}</tbody></table></details></section><section class="panel"><h2>${T.current}: ${esc(selected.week_id)}</h2><div class="cards">${cards}</div></section><p class="legal">${T.legal}</p>`;
    document.dispatchEvent(new CustomEvent('br:weekly-rendered', { detail: selected }));
  }

  window.BR_WEEKLY_SELECT = (weekId) => {
    if (weeksCache.some((week) => week.week_id === weekId)) {
      selectedWeekId = weekId;
      render();
    }
  };
  window.BR_WEEKLY_INTEGRITY = { integrityIssues, auditState, plannedEntryIsValid };

  async function main() {
    try {
      const [live, weeks, quarantine] = await Promise.all([
        json('/data/investments/live_prices.json'),
        loadWeeks(),
        loadQuarantine(),
      ]);
      liveCache = live;
      weeksCache = weeks;
      quarantineCache = quarantineMap(quarantine);
      selectedWeekId = selectedWeekId || weeks[0]?.week_id || null;
      const updated = $('updated');
      if (updated) {
        const stale = liveIsStale(live);
        updated.textContent = stale ? T.stale : `${T.updated}: ${fmtTime(live.updated_at || '')}`;
        updated.classList.toggle('stale', stale);
      }
      render();
    } catch (_) {
      const updated = $('updated');
      if (updated) {
        updated.textContent = T.loadError;
        updated.classList.add('stale');
        updated.setAttribute('role', 'status');
      }
    }
  }

  main();
  setInterval(main, 15 * 60 * 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) main(); });
}());