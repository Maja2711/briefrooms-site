/* BriefRooms House View v2 — bilingual, evidence-bound, read-only publication UI. */
(()=>{
  'use strict';
  const mount=document.querySelector('[data-house-view-monthly]');
  if(!mount)return;
  const pl=(document.documentElement.lang||'pl').toLowerCase().startsWith('pl');
  const lang=pl?'pl':'en';
  const labels=pl?{
    title:'S&P 500 · House View',tag:'Miesięczna analiza źródłowa · research-only',
    asOf:'Data analizy',age:'Raport ma więcej niż 45 dni i nie jest aktualny',
    h:'Horyzont',score:'Bilans czynników',conv:'Siła oceny (limit heurystyczny)',
    sections:{thesis:'Główna teza',growth_and_inflation:'Wzrost i inflacja',
      rates_and_energy:'Stopy i energia',earnings_and_valuation:'Wyniki spółek i wyceny',
      tactical_and_models:'Taktyka i silniki BRs',disagreements:'Konflikty dowodów'},
    models:'Cross-check silników BriefRooms',scenarios:'Scenariusze warunkowe',
    gates:'Warunki zmiany tezy',delta:'Zmiany w danych od poprzedniego miesiąca',
    metrics:'Dane źródłowe',sources:'Dowody i pochodzenie',coverage:'Zakres i braki danych',
    source:'Zobacz źródło',missing:'Brak zweryfikowanego aktualnego źródła',
    noChanges:'Pierwszy raport v2 lub brak porównywalnych zmian w obserwacjach.',
    unavailable:'Brak opublikowanej i zweryfikowanej analizy miesięcznej. Dostępna jest archiwalna analiza poniżej.',
    modelNote:'Odczyty badawcze nie głosują w modelu makro. ABSTAIN, brak danych i status challengera nie są sygnałem kierunku.',
    limitation:'Oceny są niewalidowaną heurystyką, nie prawdopodobieństwami ani targetami indeksu. Brak wycen i EPS ogranicza ocenę 6M/12M.',
    values:{defensive:'defensywny',neutral:'neutralny',mild_positive:'lekko pozytywny',unassessed:'brak oceny'},
    data:{gdp_qoq_saar_pct:'PKB USA q/q SAAR',pce_yoy_pct:'PCE r/r',core_pce_yoy_pct:'Core PCE r/r',
      us_10y_pct:'UST 10Y',brent_usd_bbl:'Brent USD/bbl',spx_futures_20d_return_pct:'S&P futures 20 sesji'},
    fundamental:'Weryfikacja EPS/P-E',belief:'Publiczny Belief SPX',months:'Archiwum JSON',
    note:'Pojedyncze źródło/miesiąc jest zamrażane. Historia redakcyjna poniżej ma zachowaną oryginalną datę.'
  }:{
    title:'S&P 500 · House View',tag:'Monthly source-grounded analysis · research-only',
    asOf:'Analysis as of',age:'Report is older than 45 days and is not current',
    h:'Horizon',score:'Factor balance',conv:'Assessment strength (heuristic cap)',
    sections:{thesis:'Core thesis',growth_and_inflation:'Growth and inflation',
      rates_and_energy:'Rates and energy',earnings_and_valuation:'Earnings and valuation',
      tactical_and_models:'Tactics and BRs engines',disagreements:'Evidence conflicts'},
    models:'BriefRooms engine cross-check',scenarios:'Conditional scenarios',
    gates:'Thesis-change conditions',delta:'Observed changes since the prior month',
    metrics:'Source observations',sources:'Evidence and provenance',coverage:'Coverage and gaps',
    source:'Open source',missing:'No verified current source',
    noChanges:'First v2 publication or no comparable observed changes.',
    unavailable:'No verified monthly research has yet been published. The archived editorial analysis remains below.',
    modelNote:'Research engine readings do not vote in the macro model. ABSTAIN, missing data and challenger status are not directional signals.',
    limitation:'Views are unvalidated heuristics, not probabilities or index targets. Missing earnings and valuation coverage limits the 6M/12M case.',
    values:{defensive:'defensive',neutral:'neutral',mild_positive:'mildly positive',unassessed:'unassessed'},
    data:{gdp_qoq_saar_pct:'U.S. GDP q/q SAAR',pce_yoy_pct:'PCE y/y',core_pce_yoy_pct:'Core PCE y/y',
      us_10y_pct:'UST 10Y',brent_usd_bbl:'Brent USD/bbl',spx_futures_20d_return_pct:'S&P futures 20 sessions'},
    fundamental:'EPS/P-E coverage',belief:'Public SPX Belief',months:'Frozen JSON archive',
    note:'Each monthly source set is frozen. The older editorial report below retains its original date.'
  };
  const node=(tag,cls,text)=>{
    const el=document.createElement(tag);
    if(cls)el.className=cls;
    if(text!==undefined&&text!==null)el.textContent=String(text);
    return el;
  };
  const add=(parent,tag,cls,text)=>{const el=node(tag,cls,text);parent.appendChild(el);return el;};
  const num=(x)=>typeof x==='number'&&Number.isFinite(x)?x.toLocaleString(pl?'pl-PL':'en-US',{maximumFractionDigits:2}):'—';
  const valid=(d)=>{
    if(!d||!/^20\d{2}-(0[1-9]|1[0-2])$/.test(d.month||'')||
       !/^20\d{2}-\d{2}-\d{2}$/.test(d.as_of||'')||!d.views||!d.inputs||!Array.isArray(d.indicators))return false;
    if(!['1M','6M','12M'].every(h=>d.views[h]&&Object.hasOwn(labels.values,d.views[h].bias)))return false;
    if(d.schema_version==='1.0')return d.status==='published_research_only';
    if(d.schema_version!=='2.0'||d.status!=='published_research_only'||!d.research)return false;
    const r=d.research,required=Object.keys(labels.sections);
    return Array.isArray(r.sections)&&r.sections.length===required.length&&
      required.every(id=>r.sections.some(s=>s.id===id&&typeof s.pl==='string'&&typeof s.en==='string'))&&
      Array.isArray(r.scenarios)&&r.scenarios.length===3&&
      Array.isArray(r.engine_crosscheck)&&r.engine_crosscheck.length===5&&
      Array.isArray(r.evidence)&&r.evidence.length>=5&&Array.isArray(r.thesis_change_gates);
  };
  const safeUrl=(value)=>{
    if(typeof value!=='string')return null;
    if(/^https:\/\/fred\.stlouisfed\.org\/graph\/fredgraph\.csv\?id=(GDPC1|PCEPI|PCEPILFE|DGS10|DCOILBRENTEU)$/.test(value))return value;
    if(/^\/data\/(gse\/gse_v2_lab_public|investments\/fse_public|public\/brace_spx_platform_public|investments\/weekly\/20\d\d-W\d\d)\.json$/.test(value))return value;
    try{
      const u=new URL(value);
      if(u.protocol==='https:'&&['insight.factset.com','www.spglobal.com','www.yardeni.com'].includes(u.hostname))return value;
    }catch(_){}
    return null;
  };
  const section=(parent,title,cls='hvm-pane')=>{
    const el=add(parent,'section',cls);
    add(el,'h3','hvm-title',title);
    return el;
  };
  const markStaleness=(card,doc)=>{
    const dt=new Date(doc.as_of+'T00:00:00Z');
    const days=Math.floor((Date.now()-dt.getTime())/86400000);
    if(!Number.isFinite(days)||days<0)throw new Error('invalid as-of');
    add(card,'p','hvm-date',labels.asOf+': '+doc.as_of+' · '+doc.month);
    if(days>45)add(card,'p','hvm-stale',labels.age);
  };
  const horizonCards=(card,doc)=>{
    const grid=add(card,'div','hvm-grid');
    ['1M','6M','12M'].forEach(h=>{
      const x=doc.views[h],tile=add(grid,'article','hvm-horizon');
      add(tile,'small','',labels.h+' '+h);
      add(tile,'strong','hvm-'+x.bias,labels.values[x.bias]);
      add(tile,'span','',labels.score+': '+(Number.isInteger(x.risk_balance_score)?x.risk_balance_score:'—'));
      add(tile,'span','',labels.conv+': '+(Number.isInteger(x.conviction)?x.conviction+'/10':'—'));
    });
  };
  const observations=(card,doc)=>{
    const panel=section(card,labels.metrics);
    const grid=add(panel,'div','hvm-fields');
    Object.entries(labels.data).forEach(([id,label])=>{
      const tile=add(grid,'div','hvm-field'),v=doc.inputs[id];
      add(tile,'small','',label);
      add(tile,'b','',num(v)+(typeof v==='number'?(id==='brent_usd_bbl'?' USD':'%'):''));
    });
  };
  const addLink=(parent,value,text)=>{
    const safe=safeUrl(value);
    if(!safe)return false;
    const a=add(parent,'a','',text);
    a.href=safe;
    a.rel='noopener noreferrer';
    if(safe.startsWith('https://'))a.target='_blank';
    return true;
  };
  const legacyOpened=(open)=>{
    const archive=document.querySelector('[data-house-view-legacy]');
    if(archive)archive.open=Boolean(open);
  };
  const renderV1=(doc)=>{
    const card=add(mount,'section','hvm-card');
    const head=add(card,'header','hvm-head');
    add(head,'span','hvm-kicker',labels.tag);
    add(head,'h2','',labels.title);
    add(head,'p','',labels.limitation);
    markStaleness(card,doc);
    horizonCards(card,doc);
    observations(card,doc);
    add(card,'p','hvm-note',labels.note);
    // V1 has no full sourced narrative: keep older editorial report visible.
    legacyOpened(true);
  };
  const renderV2=(doc)=>{
    const r=doc.research,card=add(mount,'article','hvm-card');
    const head=add(card,'header','hvm-head');
    add(head,'span','hvm-kicker',labels.tag);
    add(head,'h2','',labels.title+' · '+doc.month);
    add(head,'p','',r.sections.find(x=>x.id==='thesis')[lang]);
    markStaleness(card,doc);
    horizonCards(card,doc);
    observations(card,doc);
    const stories=add(card,'div','hvm-prose-grid');
    r.sections.filter(x=>x.id!=='thesis').forEach(item=>{
      const panel=section(stories,labels.sections[item.id]||item.id);
      add(panel,'p','',item[lang]);
      const foot=add(panel,'small','hvm-cite',item.source_ids.length?item.source_ids.join(' · '):labels.missing);
    });
    const scenarios=section(card,labels.scenarios);
    const scenarioGrid=add(scenarios,'div','hvm-triptych');
    r.scenarios.forEach(item=>{
      const el=add(scenarioGrid,'article','hvm-scenario');
      add(el,'span','hvm-kicker',item.id.toUpperCase());
      add(el,'p','',item[lang]);
      add(el,'small','hvm-cite',item.watch_ids.join(' · '));
    });
    const cross=section(card,labels.models);
    add(cross,'p','hvm-note',labels.modelNote);
    const engines=add(cross,'div','hvm-engines');
    r.engine_crosscheck.forEach(item=>{
      const el=add(engines,'article','hvm-engine');
      add(el,'strong','',item.id);
      add(el,'small','hvm-status',item.status+' · '+(item.as_of||'—'));
      add(el,'p','',item[lang]);
    });
    const change=section(card,labels.delta);
    if(!r.change_in_observed_factors.length)add(change,'p','hvm-note',labels.noChanges);
    r.change_in_observed_factors.forEach(item=>{
      add(change,'p','hvm-delta',item.source_id+': '+num(item.previous)+' → '+num(item.current)+' ('+(item.delta>0?'+':'')+num(item.delta)+')');
    });
    const gates=section(card,labels.gates);
    const list=add(gates,'ul','hvm-gates');
    r.thesis_change_gates.forEach(item=>add(list,'li','',item[lang]));
    const coverage=section(card,labels.coverage);
    const cov=r.coverage||{};
    add(coverage,'p','',labels.fundamental+': '+(cov.earnings_valuation||'unavailable')+' · '+labels.belief+': '+(cov.belief||'unavailable'));
    add(coverage,'p','hvm-note',labels.limitation);
    const evidence=section(card,labels.sources);
    const refs=add(evidence,'ul','hvm-evidence');
    r.evidence.forEach(item=>{
      const li=add(refs,'li','');
      add(li,'strong','',item.id+' · '+item.status);
      add(li,'small','',item.observed_at||'—');
      const origin=item.document_url||item.source;
      if(origin)addLink(li,origin,labels.source);
      if(item.sha256)add(li,'small','hvm-digest','SHA-256 '+item.sha256.slice(0,16)+'…');
    });
    const end=add(card,'div','hvm-publication-end');
    const a=add(end,'a','',labels.months+' · '+doc.month);
    a.href='/data/investments/house_view_monthly_archive/'+doc.month+'.json';
    add(end,'p','hvm-note',labels.note);
    legacyOpened(false);
  };
  fetch('/data/investments/house_view_monthly_current.json?v='+Date.now(),{cache:'no-store'})
    .then(res=>{if(!res.ok)throw Error('unpublished');return res.json();})
    .then(doc=>{
      if(!valid(doc))throw Error('invalid source report');
      mount.replaceChildren();
      if(doc.schema_version==='2.0')renderV2(doc);
      else renderV1(doc);
    })
    .catch(()=>{
      mount.replaceChildren(node('p','hvm-unavailable',labels.unavailable));
      legacyOpened(true);
    });
})();