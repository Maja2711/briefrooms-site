(() => {
  'use strict';

  const cfg = window.BR_PORTFOLIO_10K || { lang: 'pl' };
  const lang = cfg.lang === 'en' ? 'en' : 'pl';
  const endpoint = '/data/ai_tournament/public.json';
  const copy = lang === 'pl' ? {
    title: 'Turniej nie rozpoczął się',
    body: 'Silnik jest gotowy, ale brakuje pełnych, ujawnionych portfeli części uczestników. Samego hasha zobowiązania nie da się odwrócić do listy spółek i wag, dlatego system nie tworzy danych zastępczych.',
    ready: 'gotowy',
    missing: 'brak pełnego ujawnienia',
    footer: 'Start nastąpi dopiero po zgodności wszystkich ujawnień z hashami zapisanymi przed otwarciem rynku.',
  } : {
    title: 'The tournament has not started',
    body: 'The engine is ready, but full revealed portfolios are missing for some participants. A commitment hash cannot be reversed into tickers and weights, so the system refuses to fabricate substitute data.',
    ready: 'ready',
    missing: 'full reveal missing',
    footer: 'The tournament starts only after every reveal matches its commitment hash recorded before the market opened.',
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[character]));

  function installStyle() {
    if (document.getElementById('ai-tournament-readiness-style')) return;
    const style = document.createElement('style');
    style.id = 'ai-tournament-readiness-style';
    style.textContent = `
      .ait-readiness{padding:18px;border:1px solid #e6b84f;border-left:5px solid #c98b0b;border-radius:16px;background:#fff9e8;color:#172b45;box-shadow:0 12px 32px rgba(38,54,74,.08)}
      .ait-readiness h3{margin:0 0 8px;font-size:1.25rem}.ait-readiness p{margin:0;line-height:1.55;color:#4b5f77}
      .ait-readiness-list{display:grid;gap:8px;margin:15px 0}.ait-readiness-row{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:9px 11px;border-radius:10px;background:rgba(255,255,255,.76);border:1px solid rgba(54,75,99,.12)}
      .ait-readiness-row strong{color:#172b45}.ait-readiness-row span{font-size:.78rem;font-weight:850;text-transform:uppercase;letter-spacing:.03em}.ait-readiness-row .ok{color:#14864a}.ait-readiness-row .bad{color:#a53a29}.ait-readiness-foot{padding-top:10px;border-top:1px solid rgba(201,139,11,.22);font-weight:750!important;color:#70520d!important}
    `;
    document.head.appendChild(style);
  }

  function render(data) {
    const status = String(data?.tournament?.status || '');
    if (!status.startsWith('BLOCKED_')) return;
    installStyle();
    const participants = Array.isArray(data?.readiness?.participants) ? data.readiness.participants : [];
    const rows = participants.map(row => `
      <div class="ait-readiness-row">
        <strong>${esc(row.agent_id || '')}</strong>
        <span class="${row.ready ? 'ok' : 'bad'}">${esc(row.ready ? copy.ready : copy.missing)}</span>
      </div>`).join('');
    const html = `<section class="ait-readiness" role="status">
      <h3>${esc(copy.title)}</h3>
      <p>${esc(copy.body)}</p>
      <div class="ait-readiness-list">${rows}</div>
      <p class="ait-readiness-foot">${esc(copy.footer)}</p>
    </section>`;
    for (const selector of ['#agents-preview', '#agent-cards', '#agent-log']) {
      const node = document.querySelector(selector);
      if (node) node.innerHTML = html;
    }
  }

  async function load() {
    try {
      const response = await fetch(`${endpoint}?v=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) return;
      render(await response.json());
    } catch (_) {
      // The base tournament renderer remains available on transient network errors.
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load, { once: true });
  else load();
  setInterval(load, 15 * 60 * 1000);
})();
