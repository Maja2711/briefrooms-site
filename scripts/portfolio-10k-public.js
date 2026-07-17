(() => {
  'use strict';

  const cfg = window.BR_PORTFOLIO_10K || { lang: 'pl' };
  const lang = cfg.lang === 'en' ? 'en' : 'pl';
  const locale = lang === 'pl' ? 'pl-PL' : 'en-US';
  const reportCurrency = lang === 'en' ? 'USD' : 'PLN';

  const T = lang === 'pl' ? {
    loading: 'Ładowanie danych portfela…',
    loadError: 'Nie udało się wczytać danych portfela. Zachowujemy ostatnie opublikowane dane; spróbuj ponownie później.',
    plannedTitle: 'Portfel oczekuje na otwarcie pozycji',
    plannedText: 'Nie pokazujemy zmyślonych cen zakupu. Model czeka na wspólną sesję USA i Europy oraz zsynchronizowane notowania.',
    activeTitle: 'Model aktywny',
    activeText: 'Ceny wejścia są zamrożone. Cotygodniowy przegląd aktualizuje wycenę, wyniki, trendy, kalendarz wyników i istotne nagłówki.',
    currentValue: 'Wartość portfela', totalReturn: 'Wynik od startu', benchmark: 'Benchmark', alpha: 'Różnica do benchmarku', cash: 'Gotówka',
    sinceStart: 'od startu', lastUpdate: 'Ostatnia aktualizacja', marketSession: 'sesja rynkowa', noData: '—',
    chartTitle: 'Ścieżka wartości', chartText: 'Portfel modelowy kontra globalny ETF benchmarkowy. Wartości są prezentowane w PLN.',
    portfolioLegend: 'Portfel', benchmarkLegend: 'Benchmark', allocationTitle: 'Docelowa alokacja', allocationText: 'Wagi startowe. Model nie rotuje pozycji tylko dlatego, że cena chwilowo spadła.',
    positionsTitle: 'Pozycje portfela', positionsText: 'Każda karta pokazuje wykonanie modelowe, bieżący udział, tezę, ryzyka i najnowszy przegląd.',
    target: 'Cel', currentWeight: 'Udział teraz', entry: 'Cena wejścia', current: 'Cena teraz', quantity: 'Liczba jednostek', entryDate: 'Data wejścia', pnl: 'Wynik', dividends: 'Dywidendy', score: 'Wskaźnik przeglądu', earnings: 'Najbliższe wyniki',
    details: 'Teza, ryzyka i informacje', thesis: 'Teza inwestycyjna', invalidation: 'Warunek unieważnienia', risks: 'Najważniejsze ryzyka', recentNews: 'Najnowsze informacje', noNews: 'Brak nowych nagłówków w ostatnim pobraniu.',
    flags: { HOLD: 'TRZYMAJ / BEZ ZMIAN', ADD_REVIEW: 'PRZEGLĄD DOKUPIENIA', TRIM_REVIEW: 'PRZEGLĄD REDUKCJI', THESIS_REVIEW: 'PILNY PRZEGLĄD TEZY' },
    weeklyTitle: 'Dziennik decyzji', weeklyText: 'Cotygodniowe wpisy pozostają w historii. Flaga modelu nie jest automatycznym zleceniem.',
    methodologyTitle: 'Jak działa model', methodologyText: 'Prosty publiczny proces: najpierw dane, potem teza i ryzyko, na końcu ewentualna rotacja.',
    method: [
      ['1', 'Wycena', 'Aktualizacja cen wszystkich instrumentów i kursów USD/PLN, EUR/PLN oraz DKK/PLN.'],
      ['2', 'Biznes i wyniki', 'Sprawdzenie kalendarza wyników oraz nagłówków dotyczących guidance, regulacji, marż i kluczowych produktów.'],
      ['3', 'Rynek i ryzyko', 'Trend 50/200 sesji, momentum 6M, zmienność, obsunięcie oraz odchylenie udziału od celu.'],
      ['4', 'Decyzja', 'Rotacja tylko po złamaniu tezy, materialnym ryzyku albo nadmiernej koncentracji — nie po samym spadku kursu.']
    ],
    auditTitle: 'Rejestr transakcji modelowych', auditText: 'Ceny wejścia i liczba jednostek są zamrażane przy uruchomieniu. Historia nie jest poprawiana wstecz.',
    symbol: 'Instrument', value: 'Wartość', status: 'Status', dataSource: 'Źródło danych', disclaimer: 'Treści są publicznym eksperymentem analitycznym i edukacyjnym. To nie jest rekomendacja inwestycyjna, indywidualna porada ani potwierdzenie transakcji na rachunku XTB. Ceny mogą różnić się od BID/ASK brokera, a wynik historyczny nie gwarantuje przyszłego.',
    planned: 'planowana', active: 'aktywna', stock: 'Akcja', etf: 'ETF', approximate: 'wartość orientacyjna', fxUnavailable: 'Brak kursu USD/PLN do przeliczenia'
  } : {
    loading: 'Loading portfolio data…',
    loadError: 'Portfolio data could not be loaded. The last published data is preserved; please try again later.',
    plannedTitle: 'Portfolio awaiting synchronized market entry',
    plannedText: 'No invented purchase prices are displayed. The model waits for a common US-European session and synchronized quotes.',
    activeTitle: 'Model active',
    activeText: 'Entry prices are frozen. The weekly review updates valuation, performance, trends, earnings calendar and material headlines.',
    currentValue: 'Portfolio value', totalReturn: 'Return since launch', benchmark: 'Benchmark', alpha: 'Difference vs benchmark', cash: 'Cash',
    sinceStart: 'since launch', lastUpdate: 'Last update', marketSession: 'market session', noData: '—',
    chartTitle: 'Value path', chartText: 'Model portfolio versus the global ETF benchmark. All portfolio-level monetary values are reported in USD.',
    portfolioLegend: 'Portfolio', benchmarkLegend: 'Benchmark', allocationTitle: 'Target allocation', allocationText: 'Starting weights. The model does not rotate a position simply because its price has fallen.',
    positionsTitle: 'Portfolio positions', positionsText: 'Each card shows model execution, current weight, thesis, risks and the latest review.',
    target: 'Target', currentWeight: 'Current weight', entry: 'Entry price', current: 'Current price', quantity: 'Units', entryDate: 'Entry date', pnl: 'P/L', dividends: 'Dividends', score: 'Review indicator', earnings: 'Next earnings',
    details: 'Thesis, risks and information', thesis: 'Investment thesis', invalidation: 'Invalidation condition', risks: 'Key risks', recentNews: 'Recent information', noNews: 'No new headlines in the latest retrieval.',
    flags: { HOLD: 'HOLD / NO CHANGE', ADD_REVIEW: 'REVIEW ADDING', TRIM_REVIEW: 'REVIEW TRIMMING', THESIS_REVIEW: 'URGENT THESIS REVIEW' },
    weeklyTitle: 'Decision journal', weeklyText: 'Weekly entries remain in history. A model flag is not an automatic order.',
    methodologyTitle: 'How the model works', methodologyText: 'A simple public process: data first, then thesis and risk, and only then possible rotation.',
    method: [
      ['1', 'Valuation', 'Update all instruments and FX rates. Internal PLN accounting is converted to USD for the English public view.'],
      ['2', 'Business and earnings', 'Check earnings dates and headlines concerning guidance, regulation, margins and key products.'],
      ['3', 'Market and risk', '50/200-session trend, 6M momentum, volatility, drawdown and weight deviation from target.'],
      ['4', 'Decision', 'Rotate only after thesis failure, material risk or excessive concentration — not because of a price decline alone.']
    ],
    auditTitle: 'Model transaction register', auditText: 'Entry prices and units are frozen at launch. History is not rewritten with hindsight.',
    symbol: 'Instrument', value: 'Value', status: 'Status', dataSource: 'Data source', disclaimer: 'This is a public analytical and educational experiment. It is not investment advice, personalised financial advice or confirmation of transactions in an XTB account. Prices may differ from broker bid/ask quotes, and past performance does not guarantee future results.',
    planned: 'planned', active: 'active', stock: 'Stock', etf: 'ETF', approximate: 'approximate value', fxUnavailable: 'USD/PLN reporting rate unavailable'
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const num = value => Number.isFinite(Number(value)) ? Number(value) : null;

  function usdPlnRate(data) {
    const direct = num(data?.reporting_fx?.usd_pln);
    if (direct && direct > 0) return direct;
    for (const p of data?.positions || []) {
      if (p.currency === 'USD') {
        const rate = num(p.current_fx_to_pln) || num(p.entry_fx_to_pln);
        if (rate && rate > 0) return rate;
      }
    }
    return null;
  }

  function reportingValue(plnValue, data, snapshot = null) {
    const value = num(plnValue);
    if (value === null) return null;
    if (reportCurrency === 'PLN') return value;
    const snapshotRate = num(snapshot?.reporting_usd_pln);
    const rate = snapshotRate && snapshotRate > 0 ? snapshotRate : usdPlnRate(data);
    return rate ? value / rate : null;
  }

  function money(plnValue, data, digits = 2, snapshot = null) {
    const value = reportingValue(plnValue, data, snapshot);
    if (value === null) return T.noData + (reportCurrency === 'USD' ? ' USD' : ' PLN');
    return value.toLocaleString(locale, {minimumFractionDigits: digits, maximumFractionDigits: digits}) + ' ' + reportCurrency;
  }

  const pct = (value, digits = 2) => {
    const n = num(value); if (n === null) return T.noData;
    return (n * 100).toLocaleString(locale, {minimumFractionDigits:digits, maximumFractionDigits:digits}) + '%';
  };
  const signedPct = value => { const n = num(value); return n === null ? T.noData : (n > 0 ? '+' : '') + pct(n); };
  const signedMoney = (value, data) => { const n = reportingValue(value, data); return n === null ? T.noData : (n > 0 ? '+' : '') + n.toLocaleString(locale,{minimumFractionDigits:2,maximumFractionDigits:2}) + ' ' + reportCurrency; };
  const tone = value => num(value) > 0 ? 'positive' : num(value) < 0 ? 'negative' : 'neutral';
  const price = (value, currency) => {
    const n = num(value); if (n === null) return T.noData;
    const digits = currency === 'DKK' ? 2 : n < 10 ? 4 : 2;
    return n.toLocaleString(locale, {minimumFractionDigits:digits, maximumFractionDigits:digits}) + ' ' + esc(currency || '');
  };
  const dateFmt = value => {
    if (!value) return T.noData;
    const d = new Date(String(value).length === 10 ? value + 'T12:00:00Z' : value);
    return Number.isNaN(d.valueOf()) ? esc(value) : d.toLocaleDateString(locale, {year:'numeric',month:'short',day:'2-digit'});
  };
  const text = (obj, key) => obj?.[`${key}_${lang}`] || obj?.[key] || '';
  const kpi = (label, value, sub, cls='') => `<article class="kpi"><small>${esc(label)}</small><strong class="${cls}">${esc(value)}</strong><span>${esc(sub || '')}</span></article>`;

  function renderKpis(data) {
    const ret = num(data.total_return_percent), bench = num(data.benchmark_return_percent);
    const alpha = ret !== null && bench !== null ? ret - bench : null;
    const fxNote = reportCurrency === 'USD' && !usdPlnRate(data) ? T.fxUnavailable : T.approximate;
    document.getElementById('kpis').innerHTML = [
      kpi(T.currentValue, money(data.total_value_pln ?? data.starting_capital_pln, data), fxNote),
      kpi(T.totalReturn, signedPct(ret), T.sinceStart, tone(ret)),
      kpi(T.benchmark, signedPct(bench), data.benchmark?.broker_symbol || 'FWIA.DE', tone(bench)),
      kpi(T.alpha, signedPct(alpha), T.portfolioLegend + ' − ' + T.benchmarkLegend, tone(alpha)),
      kpi(T.cash, money(data.cash_pln, data), `${T.lastUpdate}: ${dateFmt(data.last_updated_at)}`)
    ].join('');
  }

  function drawChart(data) {
    const box = document.getElementById('chart');
    const snapshots = (data.snapshots || []).filter(s => num(s.total_value_pln) !== null);
    if (!snapshots.length) { box.innerHTML = `<div class="loading">${esc(data.status === 'active' ? T.noData : T.plannedText)}</div>`; return; }
    const width=1000,height=270,pad=40;
    const portfolio=snapshots.map(s=>reportingValue(s.total_value_pln,data,s));
    const benchmark=snapshots.map(s=>reportingValue(s.benchmark_value_pln,data,s));
    const values=[...portfolio,...benchmark].filter(v=>v!==null);
    if (!values.length) { box.innerHTML=`<div class="loading">${esc(T.fxUnavailable)}</div>`; return; }
    let min=Math.min(...values),max=Math.max(...values); if(max===min){min-=1;max+=1;}
    const x=i=>snapshots.length===1?width/2:pad+i*(width-pad*2)/(snapshots.length-1);
    const y=v=>height-pad-(v-min)*(height-pad*2)/(max-min);
    const path=series=>series.map((v,i)=>v===null?'':`${i===0?'M':'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const grid=[0,1,2,3,4].map(i=>{const yy=pad+i*(height-pad*2)/4,val=max-i*(max-min)/4;return `<line x1="${pad}" y1="${yy}" x2="${width-pad}" y2="${yy}" stroke="rgba(255,255,255,.08)"/><text x="2" y="${yy+4}" fill="#7f95aa" font-size="11">${Math.round(val)}</text>`;}).join('');
    box.innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(T.chartTitle)}">${grid}<path d="${path(portfolio)}" fill="none" stroke="#52e38b" stroke-width="4" stroke-linecap="round"/><path d="${path(benchmark)}" fill="none" stroke="#7bb8ff" stroke-width="3" stroke-dasharray="8 7" stroke-linecap="round"/></svg>`;
  }

  function renderAllocation(data) {
    document.getElementById('allocation').innerHTML=(data.positions||[]).map(p=>`<article class="alloc"><div class="alloc-top"><b>${esc(p.broker_symbol)}</b><span>${pct(p.target_weight,0)}</span></div><small>${esc(p.label)}</small><div class="bar"><i style="width:${Math.max(1,(num(p.target_weight)||0)*100)}%"></i></div></article>`).join('');
  }

  function signalLabel(code) {
    const pl={price_above_ma200:'cena > MA200',ma50_above_ma200:'MA50 > MA200',positive_six_month_momentum:'momentum 6M +',drawdown_below_twenty_percent:'obsunięcie < 20%',contained_short_term_volatility:'zmienność pod kontrolą',price_below_ma200:'cena < MA200',ma50_below_ma200:'MA50 < MA200',negative_six_month_momentum:'momentum 6M −',drawdown_above_twenty_percent:'obsunięcie > 20%',elevated_short_term_volatility:'wysoka zmienność',material_news_headline_requires_review:'istotny nagłówek do sprawdzenia'};
    const en={price_above_ma200:'price > MA200',ma50_above_ma200:'MA50 > MA200',positive_six_month_momentum:'positive 6M momentum',drawdown_below_twenty_percent:'drawdown < 20%',contained_short_term_volatility:'contained volatility',price_below_ma200:'price < MA200',ma50_below_ma200:'MA50 < MA200',negative_six_month_momentum:'negative 6M momentum',drawdown_above_twenty_percent:'drawdown > 20%',elevated_short_term_volatility:'high volatility',material_news_headline_requires_review:'material headline to review'};
    return (lang==='pl'?pl:en)[code]||String(code).replaceAll('_',' ');
  }
  const newsList=items=>!items?.length?`<li>${esc(T.noNews)}</li>`:items.map(item=>`<li><a href="${esc(item.link)}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a><span class="news-meta">${esc(item.source||'')}${item.published?' · '+esc(item.published):''}</span></li>`).join('');

  function positionCard(p,data) {
    const flag=p.review_flag||'HOLD',cardTone=flag==='THESIS_REVIEW'?'alert':flag!=='HOLD'?'review':'',pnlPct=num(p.pnl_percent);
    const riskSignals=(p.risk_signals||[]).map(s=>`<span class="signal risk">${esc(signalLabel(s))}</span>`).join('');
    const positiveSignals=(p.positive_signals||[]).map(s=>`<span class="signal">${esc(signalLabel(s))}</span>`).join('');
    const riskList=(lang==='pl'?p.risks_pl:p.risks_en)||[];
    return `<article class="position ${cardTone}"><div class="position-head"><div><div class="symbol">${esc(p.broker_symbol)}</div><h3>${esc(p.label)}</h3><div class="type">${esc(p.asset_type==='ETF'?T.etf:T.stock)}</div></div><span class="flag ${esc(flag)}">${esc(T.flags[flag]||flag)}</span></div><div class="position-value"><div><small>${esc(T.value)}</small><strong>${esc(money(p.current_value_pln??p.entry_value_pln,data))}</strong></div><div><small>${esc(T.pnl)}</small><b class="${tone(pnlPct)}">${esc(signedPct(pnlPct))} · ${esc(signedMoney(p.pnl_pln,data))}</b></div></div><div class="metrics"><div class="metric"><small>${esc(T.target)}</small><b>${esc(pct(p.target_weight,1))}</b></div><div class="metric"><small>${esc(T.currentWeight)}</small><b>${esc(pct(p.current_weight,1))}</b></div><div class="metric"><small>${esc(T.score)}</small><b>${num(p.model_score)===null?T.noData:esc(String(p.model_score)+'/100')}</b></div><div class="metric"><small>${esc(T.entry)}</small><b>${price(p.entry_price,p.currency)}</b></div><div class="metric"><small>${esc(T.current)}</small><b>${price(p.current_price,p.currency)}</b></div><div class="metric"><small>${esc(T.quantity)}</small><b>${num(p.quantity)===null?T.noData:num(p.quantity).toLocaleString(locale,{maximumFractionDigits:6})}</b></div><div class="metric"><small>${esc(T.entryDate)}</small><b>${dateFmt(p.entry_date)}</b></div><div class="metric"><small>${esc(T.dividends)}</small><b>${money(p.dividends_pln,data)}</b></div><div class="metric"><small>${esc(T.earnings)}</small><b>${dateFmt(p.next_earnings_date)}</b></div></div><p class="thesis">${esc(text(p,'thesis'))}</p><div class="signals">${positiveSignals}${riskSignals}</div><details><summary>${esc(T.details)}</summary><div class="detail-grid"><section class="detail-box"><h4>${esc(T.thesis)}</h4><p>${esc(text(p,'thesis'))}</p><h4>${esc(T.invalidation)}</h4><p>${esc(text(p,'invalidation'))}</p></section><section class="detail-box"><h4>${esc(T.risks)}</h4><ul>${riskList.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></section><section class="detail-box" style="grid-column:1/-1"><h4>${esc(T.recentNews)}</h4><ul class="news-list">${newsList(p.recent_news)}</ul></section></div></details></article>`;
  }

  const renderPositions=data=>{document.getElementById('positions').innerHTML=(data.positions||[]).map(p=>positionCard(p,data)).join('');};
  const renderReviews=data=>{const reviews=[...(data.weekly_reviews||[])].reverse().slice(0,12);document.getElementById('reviews').innerHTML=reviews.length?reviews.map(r=>`<article class="review"><time>${esc(r.week_id||'')} · ${dateFmt(r.reviewed_at)}</time><p>${esc(lang==='pl'?r.summary_pl:r.summary_en)}</p></article>`).join(''):`<div class="loading">${esc(T.noData)}</div>`;};
  const renderMethod=()=>{document.getElementById('method').innerHTML=T.method.map(([n,h,p])=>`<article class="method-card"><b>${esc(n)}</b><h3>${esc(h)}</h3><p>${esc(p)}</p></article>`).join('');};
  function renderAudit(data){document.getElementById('audit-body').innerHTML=(data.positions||[]).map(p=>`<tr><td><b>${esc(p.broker_symbol)}</b><br>${esc(p.label)}</td><td>${esc(pct(p.target_weight,1))}</td><td>${dateFmt(p.entry_date)}</td><td>${price(p.entry_price,p.currency)}</td><td>${num(p.quantity)===null?T.noData:num(p.quantity).toLocaleString(locale,{maximumFractionDigits:6})}</td><td>${money(p.entry_value_pln,data)}</td><td>${esc(p.status==='active'?T.active:T.planned)}</td></tr>`).join('');}
  function renderStatus(data){const box=document.getElementById('status-note'),active=data.status==='active';box.className='status-note'+(active?' ok':'');box.innerHTML=`<b>${esc(active?T.activeTitle:T.plannedTitle)}</b><br>${esc(active?T.activeText:(text(data,'pending_entry_rule')||T.plannedText))}`;document.getElementById('updated-meta').textContent=`${T.lastUpdate}: ${dateFmt(data.last_updated_at)} · ${T.marketSession}: ${dateFmt(data.last_market_session)}`;document.getElementById('source-meta').textContent=`${T.dataSource}: ${data.data_source||T.noData}`;const note=lang==='pl'?(data.broker_note_pl||''):(data.broker_note_en||'');document.getElementById('broker-note').textContent=note+(lang==='en'?' All portfolio-level values on this page are displayed in USD.':'');}
  function render(data){renderStatus(data);renderKpis(data);drawChart(data);renderAllocation(data);renderPositions(data);renderReviews(data);renderMethod();renderAudit(data);}
  async function load(){document.getElementById('positions').innerHTML=`<div class="loading">${esc(T.loading)}</div>`;try{const response=await fetch('/data/investments/portfolio_10k.json?v='+Date.now(),{cache:'no-store'});if(!response.ok)throw new Error('HTTP '+response.status);render(await response.json());}catch(error){document.getElementById('positions').innerHTML=`<div class="error">${esc(T.loadError)}</div>`;document.getElementById('status-note').innerHTML=`<b>${esc(T.loadError)}</b>`;}}
  document.querySelectorAll('[data-i18n]').forEach(node=>{const key=node.dataset.i18n;if(T[key])node.textContent=T[key];});
  document.getElementById('legal').textContent=T.disclaimer;
  load();
})();
