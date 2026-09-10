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
    BDX:'budimex.pl', BUD:'budimex.pl', ATT:'grupaazoty.com', PKP:'pkpcargo.com', WPL:'wirtualnemedia.pl'
  };

  const normalizeTicker = (value) => String(value || '').toUpperCase().replace(/\.WA$/,'').replace(/[^A-Z0-9]/g,'');
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));

  function resolveDomain(ticker, name) {
    const t = normalizeTicker(ticker);
    if (DOMAINS[t]) return DOMAINS[t];

    const n = String(name || '').toLowerCase();
    if (n.includes('micron')) return 'micron.com';
    if (n.includes('nvidia')) return 'nvidia.com';
    if (n.includes('apple')) return 'apple.com';
    if (n.includes('microsoft')) return 'microsoft.com';
    if (n.includes('amazon')) return 'amazon.com';
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

  function providers(domain) {
    if (!domain) return [];
    return [
      `https://logo.clearbit.com/${domain}?size=160`,
      `https://www.google.com/s2/favicons?sz=128&domain_url=https://${domain}`
    ];
  }

  function enhance(mark, ticker, name) {
    const t = normalizeTicker(ticker);
    if (!t) return;
    if (mark.dataset.companyLogoTicker === t) return;

    const domain = resolveDomain(t, name);
    const sources = providers(domain);
    mark.dataset.companyLogoTicker = t;
    mark.classList.add('br-company-logo');
    mark.classList.toggle('is-logo-fallback', !sources.length);

    const fallback = `<span class="br-company-logo-fallback" aria-hidden="true">${escapeHtml(t.slice(0,4))}</span>`;
    const img = sources.length ? `<img class="br-company-logo-img" src="${escapeHtml(sources[0])}" alt="${escapeHtml(name || t)} logo" loading="lazy" referrerpolicy="no-referrer">` : '';
    mark.innerHTML = `${img}${fallback}<strong>${escapeHtml(t)}</strong>`;

    const image = mark.querySelector('.br-company-logo-img');
    if (!image) return;
    let sourceIndex = 0;
    image.addEventListener('error', () => {
      sourceIndex += 1;
      if (sourceIndex < sources.length) {
        image.src = sources[sourceIndex];
      } else {
        mark.classList.add('is-logo-fallback');
      }
    });
    image.addEventListener('load', () => mark.classList.remove('is-logo-fallback'));
  }

  function scan() {
    root.querySelectorAll('.str-position').forEach((position) => {
      const mark = position.querySelector('.str-company-mark');
      if (!mark) return;
      const ticker = position.querySelector('.str-company-name h3')?.textContent?.trim() || mark.querySelector('strong')?.textContent?.trim() || '';
      const name = position.querySelector('.str-company-name span')?.textContent?.trim() || '';
      enhance(mark, ticker, name);
    });
  }

  let scheduled = false;
  const scheduleScan = () => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      scan();
    });
  };

  const observer = new MutationObserver(scheduleScan);
  observer.observe(root, { childList:true, subtree:true });
  scan();
})();
