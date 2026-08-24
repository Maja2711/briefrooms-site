(()=>{
  'use strict';
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
