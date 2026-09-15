(()=>{
  'use strict';
  const root=document.getElementById('home-lab-root');
  if(!root)return;
  const lang=(document.documentElement.lang||'pl').toLowerCase().startsWith('en')?'en':'pl';
  const t={
    pl:{active:'aktywne badania',review:'wymaga przeglądu',auto:'auto-promocje',finding:'Wniosek',updated:'Aktualizacja',unavailable:'Brak świeżego statusu',loading:'Ładowanie wyników badań…'},
    en:{active:'active research',review:'needs review',auto:'auto-promotions',finding:'Finding',updated:'Updated',unavailable:'Fresh status unavailable',loading:'Loading research results…'}
  }[lang];
  const $=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
  const fetchJson=async(url)=>{try{const r=await fetch(url+(url.includes('?')?'&':'?')+'v='+Date.now(),{cache:'no-store'});if(!r.ok)return null;return await r.json();}catch(_){return null;}};
  const pct=v=>Number.isFinite(Number(v))?`${Math.round(Number(v)*100)}%`:'—';
  const bp=v=>Number.isFinite(Number(v))?`${Number(v)>=0?'+':''}${Number(v).toFixed(2)} bp`:'—';
  const fmtTime=v=>{if(!v)return null;const d=new Date(v);if(Number.isNaN(d.getTime()))return null;return new Intl.DateTimeFormat(lang==='pl'?'pl-PL':'en-GB',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(d);};

  function applyDynamic(config,gseDiscovery,gseReview,eurusd){
    const cards=(config.cards||[]).map(x=>JSON.parse(JSON.stringify(x)));
    const gse=cards.find(x=>x.id==='gse-v2');
    if(gse&&gseDiscovery){
      const verified=gseDiscovery.verified_clusters;
      const target=gseDiscovery.target_verified_clusters||100;
      if(Number.isFinite(Number(verified)))gse.metrics[0].value=`${verified} / ${target}+`;
      if(gseDiscovery.status==='target_met')gse.status='HISTORY GATE MET';
      else if(gseDiscovery.status==='below_target')gse.status='HISTORY BACKFILL';
      else if(gseDiscovery.status==='discovery_error')gse.status='BACKFILL RETRY';
      gse._updated=gseDiscovery.published_at||gse._updated;
    }
    if(gse&&gseReview){
      const s=String(gseReview.status||gseReview.review_status||'');
      if(/human.*review|eligible_for_human/i.test(s))gse.review_required=true;
      const pn=gseReview.prospective_n??gseReview.paired_n??gseReview.prospective?.paired_n;
      if(Number.isFinite(Number(pn)))gse.metrics[2].value=String(pn);
      gse._updated=gseReview.published_at||gseReview.updated_at||gse._updated;
    }
    const fx=cards.find(x=>x.id==='eurusd-abc');
    if(fx&&eurusd){
      const captures=eurusd.sample?.captures;
      const m30=eurusd.comparison?.['30m']||{};
      if(Number.isFinite(Number(captures)))fx.metrics[0].value=String(captures);
      if(m30.B)fx.metrics[1].value=pct(m30.B.hit_rate);
      if(m30.C)fx.metrics[2].value=pct(m30.C.hit_rate);
      const b=m30.B?.mean_signed_return_bps_signal_only;
      const c=m30.C?.hit_rate;
      if(Number.isFinite(Number(b))&&Number.isFinite(Number(c))){
        fx[`finding_${lang}`]=lang==='pl'
          ?`30m: wariant B ma średni wynik kierunkowy ${bp(b)}, a C hit-rate ${pct(c)}. To nadal mała próba i nie jest to sygnał transakcyjny.`
          :`30m: arm B has mean directional result ${bp(b)}, while C has ${pct(c)} hit rate. The sample is still small and this is not a trading signal.`;
      }
      fx._updated=eurusd.generated_at;
    }
    return cards;
  }

  function renderSummary(summary,cards){
    const review=Math.max(Number(summary?.review_required||0),cards.filter(x=>x.review_required).length);
    const box=$('div','home-lab__summary');
    const items=[
      [summary?.active_research??cards.length,t.active,''],
      [review,t.review,'review'],
      [summary?.automatic_promotions??0,t.auto,'']
    ];
    items.forEach(([value,label,kind])=>{const item=$('span','home-lab__summary-item');item.append($('i',`home-lab__dot ${kind}`),$('strong','',String(value)),$('span','',label));box.append(item);});
    return box;
  }

  function renderCard(card){
    const a=$('a','home-lab-card'+(card.review_required?' is-review':''));
    a.href=card[`href_${lang}`]||'#';
    const top=$('div','home-lab-card__top');
    top.append($('span','home-lab-card__label',card[`label_${lang}`]||''),$('span','home-lab-card__status',card.status||'RESEARCH'));
    a.append(top,$('h3','',card.title||''),$('p','home-lab-card__desc',card[`description_${lang}`]||''));
    const metrics=$('div','home-lab-card__metrics');
    (card.metrics||[]).slice(0,3).forEach(m=>{const box=$('div','home-lab-metric');box.append($('strong','',m.value??'—'),$('span','',m[`label_${lang}`]||''));metrics.append(box);});
    a.append(metrics);
    const finding=$('p','home-lab-card__finding');
    finding.append($('strong','',t.finding+': '),document.createTextNode(card[`finding_${lang}`]||''));
    a.append(finding,$('span','home-lab-card__link',card[`cta_${lang}`]||(lang==='pl'?'Zobacz →':'View →')));
    return a;
  }

  async function run(){
    root.setAttribute('aria-busy','true');
    const [config,gseDiscovery,gseReview,eurusd]=await Promise.all([
      fetchJson('/data/lab/home_lab_status.json'),
      fetchJson('/data/gse/historical_discovery_status.json'),
      fetchJson('/data/gse/gse_v2_learning_review_status.json'),
      fetchJson('/data/investments/eurusd_abc_public_pl.json')
    ]);
    if(!config){root.textContent=t.unavailable;root.removeAttribute('aria-busy');return;}
    const cards=applyDynamic(config,gseDiscovery,gseReview,eurusd);
    root.replaceChildren(renderSummary(config.summary||{},cards));
    const grid=$('div','home-lab__cards');cards.slice(0,4).forEach(c=>grid.append(renderCard(c)));root.append(grid);
    const times=[config.updated_at,gseDiscovery?.published_at,gseReview?.published_at,gseReview?.updated_at,eurusd?.generated_at].filter(Boolean).map(v=>new Date(v)).filter(d=>!Number.isNaN(d.getTime()));
    const latest=times.length?new Date(Math.max(...times.map(d=>d.getTime()))):null;
    const foot=$('div','home-lab__foot');
    const fresh=$('span','home-lab__fresh',latest?`${t.updated}: ${fmtTime(latest.toISOString())}`:t.unavailable);
    if(latest&&Date.now()-latest.getTime()>48*3600*1000)fresh.classList.add('is-stale');
    foot.append(fresh,$('span','',lang==='pl'?'Status badawczy · bez automatycznej promocji':'Research status · no automatic promotion'));
    root.append(foot);root.removeAttribute('aria-busy');
  }
  run();
})();
