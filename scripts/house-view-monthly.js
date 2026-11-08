(()=> {
  'use strict';
  const holder=document.querySelector('[data-house-view-monthly]');
  if(!holder)return;
  const pl=(document.documentElement.lang||'pl').toLowerCase().startsWith('pl');
  const copy=pl?{
    title:'S&P 500 · miesięczny House View',badge:'Automatyczny przegląd danych · research-only',
    intro:'Bieżący, źródłowy bilans ryzyka makro i taktyki. Nie jest to pełna wycena fundamentalna ani rekomendacja transakcji.',
    date:'Stan danych',conviction:'Siła oceny',score:'Bilans czynników',
    sources:'Źródła i daty obserwacji',changed:'Zmiana wobec poprzedniej publikacji',
    archived:'Archiwum: poniższa ręcznie opracowana analiza pochodzi z października 2026 r. Aktualny miesięczny przegląd znajduje się powyżej.',
    notReady:'Nie opublikowano jeszcze zweryfikowanego przeglądu miesięcznego. Widoczna niżej analiza jest archiwalna i zachowuje swoją pierwotną datę.',
    stale:'UWAGA: ostatni zweryfikowany raport miesięczny jest starszy niż 45 dni. Nie przedstawiamy go jako aktualnego.',
    noMomentum:'Brak świeżego odczytu S&P 500 — horyzont 1M pozostaje bez oceny.',
    caveat:'Skala kierunkowa jest regułą heurystyczną, a nie prognozą prawdopodobieństwa. Brak danych o bieżących wycenach i zyskach spółek ogranicza ocenę 6M/12M; maksymalna deklarowana siła to 3/10.',
    labels:{defensive:'defensywny',neutral:'neutralny',mild_positive:'lekko pozytywny',unassessed:'brak oceny'},
    gdp:'PKB USA q/q SAAR',headline:'Inflacja PCE r/r',core:'Inflacja bazowa PCE r/r',ten:'Rentowność 10Y USA',oil:'Brent USD/bbl',momentum:'S&P futures · 20 sesji',noPrior:'Pierwszy przegląd według nowej metodologii',
    previous:'Poprzednio',current:'Teraz',none:'brak danych',source:'Źródło',archiveLink:'Archiwum miesięczne'
  }:{
    title:'S&P 500 · monthly House View',badge:'Automated source review · research-only',
    intro:'Current, source-based macro and tactical risk balance. Not a complete fundamental valuation or trading recommendation.',
    date:'Data as of',conviction:'Assessment strength',score:'Factor balance',
    sources:'Sources and observation dates',changed:'Change since the previous publication',
    archived:'Archive: the manually prepared analysis below is dated October 2026. The current monthly review is displayed above.',
    notReady:'No verified monthly review has been published yet. The analysis below is historical and retains its original date.',
    stale:'WARNING: the latest verified monthly review is over 45 days old. It is not represented as current.',
    noMomentum:'No fresh S&P 500 observation — the 1M horizon remains unassessed.',
    caveat:'Directional scores are unvalidated heuristics, not probabilities. Missing current valuation/earnings coverage limits the 6M/12M assessment; declared conviction is capped at 3/10.',
    labels:{defensive:'defensive',neutral:'neutral',mild_positive:'mildly positive',unassessed:'unassessed'},
    gdp:'U.S. GDP q/q SAAR',headline:'Headline PCE y/y',core:'Core PCE y/y',ten:'U.S. Treasury 10Y',oil:'Brent USD/bbl',momentum:'S&P futures · 20 sessions',noPrior:'First review under this methodology',
    previous:'Previously',current:'Now',none:'unavailable',source:'Source',archiveLink:'Monthly archive'
  };
  const element=(tag,cls,content)=>{
    const n=document.createElement(tag);
    if(cls)n.className=cls;
    if(content!==undefined)n.textContent=String(content);
    return n;
  };
  const add=(parent,tag,cls,content)=>{const n=element(tag,cls,content);parent.appendChild(n);return n;};
  const isValid=(x)=>{
    if(!x||x.schema_version!=='1.0'||x.status!=='published_research_only'||!/^20\d{2}-(0[1-9]|1[0-2])$/.test(x.month||''))return false;
    if(!/^20\d{2}-\d{2}-\d{2}$/.test(x.as_of||''))return false;
    if(!x.views||!x.inputs||!Array.isArray(x.indicators)||x.indicators.length!==5)return false;
    return ['1M','6M','12M'].every(k=>x.views[k]&&Object.hasOwn(copy.labels,x.views[k].bias));
  };
  const appendArchiveNotice=()=>{
    const archive=document.querySelector('.lv-snapshot')||document.getElementById('house-view');
    if(archive){
      const label=element('p','hvm-archive',copy.archived);
      archive.parentNode.insertBefore(label,archive);
    }
  };
  function render(doc){
    holder.replaceChildren();
    const root=add(holder,'section','hvm-card');
    const head=add(root,'div','hvm-head');
    add(head,'span','hvm-kicker',copy.badge);
    add(head,'h2','',copy.title);
    add(head,'p','',copy.intro);
    const asOf=new Date(doc.as_of+'T00:00:00Z');
    const diffDays=Math.floor((Date.now()-asOf.getTime())/86400000);
    if(!Number.isFinite(diffDays)||diffDays<0){throw Error('invalid published date');}
    add(root,'p','hvm-date',copy.date+': '+doc.as_of+' · '+doc.month);
    if(diffDays>45)add(root,'p','hvm-stale',copy.stale);
    const grid=add(root,'div','hvm-grid');
    ['1M','6M','12M'].forEach(h=>{
      const v=doc.views[h],c=add(grid,'article','hvm-horizon');
      add(c,'small','',h);
      add(c,'strong','hvm-'+v.bias,copy.labels[v.bias]);
      add(c,'span','',copy.score+': '+(Number.isInteger(v.risk_balance_score)?v.risk_balance_score:'—'));
      add(c,'span','',copy.conviction+': '+(Number.isInteger(v.conviction)?v.conviction+'/10':'—'));
    });
    if(doc.views['1M'].bias==='unassessed')add(root,'p','hvm-note',copy.noMomentum);
    const f=doc.inputs, metrics=[
      [copy.gdp,f.gdp_qoq_saar_pct,'%'],[copy.headline,f.pce_yoy_pct,'%'],[copy.core,f.core_pce_yoy_pct,'%'],
      [copy.ten,f.us_10y_pct,'%'],[copy.oil,f.brent_usd_bbl,' USD'],
      [copy.momentum,f.spx_futures_20d_return_pct,'%']
    ];
    const fields=add(root,'div','hvm-fields');
    metrics.forEach(([label,value,unit])=>{
      const box=add(fields,'div','hvm-field');
      add(box,'small','',label);
      add(box,'b','',typeof value==='number'&&Number.isFinite(value)?value.toLocaleString(pl?'pl-PL':'en-US',{maximumFractionDigits:2})+unit:copy.none);
    });
    const diff=doc.change_vs_previous||{},prior=diff.bias_changes||{};
    const block=add(root,'div','hvm-changes');
    add(block,'b','',copy.changed);
    if(!Object.keys(prior).length)add(block,'p','',copy.noPrior);
    else ['1M','6M','12M'].forEach(h=>{
      const entry=prior[h];
      if(entry&&copy.labels[entry.previous_bias]&&copy.labels[entry.current_bias])
        add(block,'p','',h+': '+copy.previous+' '+copy.labels[entry.previous_bias]+' → '+copy.current+' '+copy.labels[entry.current_bias]);
    });
    add(root,'p','hvm-note',copy.caveat);
    const sources=add(root,'div','hvm-sources');
    add(sources,'b','',copy.sources);
    const list=add(sources,'ul','');
    doc.indicators.forEach(row=>{
      if(!/^(GDPC1|PCEPI|PCEPILFE|DGS10|DCOILBRENTEU)$/.test(row.id||''))return;
      if(row.source_url!=='https://fred.stlouisfed.org/graph/fredgraph.csv?id='+row.id)return;
      const item=add(list,'li','');
      const link=add(item,'a','',row.source+' · '+row.id+' · '+row.observation_date);
      link.href=row.source_url;link.target='_blank';link.rel='noopener noreferrer';
    });
    const archive=add(root,'a','hvm-archive-link',copy.archiveLink+' · '+doc.month);
    archive.href='/data/investments/house_view_monthly_archive/'+doc.month+'.json';
    appendArchiveNotice();
  }
  fetch('/data/investments/house_view_monthly_current.json?v='+Date.now(),{cache:'no-store'})
    .then(r=>{if(!r.ok)throw Error('missing');return r.json();})
    .then(doc=>{if(!isValid(doc))throw Error('invalid');render(doc);})
    .catch(()=>{holder.replaceChildren(element('p','hvm-unavailable',copy.notReady));});
})();