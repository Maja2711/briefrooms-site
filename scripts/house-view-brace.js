(()=>{
  'use strict';
  const nodes=[...document.querySelectorAll('[data-brace-house]')];
  if(!nodes.length)return;
  const lang=(document.documentElement.lang||'pl').toLowerCase().startsWith('en')?'en':'pl';
  const fmtDate=(v)=>{if(!v)return '—';const d=new Date(v);if(Number.isNaN(d.getTime()))return '—';return new Intl.DateTimeFormat(lang==='pl'?'pl-PL':'en-GB',{dateStyle:'medium',timeZone:'Europe/Warsaw'}).format(d)};
  const labels=lang==='pl'?{
    pass:'zaliczona',fail:'niezaliczona',warming:'rozgrzewanie',research:'research-only',noChampion:'brak championa',sealed:'zapieczętowany',obs:'obserwacji shadow'
  }:{pass:'passed',fail:'not passed',warming:'warming up',research:'research-only',noChampion:'no champion',sealed:'sealed',obs:'shadow observations'};
  fetch('/data/public/brace_spx_generation6_public.json?v='+Date.now(),{cache:'no-store',credentials:'same-origin'})
    .then(r=>r.ok?r.json():Promise.reject(new Error(String(r.status))))
    .then(data=>{
      nodes.forEach(root=>{
        const set=(key,val)=>{const el=root.querySelector(`[data-brace-${key}]`);if(el&&val!==undefined&&val!==null)el.textContent=String(val)};
        const dev=data.development||{};const sh=data.shadow||{};const hold=data.sealed_holdout||{};
        set('generation',data.generation_id||'spx-orthogonal-core-v6');
        set('status',data.research_only?labels.research:'—');
        set('gate',dev.strict_gate_passed?labels.pass:labels.fail);
        set('champion',dev.single_champion_authorized?'authorized':labels.noChampion);
        set('shadow',`${sh.observations_collected??'—'} / ${sh.warmup_required??'—'}`);
        set('remaining',sh.observations_remaining??'—');
        set('market-date',sh.latest_market_date?fmtDate(sh.latest_market_date):'—');
        set('updated',fmtDate(sh.updated_at||data.generated_at));
        set('holdout',hold.accessed===false?labels.sealed:'—');
        const bar=root.querySelector('[data-brace-progress]');
        if(bar){const done=Number(sh.observations_collected)||0;const total=Number(sh.warmup_required)||0;const pct=total?Math.max(0,Math.min(100,done/total*100)):0;bar.style.width=pct.toFixed(1)+'%';bar.parentElement?.setAttribute('aria-valuenow',String(Math.round(pct)));}
      });
    })
    .catch(()=>{});
})();
