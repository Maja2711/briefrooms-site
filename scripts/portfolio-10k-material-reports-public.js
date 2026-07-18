(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.BRMaterialReports = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
  const num = value => Number.isFinite(Number(value)) ? Number(value) : null;
  const classToken = value => String(value || '').toLowerCase().replace(/[^a-z0-9_-]/g, '');
  const safeHttpsUrl = value => {
    try {
      const parsed = new URL(String(value || ''));
      return parsed.protocol === 'https:' ? parsed.href : null;
    } catch (_) {
      return null;
    }
  };

  const COPY = {
    pl: {
      heading: 'Istotne raporty', empty: 'Brak nowych istotnych wydarzeń.', history: 'Starsze raporty',
      event: 'Wydarzenie', thesis: 'Wpływ na tezę', quote: 'Kurs', position: 'Pozycja', sources: 'Źródła',
      modelAction: 'Akcja modelu', units: 'Jednostki', cost: 'Koszt', value: 'Wartość', result: 'Wynik',
      instrument: 'Wpływ instrumentu', fx: 'Wpływ FX', indicative: 'cena orientacyjna',
      actions: {HOLD:'Trzymaj',ADD_SMALL:'Rozważ małą transzę',TRIM:'Przegląd ograniczenia',WAIT:'Poczekaj',THESIS_REVIEW:'Pilny przegląd tezy'},
      severity: {LOW:'NISKA',MEDIUM:'ŚREDNIA',HIGH:'WYSOKA',CRITICAL:'KRYTYCZNA'},
      types: {EARNINGS:'WYNIKI',GUIDANCE:'PROGNOZA',PRICE_ALERT:'ALERT CENOWY',ANALYST_CHANGE:'ANALITYCY',REGULATORY:'REGULACJE',POLITICAL:'POLITYKA',FX:'WALUTA',OPERATIONS:'OPERACJE',DIVIDEND:'DYWIDENDA',BUYBACK:'SKUP AKCJI',MATERIAL_NEWS:'ISTOTNA INFORMACJA'}
    },
    en: {
      heading: 'Material reports', empty: 'No new material events.', history: 'Older reports',
      event: 'Event', thesis: 'Thesis effect', quote: 'Quote', position: 'Position', sources: 'Sources',
      modelAction: 'Model action', units: 'Units', cost: 'Cost', value: 'Value', result: 'P/L',
      instrument: 'Instrument effect', fx: 'FX effect', indicative: 'indicative price',
      actions: {HOLD:'Hold',ADD_SMALL:'Consider a small tranche',TRIM:'Review trimming',WAIT:'Wait',THESIS_REVIEW:'Urgent thesis review'},
      severity: {LOW:'LOW',MEDIUM:'MEDIUM',HIGH:'HIGH',CRITICAL:'CRITICAL'},
      types: {EARNINGS:'EARNINGS',GUIDANCE:'GUIDANCE',PRICE_ALERT:'PRICE ALERT',ANALYST_CHANGE:'ANALYST CHANGE',REGULATORY:'REGULATORY',POLITICAL:'POLITICAL',FX:'FX',OPERATIONS:'OPERATIONS',DIVIDEND:'DIVIDEND',BUYBACK:'BUYBACK',MATERIAL_NEWS:'MATERIAL NEWS'}
    }
  };

  const localeFor = lang => lang === 'en' ? 'en-US' : 'pl-PL';
  const dateText = (value, lang, withTime = false) => {
    if (!value) return '';
    const parsed = new Date(String(value).length === 10 ? value + 'T12:00:00Z' : value);
    if (Number.isNaN(parsed.valueOf())) return esc(value);
    const options = {year:'numeric', month:'2-digit', day:'2-digit'};
    if (withTime) Object.assign(options, {hour:'2-digit', minute:'2-digit'});
    return parsed.toLocaleString(localeFor(lang), options);
  };
  const numeric = (value, lang, digits = 2) => {
    const parsed = num(value);
    return parsed === null ? null : parsed.toLocaleString(localeFor(lang), {minimumFractionDigits:digits, maximumFractionDigits:digits});
  };
  const money = (value, currency, lang, signed = false) => {
    const parsed = num(value);
    if (parsed === null) return null;
    const prefix = signed && parsed > 0 ? '+' : '';
    return prefix + numeric(parsed, lang, 2) + ' ' + esc(currency || '');
  };
  const percentage = (value, lang) => {
    const parsed = num(value);
    if (parsed === null) return null;
    return (parsed > 0 ? '+' : '') + numeric(parsed * 100, lang, 2) + '%';
  };
  const field = (report, name, lang) => report?.[`${name}_${lang}`] || '';

  function reportsForPosition(reports, positionId) {
    return (Array.isArray(reports) ? reports : [])
      .filter(report => report && report.position_id === positionId)
      .sort((left, right) => String(right.published_at || right.event_date || '').localeCompare(String(left.published_at || left.event_date || '')));
  }

  function renderSources(sources, copy) {
    const links = (Array.isArray(sources) ? sources : []).map(source => {
      const url = safeHttpsUrl(source?.url);
      if (!url) return '';
      return `<li><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(source?.label || copy.sources)}</a></li>`;
    }).filter(Boolean);
    return links.length ? `<div class="material-report__sources"><b>${esc(copy.sources)}</b><ul>${links.join('')}</ul></div>` : '';
  }

  function renderQuote(quote, lang, copy) {
    if (!quote || typeof quote !== 'object') return '';
    const value = money(quote.value, quote.currency, lang);
    if (!value) return '';
    const kind = String(quote.kind || '');
    const parts = [esc(kind), value, quote.market ? esc(quote.market) : '', quote.quoted_at ? dateText(quote.quoted_at, lang, true) : ''].filter(Boolean);
    const indicative = kind === 'INDICATIVE' ? `<em>${esc(copy.indicative)}</em>` : '';
    return `<div class="material-report__quote"><b>${esc(copy.quote)}</b><span>${parts.join(' · ')}</span>${indicative}</div>`;
  }

  function renderPosition(snapshot, lang, copy) {
    if (!snapshot || typeof snapshot !== 'object') return '';
    const currency = snapshot.position_currency || '';
    const rows = [];
    const units = numeric(snapshot.quantity, lang, 6);
    const localCost = money(snapshot.cost_basis_local, currency, lang);
    const localValue = money(snapshot.market_value_local, currency, lang);
    const localPnl = money(snapshot.unrealized_pnl_local, currency, lang, true);
    const percent = percentage(snapshot.unrealized_pnl_percent, lang);
    const costPln = money(snapshot.cost_basis_pln, 'PLN', lang);
    const valuePln = money(snapshot.market_value_pln, 'PLN', lang);
    const pnlPln = money(snapshot.unrealized_pnl_pln, 'PLN', lang, true);
    const instrument = money(snapshot.instrument_effect_pln, 'PLN', lang, true);
    const fx = money(snapshot.fx_effect_pln, 'PLN', lang, true);
    if (units) rows.push(`<span><small>${esc(copy.units)}</small><b>${units}</b></span>`);
    if (localCost) rows.push(`<span><small>${esc(copy.cost)}</small><b>${localCost}</b></span>`);
    if (localValue) rows.push(`<span><small>${esc(copy.value)}</small><b>${localValue}</b></span>`);
    if (localPnl) rows.push(`<span><small>${esc(copy.result)}</small><b>${localPnl}${percent ? ` (${percent})` : ''}</b></span>`);
    if (costPln) rows.push(`<span><small>${esc(copy.cost)} PLN</small><b>${costPln}</b></span>`);
    if (valuePln) rows.push(`<span><small>${esc(copy.value)} PLN</small><b>${valuePln}</b></span>`);
    if (pnlPln) rows.push(`<span><small>${esc(copy.result)} PLN</small><b>${pnlPln}</b></span>`);
    if (instrument) rows.push(`<span><small>${esc(copy.instrument)}</small><b>${instrument}</b></span>`);
    if (fx) rows.push(`<span><small>${esc(copy.fx)}</small><b>${fx}</b></span>`);
    return rows.length ? `<div class="material-report__position"><b>${esc(copy.position)}</b><div>${rows.join('')}</div></div>` : '';
  }

  function renderReport(report, lang = 'pl') {
    lang = lang === 'en' ? 'en' : 'pl';
    const copy = COPY[lang];
    const title = field(report, 'title', lang);
    const summary = field(report, 'summary', lang);
    const thesis = field(report, 'thesis_effect', lang);
    const severity = String(report?.severity || '');
    const impact = String(report?.impact || '');
    const action = String(report?.model_action || '');
    return `<article class="material-report impact-${classToken(impact)} severity-${classToken(severity)}">
      <div class="material-report__meta"><time datetime="${esc(report?.event_date || '')}">${dateText(report?.event_date, lang)}</time><div class="material-report__badges"><span>${esc(copy.types[report?.type] || report?.type || '')}</span><span>${esc(copy.severity[severity] || severity)}</span></div></div>
      <div class="material-report__body"><h5>${esc(title)}</h5>${summary ? `<p><b>${esc(copy.event)}:</b> ${esc(summary)}</p>` : ''}${thesis ? `<p><b>${esc(copy.thesis)}:</b> ${esc(thesis)}</p>` : ''}</div>
      ${renderQuote(report?.quote, lang, copy)}
      ${renderPosition(report?.position_snapshot, lang, copy)}
      <div class="model-action"><small>${esc(copy.modelAction)}</small><b>${esc(copy.actions[action] || action)}</b></div>
      ${renderSources(report?.sources, copy)}
    </article>`;
  }

  function renderForPosition({reports, position, lang = 'pl'} = {}) {
    lang = lang === 'en' ? 'en' : 'pl';
    const copy = COPY[lang];
    const selected = reportsForPosition(reports, position?.id);
    const visible = selected.slice(0, 3);
    const history = selected.slice(3);
    const body = visible.length ? visible.map(report => renderReport(report, lang)).join('') : `<p class="material-reports__empty">${esc(copy.empty)}</p>`;
    const older = history.length ? `<details class="material-reports__history"><summary>${esc(copy.history)} (${history.length})</summary>${history.map(report => renderReport(report, lang)).join('')}</details>` : '';
    return `<section class="material-reports"><div class="material-reports__header"><h4>${esc(copy.heading)}</h4></div>${body}${older}</section>`;
  }

  return {esc, safeHttpsUrl, reportsForPosition, renderReport, renderForPosition};
});
