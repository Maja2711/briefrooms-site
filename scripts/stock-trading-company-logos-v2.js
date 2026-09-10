(() => {
  'use strict';

  const root = document.getElementById('stock-trading-portfolio-root');
  if (!root) return;

  const DOMAINS = {
    MU:'micron.com', NVDA:'nvidia.com', AAPL:'apple.com', MSFT:'microsoft.com', AMZN:'amazon.com', META:'meta.com',
    TSLA:'tesla.com', GOOGL:'google.com', GOOG:'google.com', NFLX:'netflix.com', ORCL:'oracle.com', AMD:'amd.com',
    INTC:'intel.com', QCOM:'qualcomm.com', AVGO:'broadcom.com', IBM:'ibm.com', CRM:'salesforce.com', NOW:'servicenow.com',
    PLTR:'palantir.com', UBER:'uber.com', ABNB:'airbnb.com', PYPL:'paypal.com', JPM:'jpmorganchase.com', BAC:'bankofamerica.com',
    GS:'goldmansachs.com', MS:'morganstanley.com', V:'visa.com', MA:'mastercard.com', KO:'coca-colacompany.com', PEP:'pepsico.com',
    MCD:'mcdonalds.com', NKE:'nike.com', DIS:'thewaltdisneycompany.com', WMT:'walmart.com', COST:'costco.com', XOM:'corporate.exxonmobil.com',
    CVX:'chevron.com', CAT:'caterpillar.com', BA:'boeing.com', GE:'ge.com', LLY:'lilly.com', JNJ:'jnj.com', PFE:'pfizer.com',
    MRK:'merck.com', ABBV:'abbvie.com', TMO:'thermofisher.com', UNH:'unitedhealthgroup.com', HD:'homedepot.com',

    JSW:'jsw.pl', PKN:'orlen.pl', ORLEN:'orlen.pl', KGHM:'kghm.com', PKO:'pkobp.pl', PZU:'pzu.pl', PEO:'pekao.com.pl',
    MBK:'mbank.pl', ALR:'aliorbank.pl', ING:'ing.pl', SAN:'santander.pl', SPL:'santander.pl', CDR:'cdprojekt.com', LPP:'lppsa.com',
    DNP:'grupadino.pl', CCC:'ccc.eu', OPL:'orange.pl', CPS:'grupapolsatplus.pl', PGE:'gkpge.pl', TPE:'tauron.pl', ENA:'enea.pl',
    ALE:'allegro.eu', XTB:'xtb.com', ACP:'asseco.com', ASB:'assecobs.pl', EUR:'eurocash.pl', KRU:'kruk.eu', MIL:'bankmillennium.pl',
    BDX:'budimex.pl', BUD:'budimex.pl', ATT:'grupaazoty.com', PKP:'pkpcargo.com'
  };

  const normalizeTicker = (value) => String(value || '').toUpperCase().replace(/\.WA$/,'').replace(/[^A-Z0-9]/g,'');
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));

  function resolveDomain(ticker, name) {
    const t = normalizeTicker(ticker);
    if (DOMAINS[t]) return DOMAINS[t];
    const n = String(name || '').toLowerCase();
    if (n.includes('micron')) return 'micron.com';
    if (n.includes('nvidia')) return 'nvidia.com';
    if (n.includes('apple')) return 'apple.com';
    if (n.includes('microsoft')) return 'microsoft.com';
    if (n.includes('amazon')) return 'amazon.com';
    if (n.includes('alphabet') || n.includes('google')) return 'google.com';
    if (n.includes('meta platform')) return 'meta.com';
    if (n.includes('tesla')) return 'tesla.com';
    if (n.includes('jsw') || n.includes('jastrzęb') || n.includes('jastrzeb')) return 'jsw.pl';
    if (n.includes('orlen')) return 'orlen.pl';
    if (n.includes('kghm')) return 'kghm.com';
    if (n.includes('pko')) return 'pkobp.pl';
    if (n.includes('pekao')) return 'pekao.com.pl';
    if (n.includes('pzu')) return 'pzu.pl';
    if (n.includes('cd projekt')) return 'cdprojekt.com';
    if (n.includes('allegro')) return 'allegro.eu';
    if (n.includes('xtb')) return 'xtb.com';
    return '';
  }

  function sourcesFor(domain) {
    if (!domain) return [];
    return [
      `https://logo.clearbit.com/${domain}?size=192`,
      `https://www.google.com/s2/favicons?sz=128&domain_url=https://${domain}`,
      `https://icons.duckduckgo.com/ip3/${domain}.ico`
    ];
  }

  function enhance(position) {
    if (position.classList.contains('str-position-empty')) return;
    const mark = position.querySelector('.str-company-mark');
    if (!mark) return;

    const ticker = normalizeTicker(position.querySelector('.str-company-name h3')?.textContent || mark.querySelector('strong')?.textContent || '');
    const name = position.querySelector('.str-company-name span')?.textContent?.trim() || ticker;
    if (!ticker) return;

    const key = `${ticker}|${name}`;
    if (mark.dataset.logoV2Key === key) return;
    mark.dataset.logoV2Key = key;

    const domain = resolveDomain(ticker, name);
    const sources = sourcesFor(domain);
    mark.classList.add('br-company-logo-v2');
    mark.classList.toggle('is-logo-fallback', !sources.length);

    mark.innerHTML = `${sources.length ? `<img class="br-company-logo-img" src="${esc(sources[0])}" alt="${esc(name)} — logo spółki" loading="eager" referrerpolicy="no-referrer">` : ''}<span class="br-company-logo-fallback" aria-hidden="true">${esc(ticker.slice(0,4))}</span><strong>${esc(ticker)}</strong>`;

    const img = mark.querySelector('.br-company-logo-img');
    if (!img) return;

    let index = 0;
    const next = () => {
      index += 1;
      if (index < sources.length) {
        img.src = sources[index];
      } else {
        mark.classList.add('is-logo-fallback');
      }
    };
    img.addEventListener('error', next);
    img.addEventListener('load', () => mark.classList.remove('is-logo-fallback'));
  }

  function scan() {
    root.querySelectorAll('.str-position').forEach(enhance);
  }

  let frame = 0;
  const schedule = () => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      scan();
    });
  };

  new MutationObserver(schedule).observe(root, {childList:true, subtree:true});
  document.addEventListener('DOMContentLoaded', schedule, {once:true});
  window.addEventListener('load', schedule, {once:true});
  schedule();
})();
