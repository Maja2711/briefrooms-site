(function () {
  'use strict';

  const CFG = window.BR_WEEKLY || {};
  const L = CFG.lang === 'en' ? 'en' : 'pl';
  const T = {
    pl: {
      updated: 'Aktualizacja', summary: 'Podsumowanie', current: 'Najnowszy tydzień',
      open: 'Cena otwarcia', close: 'Cena zamknięcia', price: 'Cena teraz', priceTime: 'Stan na',
      status: 'Status', pnl: 'Wynik teraz', notional: 'Nominał', sl: 'Stop Loss', tp: 'Take Profit',
      rr: 'Risk/Reward', closed: 'zamknięta', active: 'w trakcie', neutral: 'bez pozycji',
      planned: 'oczekuje na otwarcie', history: 'Historia pozycji', week: 'Tydzień',
      instrument: 'Instrument', position: 'Pozycja', result: 'Wynik', points: 'pkt',
      analysis: 'Codzienna analiza pozycji', method: 'Metoda', regime: 'Reżim',
      analysisMissing: 'Analiza zostanie zapisana przy wejściu lub podczas dziennego przeglądu.',
      stale: 'Dane rynkowe są opóźnione. Pokazujemy ostatnią zapisaną cenę wraz z godziną jej aktualizacji.',
      loadError: 'Nie udało się pobrać aktualnych danych. Ostatnio zapisany widok pozostaje dostępny.',
      no: '—', audit: 'DANE W AUDYCIE', withheld: 'Wynik wstrzymany',
      auditText: 'Rekord został wyłączony z podsumowania i historii, ponieważ ceny, wynik lub chronologia nie przeszły kontroli spójności.',
      total: 'Łączny wynik zamkniętych pozycji', wins: 'Zyskowne / stratne',
      legal: 'Treści mają charakter edukacyjny i analityczny. To nie jest rekomendacja inwestycyjna ani porada finansowa. Nie podejmuj decyzji inwestycyjnych wyłącznie na podstawie tej strony.'
    },
    en: {
      updated: 'Updated', summary: 'Summary', current: 'Latest week',
      open: 'Open price', close: 'Close price', price: 'Price now', priceTime: 'As of',
      status: 'Status', pnl: 'Result now', notional: 'Notional', sl: 'Stop Loss', tp: 'Take Profit',
      rr: 'Risk/Reward', closed: 'closed', active: 'in progress', neutral: 'no position',
      planned: 'waiting for entry', history: 'Position history', week: 'Week',
      instrument: 'Instrument', position: 'Position', result: 'Result', points: 'pts',
      analysis: 'Daily position analysis', method: 'Method', regime: 'Regime',
      analysisMissing: 'Analysis will be saved at entry or during the daily review.',
      stale: 'Market data is delayed. The last stored price is shown together with its update time.',
      loadError: 'Current data could not be loaded. The last stored view remains available.',
      no: '—', audit: 'DATA UNDER AUDIT', withheld: 'Result withheld',
      auditText: 'This record is excluded from totals and history because its prices, result, or chronology failed the integrity check.',
      total: 'Total closed result', wins: 'Profitable / losing',
      legal: 'Content is educational and analytical. It is not investment advice or financial advice. Do not make investment decisions based only on this page.'
    }
  }[L];

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
  }
  function n(value) {
    const result = Number(value);
    return Number.isFinite(result) ? result : null;
  }
  function good(value) {
    const result = n(value);
    return result !== null && result > 0 ? result : null;
  }
  function parseTime(value) {
    if (!value) return null;
    const date = new Date(String(value));
    return Number.isNaN(date.getTime()) ? null : date;
  }
  function closeEnough(a, b, tolerance) {
    return a !== null && b !== null && Math.abs(a - b) <= tolerance;
  }
  function fmt(value, instrumentId) {
    const result = good(value);
    if (result === null) return T.no;
    const digits = instrumentId === 'eurusd' ? 5 : 2;
    return result.toLocaleString(L === 'pl' ? 'pl-PL' : 'en-US', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }
  function money(value) {
    const result = n(value);
    return result === null ? T.no : `${result >= 0 ? '+' : ''}${result.toLocaleString(L === 'pl' ? 'pl-PL' : 'en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })} USD`;
  }
  function signed(value, digits = 2) {
    const result = n(value);
    return result === null ? T.no : `${result >= 0 ? '+' : ''}${result.toLocaleString(L === 'pl' ? 'pl-PL' : 'en-US', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })}`;
  }
  function label(item) { return item[L === 'pl' ? 'label_pl' : 'label_en'] || item.symbol || item.instrument_id; }
  function dir(item) { return item.direction === 'short' ? 'short' : item.direction === 'long' ? 'long' : 'neutral'; }
  function dirText(item) { return dir(item) === 'neutral' ? (L === 'pl' ? 'NEUTRALNIE' : 'NEUTRAL') : dir(item).toUpperCase(); }
  function liveRecord(live, id) { return (((live || {}).prices || {})[id] || {}); }
  function livePrice(live, id) { return good(liveRecord(live, id).price); }
  function liveTimestamp(live, id) {
    const record = liveRecord(live, id);
    return record.current_price_updated_at || record.timestamp || '';
  }
  function notional(item) { return good(item.instrument_id === 'eurusd' ? item.notional_eur : item.notional_usd) || 10000; }
  function notionalText(item) {
    const currency = item.instrument_id === 'eurusd' ? 'EUR' : 'USD';
    return `${notional(item).toLocaleString(L === 'pl' ? 'pl-PL' : 'en-US', { maximumFractionDigits: 0 })} ${currency}`;
  }
  function riskPlan(item) {
    const plan = item && typeof item.risk_plan === 'object' ? item.risk_plan : {};
    return {
      sl: good(plan.stop_loss_price ?? item.stop_loss_price),
      tp: good(plan.take_profit_price ?? item.take_profit_price),
      rr: n(plan.reward_to_risk ?? item.risk_distance?.reward_to_risk),
    };
  }
  function rrText(value) {
    const result = n(value);
    return result === null || result <= 0 ? T.no : `1:${result.toLocaleString(L === 'pl' ? 'pl-PL' : 'en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }
  function calculatedMetrics(item, mark) {
    const entry = good(item.entry_price);
    const market = good(mark);
    if (dir(item) === 'neutral' || entry === null || market === null) return null;
    const move = dir(item) === 'long' ? market - entry : entry - market;
    const percent = move / entry * 100;
    const value = item.instrument_id === 'eurusd' ? move * notional(item) : percent / 100 * notional(item);
    const units = item.instrument_id === 'eurusd' ? move / 0.0001 : move;
    return { value, units, percent };
  }
  function metrics(item, mark) {
    const calculated = calculatedMetrics(item, mark);
    if (good(item.exit_price) === null) return calculated;
    const value = n(item.result_value);
    const units = n(item.result_units);
    const percent = n(item.result_percent);
    return {
      value: value !== null ? value : calculated?.value ?? null,
      units: units !== null ? units : calculated?.units ?? null,
      percent: percent !== null ? percent : calculated?.percent ?? null,
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
  function hasClose(item) { return dir(item) !== 'neutral' && good(item.entry_price) !== null && good(item.exit_price) !== null; }
  function isOpen(item) { return dir(item) !== 'neutral' && good(item.entry_price) !== null && !hasClose(item); }
  function status(item) {
    if (dir(item) === 'neutral') return T.neutral;
    if (hasClose(item)) return T.closed;
    if (isOpen(item)) return T.active;
    return T.planned;
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
    for (let i = 0; i < 26; i += 1) {
      const date = new Date();
      date.setDate(date.getDate() - i * 7);
      const id = isoWeek(date);
      if (!ids.includes(id)) ids.push(id);
    }
    const out = [];
    await Promise.all(ids.map((id) => json(`/data/investments/weekly/${id}.json`).then((week) => out.push(week)).catch(() => {})));
    return out.filter((week) => week && week.week_id).sort((a, b) => String(b.week_id).localeCompare(String(a.week_id)));
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
  function integrityIssues(item) {
    const issues = [];
    const side = dir(item);
    const entry = good(item.entry_price);
    const exit = good(item.exit_price);
    const entryAt = parseTime(item.entry_captured_at);
    const exitAt = parseTime(item.exit_captured_at);
    const plan = riskPlan(item);

    if (side !== 'neutral' && entry === null) issues.push('directional_missing_entry');
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
      const expected = calculatedMetrics(item, exit);
      const unitTolerance = item.instrument_id === 'eurusd' ? 0.15 : 0.05;
      if (n(item.result_units) !== null && !closeEnough(n(item.result_units), expected.units, unitTolerance)) issues.push('result_units_mismatch');
      if (n(item.result_percent) !== null && !closeEnough(n(item.result_percent), expected.percent, 0.02)) issues.push('result_percent_mismatch');
      if (n(item.result_value) !== null && !closeEnough(n(item.result_value), expected.value, 0.05)) issues.push('result_value_mismatch');

      const priceTolerance = item.instrument_id === 'eurusd' ? 0.00002 : 0.05;
      if (item.exit_reason === 'stop_loss' && plan.sl !== null) {
        if (side === 'long' && exit > plan.sl + priceTolerance) issues.push('stop_exit_above_stop');
        if (side === 'short' && exit < plan.sl - priceTolerance) issues.push('stop_exit_below_stop');
      }
      if (item.exit_reason === 'take_profit' && plan.tp !== null) {
        if (side === 'long' && exit < plan.tp - priceTolerance) issues.push('take_exit_below_target');
        if (side === 'short' && exit > plan.tp + priceTolerance) issues.push('take_exit_above_target');
      }
    }
    return issues;
  }
  function auditState(week, item, quarantine) {
    const key = `${week.week_id}/${item.instrument_id}`;
    const explicit = quarantine.get(key) || null;
    const issues = integrityIssues(item);
    if (explicit || issues.length) {
      return {
        withheld: true,
        reason: explicit?.reason || `${T.auditText} [${issues.join(', ')}]`,
        issues,
      };
    }
    return { withheld: false, reason: '', issues: [] };
  }
  function fmtTime(value) {
    if (!value) return '';
    const date = new Date(String(value));
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString(L === 'pl' ? 'pl-PL' : 'en-GB', {
      timeZone: 'Europe/Warsaw', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  }
  function liveIsStale(live) {
    const date = new Date(String((live || {}).updated_at || ''));
    return Number.isNaN(date.getTime()) || Date.now() - date.getTime() > 45 * 60 * 1000;
  }
  function latestAnalysis(item) {
    if (item.latest_daily_analysis && typeof item.latest_daily_analysis === 'object') return item.latest_daily_analysis;
    const rows = Array.isArray(item.daily_trading_analysis) ? item.daily_trading_analysis : [];
    return rows.length ? rows[rows.length - 1] : null;
  }
  function analysisHtml(item) {
    const analysis = latestAnalysis(item);
    if (!analysis) return `<div class="cell big analysis"><dt>${T.analysis}</dt><dd>${T.analysisMissing}</dd></div>`;
    const summary = analysis[L === 'pl' ? 'summary_pl' : 'summary_en'] || '';
    const meta = [
      analysis.selected_method ? `${T.method}: ${analysis.selected_method}` : '',
      analysis.weekly_regime ? `${T.regime}: ${analysis.weekly_regime}` : '',
      analysis.reviewed_at ? fmtTime(analysis.reviewed_at) : '',
    ].filter(Boolean).join(' · ');
    return `<div class="cell big analysis"><dt>${T.analysis}</dt><dd>${esc(summary || T.analysisMissing)}</dd><small>${esc(meta)}</small></div>`;
  }
  function auditCard(item, currentPrice, currentTime, state) {
    return `<article class="card ${esc(dir(item))} integrity-withheld"><div class="head"><div><p>${esc(label(item))}</p><h3>${esc(dirText(item))}</h3></div></div><dl class="grid"><div class="cell"><dt>${T.price}</dt><dd>${esc(fmt(currentPrice, item.instrument_id))}</dd><small>${currentTime ? esc(`${T.priceTime}: ${currentTime}`) : T.no}</small></div><div class="cell"><dt>${T.open}</dt><dd>${T.no}</dd></div><div class="cell"><dt>${T.close}</dt><dd>${T.no}</dd></div><div class="cell"><dt>${T.notional}</dt><dd>${T.no}</dd></div><div class="cell"><dt>${T.sl}</dt><dd>${T.no}</dd></div><div class="cell"><dt>${T.tp}</dt><dd>${T.no}</dd></div><div class="cell big"><dt>${T.rr}</dt><dd>${T.no}</dd></div><div class="cell big"><dt>${T.pnl}</dt><dd class="neutral">${esc(T.withheld)}</dd></div><div class="cell"><dt>${T.status}</dt><dd>${esc(T.audit)}</dd></div><div class="cell big analysis"><dt>${T.audit}</dt><dd>${esc(state.reason || T.auditText)}</dd></div></dl></article>`;
  }
  function render(weeks, live, quarantineData) {
    if (!weeks.length) return;
    const latest = weeks[0];
    const quarantine = quarantineMap(quarantineData);
    const stale = liveIsStale(live);
    const updated = $('updated');
    if (updated) {
      updated.textContent = stale ? T.stale : `${T.updated}: ${fmtTime((live || {}).updated_at || latest.forecast_created_at || '')}`;
      updated.classList.toggle('stale', stale);
    }

    let total = 0;
    let wins = 0;
    let losses = 0;
    let closed = 0;
    const rows = [];
    for (const week of weeks) {
      for (const item of (week.instruments || [])) {
        const state = auditState(week, item, quarantine);
        if (state.withheld || !hasClose(item)) continue;
        const result = metrics(item, item.exit_price);
        const value = result?.value ?? null;
        if (value === null) continue;
        closed += 1;
        total += value;
        if (value > 0) wins += 1;
        else if (value < 0) losses += 1;
        rows.push(`<tr><td>${esc(week.week_id)}</td><td>${esc(label(item))}</td><td>${esc(dirText(item))}</td><td>${esc(fmt(item.entry_price, item.instrument_id))}</td><td>${esc(fmt(item.exit_price, item.instrument_id))}</td><td class="${tone(value)}">${esc(resultText(item, item.exit_price))}</td></tr>`);
      }
    }

    const cards = (latest.instruments || []).map((item) => {
      const current = livePrice(live, item.instrument_id);
      const currentTime = fmtTime(liveTimestamp(live, item.instrument_id));
      const state = auditState(latest, item, quarantine);
      if (state.withheld) return auditCard(item, current, currentTime, state);
      const mark = hasClose(item) ? item.exit_price : current;
      const result = metrics(item, mark);
      const value = result?.value ?? null;
      const neutral = dir(item) === 'neutral';
      const risk = riskPlan(item);
      return `<article class="card ${esc(dir(item))}"><div class="head"><div><p>${esc(label(item))}</p><h3>${esc(dirText(item))}</h3></div></div><dl class="grid"><div class="cell"><dt>${T.price}</dt><dd>${esc(fmt(current, item.instrument_id))}</dd><small>${currentTime ? esc(`${T.priceTime}: ${currentTime}`) : T.no}</small></div><div class="cell"><dt>${T.open}</dt><dd>${neutral ? T.no : esc(fmt(item.entry_price, item.instrument_id))}</dd></div><div class="cell"><dt>${T.close}</dt><dd>${hasClose(item) ? esc(fmt(item.exit_price, item.instrument_id)) : neutral ? T.no : T.active}</dd></div><div class="cell"><dt>${T.notional}</dt><dd>${neutral ? T.no : esc(notionalText(item))}</dd></div><div class="cell"><dt>${T.sl}</dt><dd>${neutral ? T.no : esc(fmt(risk.sl, item.instrument_id))}</dd></div><div class="cell"><dt>${T.tp}</dt><dd>${neutral ? T.no : esc(fmt(risk.tp, item.instrument_id))}</dd></div><div class="cell big"><dt>${T.rr}</dt><dd>${neutral ? T.no : esc(rrText(risk.rr))}</dd></div><div class="cell big"><dt>${T.pnl}</dt><dd class="${tone(value)}">${neutral ? T.no : esc(resultText(item, mark))}</dd></div><div class="cell"><dt>${T.status}</dt><dd>${esc(status(item))}</dd></div>${analysisHtml(item)}</dl></article>`;
    }).join('');

    $('app').innerHTML = `<section class="panel"><h2>${T.summary}</h2><div class="summary-top"><div class="kpi"><span>${T.total}</span><strong class="${tone(total)}">${esc(money(total))}</strong></div><div class="kpi"><span>${T.wins}</span><strong>${wins} / ${losses}</strong><small>${closed ? Math.round(wins / closed * 100) : 0}%</small></div></div><details class="history"><summary>${T.history}</summary><table><thead><tr><th>${T.week}</th><th>${T.instrument}</th><th>${T.position}</th><th>${T.open}</th><th>${T.close}</th><th>${T.result}</th></tr></thead><tbody>${rows.join('') || `<tr><td colspan="6">${T.no}</td></tr>`}</tbody></table></details></section><section class="panel"><h2>${T.current}: ${esc(latest.week_id || '')}</h2><div class="cards">${cards}</div></section><p class="legal">${T.legal}</p>`;
    document.dispatchEvent(new CustomEvent('br:weekly-rendered', { detail: latest }));
  }

  let weeksCache = null;
  async function main() {
    try {
      const livePromise = json('/data/investments/live_prices.json');
      const weeksPromise = weeksCache ? Promise.resolve(weeksCache) : loadWeeks();
      const quarantinePromise = loadQuarantine();
      const [live, weeks, quarantine] = await Promise.all([livePromise, weeksPromise, quarantinePromise]);
      weeksCache = weeks;
      render(weeks, live, quarantine);
    } catch (_) {
      const updated = $('updated');
      if (updated) {
        updated.textContent = T.loadError;
        updated.classList.add('stale');
        updated.setAttribute('role', 'status');
      }
    }
  }

  window.BR_WEEKLY_INTEGRITY = { integrityIssues, auditState };
  main();
  setInterval(main, 15 * 60 * 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) main(); });
}());
