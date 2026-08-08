(() => {
  'use strict';

  const cfg = window.BR_PORTFOLIO_10K || { lang: 'pl' };
  const lang = cfg.lang === 'en' ? 'en' : 'pl';
  const endpoint = '/data/ai_tournament/company_profiles.json';
  const state = {
    profiles: {},
    activeTrigger: null,
  };

  const T = lang === 'en' ? {
    dialogLabel: 'Company profile',
    close: 'Close',
    open: 'Open company profile',
    sector: 'Business area',
    weight: 'Portfolio weight',
    ticker: 'Ticker',
    unavailable: 'Company description is not available.',
  } : {
    dialogLabel: 'Profil spółki',
    close: 'Zamknij',
    open: 'Otwórz profil spółki',
    sector: 'Obszar działalności',
    weight: 'Udział w portfelu',
    ticker: 'Ticker',
    unavailable: 'Opis spółki nie jest dostępny.',
  };

  function installStyles() {
    if (document.getElementById('ait-company-profile-style')) return;
    const style = document.createElement('style');
    style.id = 'ait-company-profile-style';
    style.textContent = `
      .ait-company-trigger{appearance:none;border:1px solid #dce5ef;border-radius:9px;background:#eef3f8;color:#263b56;padding:6px 9px;font:inherit;font-size:10px;font-weight:850;line-height:1;cursor:pointer;transition:border-color .16s ease,background .16s ease,transform .16s ease,box-shadow .16s ease}
      .ait-company-trigger:hover{background:#e5edf6;border-color:#b7c8dc;transform:translateY(-1px)}
      .ait-company-trigger:focus-visible{outline:3px solid rgba(47,115,237,.22);outline-offset:2px;border-color:#2f73ed}
      .ait-company-trigger .ait-company-ticker{font-weight:950}.ait-company-trigger .ait-company-weight{margin-left:4px;color:#60738a;font-weight:800}
      .ait-company-modal{position:fixed;inset:0;z-index:10020;display:grid;place-items:center;padding:24px;background:rgba(9,24,42,.52);backdrop-filter:blur(5px);opacity:0;visibility:hidden;transition:opacity .16s ease,visibility .16s ease}
      .ait-company-modal.open{opacity:1;visibility:visible}.ait-company-dialog{width:min(470px,calc(100vw - 32px));border:1px solid rgba(210,220,232,.95);border-radius:20px;background:#fff;color:#172b45;box-shadow:0 26px 80px rgba(6,24,48,.28);transform:translateY(8px) scale(.985);transition:transform .16s ease;overflow:hidden}
      .ait-company-modal.open .ait-company-dialog{transform:translateY(0) scale(1)}.ait-company-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:20px 20px 15px;border-bottom:1px solid #e7edf4;background:linear-gradient(145deg,#fbfdff,#f4f8fc)}
      .ait-company-identity{display:flex;align-items:center;gap:13px;min-width:0}.ait-company-symbol{display:grid;place-items:center;flex:0 0 auto;width:50px;height:50px;border-radius:15px;background:#e9f0ff;color:#255fc4;font-size:13px;font-weight:950;letter-spacing:.03em}
      .ait-company-title{min-width:0}.ait-company-title small{display:block;color:#78879a;font-size:10px;font-weight:850;text-transform:uppercase;letter-spacing:.08em}.ait-company-title h3{margin:4px 0 0;font-size:20px;line-height:1.2;color:#172b45}
      .ait-company-close{display:grid;place-items:center;flex:0 0 auto;width:34px;height:34px;border:1px solid #dbe4ee;border-radius:10px;background:#fff;color:#52667f;font-size:19px;line-height:1;cursor:pointer}.ait-company-close:hover{background:#eef3f8;color:#172b45}.ait-company-close:focus-visible{outline:3px solid rgba(47,115,237,.22);outline-offset:2px}
      .ait-company-body{padding:18px 20px 20px}.ait-company-sector{display:inline-flex;align-items:center;min-height:28px;padding:6px 9px;border-radius:999px;background:#e8f7ef;color:#147946;font-size:10px;font-weight:850}.ait-company-description{margin:14px 0 17px;color:#4f6075;font-size:13px;line-height:1.65}
      .ait-company-facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.ait-company-fact{padding:11px 12px;border-radius:12px;background:#f6f8fb;border:1px solid #e7edf4}.ait-company-fact small{display:block;color:#8290a1;font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.06em}.ait-company-fact strong{display:block;margin-top:4px;color:#172b45;font-size:13px}
      body.ait-company-modal-open{overflow:hidden}
      @media(max-width:560px){.ait-company-modal{padding:12px;align-items:end}.ait-company-dialog{width:100%;border-radius:20px 20px 14px 14px}.ait-company-head{padding:17px 16px 14px}.ait-company-body{padding:16px}.ait-company-title h3{font-size:18px}}
      @media(prefers-reduced-motion:reduce){.ait-company-trigger,.ait-company-modal,.ait-company-dialog{transition:none!important}}
    `;
    document.head.appendChild(style);
  }

  function ensureModal() {
    let modal = document.getElementById('ait-company-modal');
    if (modal) return modal;

    modal = document.createElement('div');
    modal.id = 'ait-company-modal';
    modal.className = 'ait-company-modal';
    modal.setAttribute('aria-hidden', 'true');
    modal.innerHTML = `
      <section class="ait-company-dialog" role="dialog" aria-modal="true" aria-labelledby="ait-company-name" aria-describedby="ait-company-description" tabindex="-1">
        <header class="ait-company-head">
          <div class="ait-company-identity">
            <span class="ait-company-symbol" id="ait-company-symbol"></span>
            <div class="ait-company-title">
              <small>${T.dialogLabel}</small>
              <h3 id="ait-company-name"></h3>
            </div>
          </div>
          <button class="ait-company-close" type="button" aria-label="${T.close}">×</button>
        </header>
        <div class="ait-company-body">
          <span class="ait-company-sector" id="ait-company-sector"></span>
          <p class="ait-company-description" id="ait-company-description"></p>
          <div class="ait-company-facts">
            <div class="ait-company-fact"><small>${T.ticker}</small><strong id="ait-company-ticker"></strong></div>
            <div class="ait-company-fact"><small>${T.weight}</small><strong id="ait-company-weight"></strong></div>
          </div>
        </div>
      </section>`;
    document.body.appendChild(modal);

    modal.querySelector('.ait-company-close').addEventListener('click', closeModal);
    modal.addEventListener('mousedown', event => {
      if (event.target === modal) closeModal();
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && modal.classList.contains('open')) closeModal();
    });
    return modal;
  }

  function closeModal() {
    const modal = document.getElementById('ait-company-modal');
    if (!modal || !modal.classList.contains('open')) return;
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('ait-company-modal-open');
    const trigger = state.activeTrigger;
    state.activeTrigger = null;
    window.setTimeout(() => {
      if (trigger && document.contains(trigger)) trigger.focus();
    }, 0);
  }

  function openModal(trigger) {
    const ticker = String(trigger.dataset.ticker || '').toUpperCase();
    const profile = state.profiles[ticker];
    if (!profile) return;

    const modal = ensureModal();
    const description = lang === 'en' ? profile.description_en : profile.description_pl;
    const sector = lang === 'en' ? profile.sector_en : profile.sector_pl;
    modal.querySelector('#ait-company-symbol').textContent = ticker;
    modal.querySelector('#ait-company-name').textContent = profile.name || ticker;
    modal.querySelector('#ait-company-sector').textContent = sector || T.sector;
    modal.querySelector('#ait-company-description').textContent = description || T.unavailable;
    modal.querySelector('#ait-company-ticker').textContent = ticker;
    modal.querySelector('#ait-company-weight').textContent = trigger.dataset.weight || '—';

    state.activeTrigger = trigger;
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('ait-company-modal-open');
    window.requestAnimationFrame(() => modal.querySelector('.ait-company-close').focus());
  }

  function parseHolding(node) {
    const text = String(node.textContent || '').trim();
    const match = text.match(/^([A-Z][A-Z0-9.-]{0,9})\s*(.*)$/);
    if (!match) return null;
    return { ticker: match[1].toUpperCase(), weight: match[2].trim() };
  }

  function decorateHoldings(root = document) {
    root.querySelectorAll('.aitx-holdings span:not([data-company-profile-ready])').forEach(node => {
      const holding = parseHolding(node);
      if (!holding || !state.profiles[holding.ticker]) return;

      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'ait-company-trigger';
      button.dataset.companyProfileReady = 'true';
      button.dataset.ticker = holding.ticker;
      button.dataset.weight = holding.weight || '—';
      button.setAttribute('aria-haspopup', 'dialog');
      button.setAttribute('aria-label', `${holding.ticker} — ${state.profiles[holding.ticker].name}. ${T.open}`);

      const ticker = document.createElement('span');
      ticker.className = 'ait-company-ticker';
      ticker.textContent = holding.ticker;
      button.appendChild(ticker);
      if (holding.weight) {
        const weight = document.createElement('span');
        weight.className = 'ait-company-weight';
        weight.textContent = holding.weight;
        button.appendChild(weight);
      }
      node.replaceWith(button);
    });
  }

  async function loadProfiles() {
    installStyles();
    ensureModal();
    try {
      const response = await fetch(`${endpoint}?v=1`, { cache: 'force-cache' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (payload.schema_version !== 'ai-tournament-company-profiles-v1') throw new Error('unsupported schema');
      state.profiles = payload.profiles || {};
      decorateHoldings(document.getElementById('agent-cards') || document);
    } catch (_) {
      // Tickers remain readable even when the optional profile layer is unavailable.
    }
  }

  document.addEventListener('click', event => {
    const trigger = event.target.closest('.ait-company-trigger');
    if (trigger) openModal(trigger);
  });
  window.addEventListener('briefrooms:ai-tournament-rendered', () => {
    if (Object.keys(state.profiles).length) decorateHoldings(document.getElementById('agent-cards') || document);
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', loadProfiles, { once: true });
  else loadProfiles();
})();
