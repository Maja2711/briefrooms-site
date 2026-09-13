(()=>{
  'use strict';

  const injectLatestArticle=()=>{
    const path=window.location.pathname.replace(/\/$/,'');
    if(path!=='/pl/geopolityka'&&path!=='/pl/geopolityka.html')return;
    const list=document.querySelector('.library .tiles');
    if(!list||list.querySelector('[data-briefrooms-article="nato-threshold-poland"]'))return;

    const item=document.createElement('li');
    item.className='tile';
    item.dataset.briefroomsArticle='nato-threshold-poland';
    item.innerHTML=`
      <a class="tile-link" href="/pl/geo/jak-rosja-testuje-prog-reakcji-nato-wobec-polski.html">
        <time class="tile-date" datetime="2026-09-13">13.09.2026</time>
        <span class="tile-body">
          <span class="tile-title">Jak Rosja testuje próg reakcji NATO wobec Polski</span>
          <span class="tile-desc">Szara strefa, drony, sabotaż i granica między art. 4 a art. 5. Analiza z eksperymentalnym GSE Lab — 30 Day Outlook.</span>
        </span>
      </a>`;
    list.prepend(item);
  };

  injectLatestArticle();

  const box=document.querySelector('[data-gse-lab-entry]');
  if(!box)return;
  const lang=(document.documentElement.lang||'pl').toLowerCase().startsWith('en')?'en':'pl';
  const value=(sel,v)=>{const n=box.querySelector(sel);if(n&&v!==undefined&&v!==null)n.textContent=String(v)};
  fetch('/data/gse/gse_v2_lab_public.json?v='+Date.now(),{cache:'no-store'})
    .then(r=>r.ok?r.json():Promise.reject(new Error(String(r.status))))
    .then(data=>{
      const s=data.summary||{};const best=data.best_horizon||{};const p=data.prospective||{};
      value('[data-gse-clusters]',`${s.verified_clusters??'—'} / ${s.target_verified_clusters??100}+`);
      value('[data-gse-walk]',s.walk_forward_n??'—');
      value('[data-gse-live]',s.prospective_paired_n??'—');
      value('[data-gse-best]',best.label||'—');
      const status=box.querySelector('[data-gse-status]');if(status)status.textContent=String(data.readiness?.status||data.engine?.mode||'shadow').replaceAll('_',' ').toUpperCase();
      const finding=box.querySelector('[data-gse-finding]');if(finding&&best.label){const imp=Number.isFinite(Number(best.brier_improvement_pct))?Number(best.brier_improvement_pct).toFixed(1)+'%':'—';finding.textContent=lang==='pl'?`Najlepszy historyczny horyzont: ${best.label}; poprawa Brier względem prostej bazy: ${imp}. Walidacja live: ${p.paired_n??'—'} sparowanych prognoz.`:`Best historical horizon: ${best.label}; Brier improvement versus the simple baseline: ${imp}. Prospective validation: ${p.paired_n??'—'} paired forecasts.`}
    }).catch(()=>{});
})();
