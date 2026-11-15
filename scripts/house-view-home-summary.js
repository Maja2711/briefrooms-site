/* Shared read-only House View homepage projection.
 * It consumes the SAME frozen JSON as Long View; never reuses archival page HTML.
 * Public interface: BriefRoomsHouseViewHome.create(snapshot, lang, nowMs).
 * No AI calls, no trading-state writes, no invented targets/probabilities.
 */
((root, factory) => {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.BriefRoomsHouseViewHome = api;
})(typeof window !== 'undefined' ? window : null, () => {
  'use strict';

  const allowedBias = new Set(['defensive','neutral','mild_positive','unassessed']);
  const labels = {
    pl: {
      horizon: {defensive:'defensywny',neutral:'neutralny',mild_positive:'lekko pozytywny',unassessed:'bez oceny'},
      risk: 'Przewaga ryzyka korekty w krótkim terminie',
      neutral: 'Brak wyraźnej przewagi kierunku w najbliższym miesiącu',
      positive: 'Umiarkowana przewaga scenariusza odbicia',
      unknown: 'Brak podstaw do oceny kierunku na najbliższy miesiąc',
      stale: 'Analiza archiwalna — brak nowego House View',
      staleDescription: 'Ten raport ma więcej niż 45 dni. Nie traktuj wskazanych kierunków jako aktualnego stanowiska rynku; otwórz Long View, aby sprawdzić datę i źródła.',
      month: 'Data analizy',
      short: '1M', medium:'6M', long:'12M',
      pressure: 'Ryzyko dalszej korekty S&P 500 pozostaje podwyższone.',
      positiveContext: 'Momentum sprzyja odbiciu, ale samo nie potwierdza hossy.',
      neutralContext: 'Kierunek rynku wymaga potwierdzenia kolejnymi danymi.',
      down: 'Futures S&P 500 za 20 sesji',
      yield: 'UST 10Y',
      inflation: 'core PCE',
      growth: 'PKB USA q/q SAAR',
      oil: 'Brent',
      earnings: 'Dla mocniejszej tezy 6–12M potrzebne są potwierdzone EPS i wyceny.',
      stagnation: 'Rynek bez jednoznacznego impulsu cenowego.',
      higherYield: 'Wysokie rentowności ograniczają potencjał wzrostu P/E.',
      dated: 'Stan na'
    },
    en: {
      horizon: {defensive:'defensive',neutral:'neutral',mild_positive:'mildly positive',unassessed:'unassessed'},
      risk: 'Near-term S&P 500 correction risk dominates',
      neutral: 'No clear S&P 500 direction over the next month',
      positive: 'Moderate S&P 500 rebound potential',
      unknown: 'Insufficient evidence for a one-month S&P 500 direction',
      stale: 'Archived analysis — no current House View',
      staleDescription: 'This report is over 45 days old. Its directional views must not be treated as current; open Long View to check the date and sources.',
      month: 'Analysis as of',
      short: '1M', medium:'6M', long:'12M',
      pressure: 'Further near-term S&P 500 correction risk remains elevated.',
      positiveContext: 'Momentum supports a rebound, not necessarily a lasting rally.',
      neutralContext: 'The market direction needs confirmation from subsequent data.',
      down: 'S&P 500 futures over 20 sessions',
      yield: '10-year Treasury yield',
      inflation: 'core PCE',
      growth: 'U.S. GDP q/q SAAR',
      oil: 'Brent',
      earnings: 'A stronger 6–12M view needs verified EPS and valuations.',
      stagnation: 'Price momentum does not yet confirm a trend.',
      higherYield: 'High yields constrain potential P/E expansion.',
      dated: 'As of'
    }
  };

  const number = x => typeof x === 'number' && Number.isFinite(x);
  const dateOnly = x => typeof x === 'string' && /^20\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/.test(x);
  const view = x => !!x && typeof x === 'object' && allowedBias.has(x.bias) &&
                      Number.isInteger(x.risk_balance_score) &&
                      Number.isInteger(x.conviction) && x.conviction >= 0 && x.conviction <= 10;
  const pct = (v, lang) => v.toLocaleString(lang === 'pl' ? 'pl-PL' : 'en-GB', {
    minimumFractionDigits: 2, maximumFractionDigits: 2
  }) + '%';

  function validate(snapshot, nowMs) {
    if (!snapshot || typeof snapshot !== 'object' || !['1.0','2.0'].includes(snapshot.schema_version) ||
        snapshot.status !== 'published_research_only' || !/^\d{4}-(0[1-9]|1[0-2])$/.test(snapshot.month || '') ||
        !dateOnly(snapshot.as_of) || snapshot.as_of.slice(0,7) !== snapshot.month) return null;

    const asOf = Date.parse(snapshot.as_of + 'T00:00:00Z');
    if (!Number.isFinite(asOf) || new Date(asOf).toISOString().slice(0,10) !== snapshot.as_of ||
        !Number.isFinite(nowMs) || asOf > nowMs) return null;
    const views = snapshot.views;
    if (!views || !['1M','6M','12M'].every(h => view(views[h]))) return null;
    const inputs = snapshot.inputs;
    if (!inputs || !['gdp_qoq_saar_pct','core_pce_yoy_pct','us_10y_pct','brent_usd_bbl']
        .every(k => number(inputs[k]))) return null;
    if (!(inputs.brent_usd_bbl > 0 && inputs.us_10y_pct >= 0 &&
          inputs.core_pce_yoy_pct > -15 && inputs.core_pce_yoy_pct < 50)) return null;
    if (inputs.spx_futures_20d_return_pct != null && !number(inputs.spx_futures_20d_return_pct)) return null;
    if (snapshot.schema_version === '2.0' &&
        (!snapshot.research || !Array.isArray(snapshot.research.sections) ||
         !snapshot.research.sections.some(s => s.id === 'thesis' &&
           typeof s.pl === 'string' && typeof s.en === 'string'))) return null;
    return {asOf,ageDays:Math.floor((nowMs - asOf)/86400000)};
  }

  function create(snapshot, language = 'pl', nowMs = Date.now()) {
    const lang = language === 'en' ? 'en' : 'pl';
    const T = labels[lang];
    const verified = validate(snapshot, nowMs);
    if (!verified) return null;
    const shownDate = new Intl.DateTimeFormat(lang === 'pl' ? 'pl-PL' : 'en-GB', {
      timeZone:'UTC', day:'2-digit',month:'2-digit',year:'numeric'
    }).format(new Date(verified.asOf));
    const meta = T.month + ': ' + shownDate;
    if (verified.ageDays > 45) {
      return {
        title:T.stale, desc:T.staleDescription, metrics:[],
        meta, status:lang === 'pl' ? 'ARCHIWUM' : 'ARCHIVE',
        stale:true, asOf:snapshot.as_of, month:snapshot.month
      };
    }

    const V = snapshot.views, i = snapshot.inputs;
    const dir = V['1M'].bias;
    const titlePart = dir === 'defensive' ? T.risk :
      dir === 'neutral' ? T.neutral :
      dir === 'mild_positive' ? T.positive : T.unknown;

    const mom = i.spx_futures_20d_return_pct;
    const facts = [];
    if (number(mom)) facts.push(T.down + ': ' + (mom > 0 ? '+' : '') + pct(mom, lang));
    const fmtAmount = v => v.toLocaleString(lang === 'pl' ? 'pl-PL' : 'en-GB', {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    });
    facts.push(T.growth + ' ' + (i.gdp_qoq_saar_pct > 0 ? '+' : '') + pct(i.gdp_qoq_saar_pct, lang));
    facts.push(T.inflation + ' ' + pct(i.core_pce_yoy_pct, lang));
    facts.push(T.yield + ' ' + pct(i.us_10y_pct, lang));
    facts.push(T.oil + ' ' + fmtAmount(i.brent_usd_bbl) + ' USD/bbl');
    let implication = dir === 'defensive' ? T.pressure :
      dir === 'mild_positive' ? T.positiveContext : T.neutralContext;
    if (dir === 'unassessed') implication = T.stagnation;

    const short = facts.join(' · ') + '. ' + implication;
    const metrics = ['1M','6M','12M'].map(h => ({label:h,value:T.horizon[V[h].bias]}));
    return {
      title:titlePart,
      desc:short,
      metrics,
      meta,
      status:lang === 'pl' ? 'HOUSE VIEW' : 'HOUSE VIEW',
      stale:false, asOf:snapshot.as_of, month:snapshot.month,
      source:'data/investments/house_view_monthly_current.json'
    };
  }

  return Object.freeze({create, validate});
});
