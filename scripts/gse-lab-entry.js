(()=>{
  'use strict';

  const ensureFeaturedArticles=()=>{
    const path=window.location.pathname.replace(/\/$/,'');
    if(path!=='/pl/geopolityka'&&path!=='/pl/geopolityka.html')return;
    const list=document.querySelector('.library .tiles');
    if(!list)return;

    const featured=[
      {
        id:'arctic-shortcut',
        href:'/pl/geo/arktyczny-skrot.html',
        date:'2026-09-20',
        dateLabel:'20.09.2026',
        title:'Arktyczny skrót',
        desc:'Czy Północna Droga Morska zmieni handel Europa–Azja, czy tylko zamieni jedno wąskie gardło na nową zależność?'
      },
      {
        id:'nato-threshold-poland',
        href:'/pl/geo/jak-rosja-testuje-prog-reakcji-nato-wobec-polski.html',
        date:'2026-09-13',
        dateLabel:'13.09.2026',
        title:'Jak Rosja testuje próg reakcji NATO wobec Polski',
        desc:'Szara strefa, drony, sabotaż i granica między art. 4 a art. 5. Analiza z eksperymentalnym GSE Lab — 30 Day Outlook.'
      }
    ];

    const nodeFor=(article)=>{
      const existing=[...list.querySelectorAll('.tile')].find(tile=>{
        const link=tile.querySelector('.tile-link');
        return link&&link.getAttribute('href')===article.href;
      });
      if(existing){
        existing.dataset.briefroomsArticle=article.id;
        return existing;
      }

      const item=document.createElement('li');
      item.className='tile';
      item.dataset.briefroomsArticle=article.id;
      item.innerHTML=`
        <a class="tile-link" href="${article.href}">
          <time class="tile-date" datetime="${article.date}">${article.dateLabel}</time>
          <span class="tile-art" aria-hidden="true"></span>
          <span class="tile-body">
            <span class="tile-title">${article.title}</span>
            <span class="tile-desc">${article.desc}</span>
          </span>
        </a>`;
      return item;
    };

    // Prepend in reverse so the final visible order is newest first.
    featured.slice().reverse().forEach(article=>{
      list.prepend(nodeFor(article));
    });
  };

  ensureFeaturedArticles();

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
