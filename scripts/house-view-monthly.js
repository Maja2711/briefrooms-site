/* BriefRooms House View v2 — bilingual, evidence-bound, read-only publication UI. */
(()=>{
  'use strict';
  const mount=document.querySelector('[data-house-view-monthly]');
  if(!mount)return;
  const pl=(document.documentElement.lang||'pl').toLowerCase().startsWith('pl');
  const lang=pl?'pl':'en';
  const labels=pl?{
    title:'S&P 500 · House View',tag:'Analiza rynku · scenariusz 1M / 6M / 12M',
    modelDetails:'Szczegóły techniczne silników BRs',sourceDetails:'Metodologia, źródła i ograniczenia',
    editorialVersion:'Interpretacja rynkowa v2.1 opracowana z zamrożonych danych — bez zmiany historii',
    scenarioNames:{base:'Scenariusz bazowy',downside:'Ryzyko spadkowe',upside:'Warunki odbicia'},
    horizonMeaning:{defensive:'Przeważa ryzyko spadków',neutral:'Brak wyraźnej przewagi kierunku',mild_positive:'Umiarkowana przewaga argumentów za wzrostem',unassessed:'Brak podstaw do oceny kierunku'},
    methodDetails:'Ocena punktowa (skala heurystyczna, nie prognoza prawdopodobieństwa)',
    asOf:'Data analizy',age:'Raport ma więcej niż 45 dni i nie jest aktualny',
    h:'Horyzont',score:'Bilans czynników',conv:'Siła oceny (limit heurystyczny)',
    sections:{thesis:'Główna teza',growth_and_inflation:'Wzrost i inflacja',
      rates_and_energy:'Stopy i energia — wpływ na S&P 500',earnings_and_valuation:'Zyski spółek i wyceny — warunek hossy',
      tactical_and_models:'S&P 500 — ryzyko korekty czy odbicie?',disagreements:'Co zmieni naszą tezę?'},
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
    title:'S&P 500 · House View',tag:'Market outlook · 1M / 6M / 12M scenario',
    modelDetails:'Technical details of BRs engines',sourceDetails:'Methods, evidence and limitations',
    editorialVersion:'v2.1 market interpretation from frozen data — historical records unchanged',
    scenarioNames:{base:'Base scenario',downside:'Downside risks',upside:'Recovery conditions'},
    horizonMeaning:{defensive:'Downside risk dominates',neutral:'No clear directional advantage',mild_positive:'Modest upside balance',unassessed:'Insufficient basis for a directional call'},
    methodDetails:'Factor scoring (unvalidated heuristic, not a probability forecast)',
    asOf:'Analysis as of',age:'Report is older than 45 days and is not current',
    h:'Horizon',score:'Factor balance',conv:'Assessment strength (heuristic cap)',
    sections:{thesis:'Core thesis',growth_and_inflation:'Growth and inflation',
      rates_and_energy:'Rates and energy — implications for S&P 500',earnings_and_valuation:'Earnings and valuations — conditions for a rally',
      tactical_and_models:'S&P 500 — correction or rebound?',disagreements:'What would change our view?'},
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
    if(!['gdp_qoq_saar_pct','pce_yoy_pct','core_pce_yoy_pct','us_10y_pct','brent_usd_bbl'].every(k=>typeof d.inputs[k]==='number'&&Number.isFinite(d.inputs[k])))return false;
    const r=d.research,required=Object.keys(labels.sections);
    return Array.isArray(r.sections)&&r.sections.length===required.length&&
      required.every(id=>r.sections.some(s=>s.id===id&&typeof s.pl==='string'&&typeof s.en==='string'))&&
      Array.isArray(r.scenarios)&&r.scenarios.length===3&&
      Array.isArray(r.engine_crosscheck)&&r.engine_crosscheck.length===5&&
      Array.isArray(r.evidence)&&r.evidence.length>=5&&Array.isArray(r.thesis_change_gates);
  };

  /* Presentation-only editorial v2.1: explain the frozen facts without changing
     archived observations or silently promoting engine research to an S&P call. */
  const investorEditorial=(doc)=>{
    if(doc.schema_version!=='2.0')return null;
    const f=doc.inputs,v=doc.views,r=doc.research;
    const need=['gdp_qoq_saar_pct','pce_yoy_pct','core_pce_yoy_pct','us_10y_pct','brent_usd_bbl'];
    if(!need.every(k=>typeof f[k]==='number'&&Number.isFinite(f[k])))return null;
    const mom=typeof f.spx_futures_20d_return_pct==='number'&&Number.isFinite(f.spx_futures_20d_return_pct)
      ?f.spx_futures_20d_return_pct:null;
    const gdp=f.gdp_qoq_saar_pct,core=f.core_pce_yoy_pct,rate=f.us_10y_pct,
      pce=f.pce_yoy_pct,oil=f.brent_usd_bbl;
    const n=(x)=>x.toLocaleString(pl?'pl-PL':'en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
    const bias=(h)=>labels.values[v[h].bias];
    const risk=(flag)=>(r.engine_crosscheck||[]).some(e=>e.status==='research_only'&&e.risk_flag===flag);
    const structureRisk=risk('structural_caution'),geoRisk=risk('geopolitical_caution');
    const refs=(arr)=>arr.filter(id=>(r.evidence||[]).some(x=>x.id===id));
    const sect=(id,txt,ref)=>({id,[lang]:txt,source_ids:refs(ref)});
    const stance={
      defensive:pl?'przeważa ryzyko dalszej korekty lub słabej konsolidacji':'correction or weak consolidation risk dominates',
      neutral:pl?'brak wyraźnej przewagi kierunkowej; możliwy rynek wahliwy':'no clear directional edge; choppy price action is plausible',
      mild_positive:pl?'przewaga argumentów za umiarkowanym odbiciem, nie za silną hossą':'the balance favors a modest rebound, not an established bull market',
      unassessed:pl?'brak kwalifikowanego sygnału, więc nie określamy kierunku':'no qualified market observation, so no direction is assigned'
    };
    const lead=pl
      ? 'S&P 500 — w horyzoncie 1M '+stance[v['1M'].bias]+'. Ocena 6M: '+bias('6M')+', 12M: '+bias('12M')+'. Przy PKB '+n(gdp)+'% SAAR gospodarka nie wskazuje sama w sobie załamania aktywności, ale inflacja bazowa '+n(core)+'% i rentowność 10Y '+n(rate)+'% ograniczają przestrzeń do wzrostu wycen akcji. Trwały wzrost indeksu wymagałby niższej presji inflacyjnej lub potwierdzonej poprawy EPS.'
      : 'S&P 500 — over 1M, '+stance[v['1M'].bias]+'. 6M: '+bias('6M')+'; 12M: '+bias('12M')+'. GDP at '+n(gdp)+'% SAAR does not by itself indicate an activity collapse, but core inflation of '+n(core)+'% and a '+n(rate)+'% 10-year yield constrain equity valuations. A sustained advance would require easing inflation pressure or verified EPS improvement.';
    let growth;
    if(gdp>=2&&core>=2.7){
      growth=pl
        ?'Wzrost aktywności wspiera potencjał przychodów spółek, ale inflacja może utrzymywać koszty finansowania i presję na marże. Dla S&P 500 to wsparcie ze strony popytu, nie argument za szybkim wzrostem mnożników P/E.'
        :'Growth supports revenue potential, but inflation can sustain funding costs and margin pressure. For the S&P 500 that is demand support, not a reason to assume rapid P/E multiple expansion.';
    }else if(gdp<1){
      growth=pl
        ?'Słaba aktywność zwiększa ryzyko rozczarowania przychodami i zyskami. To argument za ostrożniejszą oceną S&P 500, zwłaszcza jeżeli inflacja nie spada.'
        :'Weak growth raises revenue and earnings disappointment risk. This warrants a more cautious S&P 500 view, particularly if inflation remains elevated.';
    }else if(core<=2.3&&gdp>=1){
      growth=pl
        ?'Wzrost gospodarczy przy niższej inflacji bazowej byłby korzystniejszym połączeniem dla akcji. S&P 500 mógłby skorzystać, jeżeli kolejne odczyty potwierdzą tę tendencję.'
        :'Growth alongside lower core inflation is a more supportive equity mix. The S&P 500 could benefit if subsequent releases confirm it.';
    }else{
      growth=pl
        ?'Wzrost pozostaje dodatni, lecz sama dynamika PKB nie przesądza o wzroście indeksu. Kluczowe będzie przełożenie popytu na zyski spółek bez nowej presji kosztowej.'
        :'GDP remains positive, but GDP alone does not imply index gains. The key is converting demand into earnings without renewed cost pressure.';
    }
    let rateEffect;
    if(rate>=4.5){
      rateEffect=pl
        ?'Tak wysoka rentowność zwiększa atrakcyjność obligacji względem akcji i podnosi stopę dyskontową przyszłych zysków. Nasz scenariusz bazowy nie zakłada automatycznego wzrostu P/E S&P 500. Warunkiem poprawy byłby spadek rentowności bez osłabienia gospodarki albo wyraźnie lepszy EPS.'
        :'Yields this high make bonds more competitive with equities and raise the discount rate on future earnings. Our base case does not assume automatic S&P 500 P/E expansion. Lower yields without weaker growth, or stronger EPS, would improve the case.';
    }else if(rate<=3.5){
      rateEffect=pl
        ?'Niższy koszt kapitału sprzyja wycenom S&P 500, pod warunkiem że nie wynika głównie z nadchodzącej recesji.'
        :'Lower funding costs support S&P 500 valuations, provided they do not mainly reflect looming recession.';
    }else{
      rateEffect=pl
        ?'Koszt kapitału nadal ogranicza mnożniki P/E. Dalszy spadek rentowności bez pogorszenia wzrostu byłby dla S&P 500 korzystniejszy niż ich wzrost.'
        :'Funding costs continue to constrain P/E multiples. Easing yields without weaker growth would be more supportive for the S&P 500 than rising yields.';
    }
    const oilEffect=oil>=110
      ?(pl?'Droga ropa zwiększa ryzyko kosztów, inflacji i słabszych marż spółek.':'High oil prices increase cost, inflation and margin risks.')
      :oil>=100
        ?(pl?'Ropa powyżej 100 USD może utrudniać dezinflację i poprawę marż, nawet jeśli nie jest bezpośrednim sygnałem spadku indeksu.':'Oil above USD 100 may complicate disinflation and margin improvement, though it does not alone forecast an index decline.')
        :(pl?'Niższa cena ropy ogranicza część presji kosztowej, ale sama nie przesądza o hossie.':'Lower oil eases some cost pressure but does not by itself establish a bull market.');
    const fund=r.fundamentals||{};
    const valuations=fund.status==='curated_not_live'&&Number.isFinite(fund.forward_pe)&&Number.isFinite(fund.forward_eps_growth_pct)
      ?(pl
        ?'Źródłowy forward P/E: '+n(fund.forward_pe)+'x; prognozowany wzrost EPS: '+n(fund.forward_eps_growth_pct)+'%. Wzrost EPS mógłby pomóc S&P 500 mimo wysokich rentowności, jednak wzrost cen bez poprawy EPS zwiększa ryzyko przewartościowania. Wciąż potrzebna jest weryfikacja rewizji prognoz.'
        :'Sourced forward P/E: '+n(fund.forward_pe)+'x; expected EPS growth: '+n(fund.forward_eps_growth_pct)+'%. Earnings gains could support the S&P 500 despite high yields, but prices rising faster than earnings would increase valuation risk. Earnings revisions still need verification.')
      :(pl
        ?'Dla S&P 500 w horyzoncie 6–12M kluczowe są zyski spółek i wyceny, nie sam wzrost PKB. Bez aktualnego, potwierdzonego konsensusu EPS, rewizji prognoz i forward P/E nie zakładamy wzrostu indeksu tylko dzięki droższym akcjom. Dlatego nie podnosimy tezy długoterminowej do zdecydowanie wzrostowej i nie podajemy nieuzasadnionego targetu.'
        :'For the S&P 500 over 6–12M, earnings and valuations matter more than GDP alone. Without verified current index EPS consensus, revisions and forward P/E, we do not assume index gains driven solely by more expensive stocks. We therefore do not upgrade the longer-term case to strongly bullish or invent a price target.');
    let tactical;
    if(mom===null){
      tactical=pl
        ?'Bez wiarygodnej obserwacji rynku za ostatnie 20 sesji nie wyznaczamy nowego kierunku 1M. Ryzyko oceniamy wyłącznie na podstawie potwierdzonych danych makro.'
        :'Without a qualified 20-session price observation we do not make a fresh 1M directional call. Risk assessment is limited to verified macro facts.';
    }else if(mom<=-3){
      tactical=pl
        ?'S&P 500 futures straciły '+n(Math.abs(mom))+'% w ostatnich 20 sesjach. To osłabienie rynku zwiększa ryzyko kolejnej fali korekty w 1M, lecz nie wyznacza poziomu dna. Trwała poprawa momentum byłaby pierwszym sygnałem słabnięcia tej tezy.'
        :'S&P 500 futures lost '+n(Math.abs(mom))+'% over 20 sessions. Market weakness increases the risk of another correction leg over 1M, but does not identify a bottom. Sustained momentum improvement would be an early reason to reconsider this view.';
    }else if(mom>=3){
      tactical=pl
        ?'S&P 500 futures wzrosły '+n(mom)+'% w 20 sesjach. To sprzyja krótkoterminowemu odbiciu, ale samo momentum nie wystarcza do tezy o trwałym wzroście 6–12M.'
        :'S&P 500 futures gained '+n(mom)+'% over 20 sessions. That favors a near-term rebound, but momentum alone cannot establish a sustained 6–12M advance.';
    }else{
      tactical=pl
        ?'S&P 500 futures zmieniły się o '+n(mom)+'% w 20 sesjach; nie widać jednoznacznego impulsu. Silniejsze potwierdzenie trendu wymagałoby poprawy cen i otoczenia makro.'
        :'S&P 500 futures changed '+n(mom)+'% over 20 sessions, without a clear impulse. Stronger trend confirmation would require improving prices and macro conditions.';
    }
    if(structureRisk)tactical+=pl
      ?' Dodatkowo rynek wykazuje ryzyko gwałtownych wahań, co zwiększa niepewność krótkoterminową, ale nie przesądza kierunku.'
      :' The market also exhibits elevated abrupt-move risk, adding near-term uncertainty without independently predicting direction.';
    if(geoRisk)tactical+=pl
      ?' Ryzyka geopolityczne mogą podnosić wymaganą przez inwestorów premię za ryzyko.'
      :' Geopolitical risks may increase the equity risk premium demanded by investors.';
    const disagreement=(r.engine_conflicts||[]).length
      ?(pl
        ?'Rynek i ryzyka otoczenia wysyłają sprzeczne sygnały. Nie uśredniamy modeli o różnych horyzontach; dla S&P 500 rozstrzygające będą utrzymanie kierunku cen, inflacja i rentowności.'
        :'Price action and contextual risks diverge. We do not average models with different horizons; sustained prices, inflation and yields will be the decisive S&P 500 checks.')
      :v['1M'].bias==='defensive'
        ?(pl
          ?'Co mogłoby odwrócić ostrożną tezę 1M? Trwała poprawa momentum S&P 500, spadek inflacji bazowej i niższe rentowności bez osłabienia PKB. Dziś dodatni wzrost gospodarczy ogranicza pesymizm, ale nie dowodzi, że indeks osiągnął już dno.'
          :'What would overturn the cautious 1M call? Sustained S&P 500 momentum recovery, easing core inflation and lower yields without weaker GDP. Positive growth reduces bearish conviction but does not establish a market bottom.')
        :(pl
          ?'Co zmieni bilans? Wyższa inflacja i rentowności zwiększą presję na S&P 500; ich spadek przy stabilnym wzroście i lepszym momentum wzmocni scenariusz wzrostowy. Brak sporu między silnikami nie jest potwierdzeniem prognozy.'
          :'What changes the balance? Higher inflation and yields increase pressure on the S&P 500; easing inflation with stable growth and improved momentum strengthens the upside case. No model disagreement does not validate a forecast.');
    const sections=[
      sect('thesis',lead,['GDPC1','PCEPILFE','DGS10',...(mom===null?[]:['WES'])]),
      sect('growth_and_inflation',(pl
        ?'PKB USA '+n(gdp)+'% q/q SAAR, PCE '+n(pce)+'% r/r, core PCE '+n(core)+'% r/r. '
        :'U.S. GDP '+n(gdp)+'% q/q SAAR, PCE '+n(pce)+'% y/y, core PCE '+n(core)+'% y/y. ')+growth,
        ['GDPC1','PCEPI','PCEPILFE']),
      sect('rates_and_energy',(pl
        ?'US 10Y: '+n(rate)+'%; Brent: '+n(oil)+' USD/bbl. '
        :'U.S. 10Y: '+n(rate)+'%; Brent: USD '+n(oil)+'/bbl. ')+rateEffect+' '+oilEffect,
        ['DGS10','DCOILBRENTEU','PCEPILFE']),
      sect('earnings_and_valuation',valuations,fund.status==='curated_not_live'?['FUNDAMENTALS','DGS10']:['GDPC1','DGS10']),
      sect('tactical_and_models',tactical,[...(mom===null?[]:['WES']),...(structureRisk?['FSE']:[]),...(geoRisk?['GSE']:[])]),
      sect('disagreements',disagreement,['GDPC1','PCEPILFE','DGS10',...(mom===null?[]:['WES'])])
    ];
    const scenarios=[
      {id:'base',[lang]:pl
        ?'Scenariusz bazowy: S&P 500 w 1M '+bias('1M')+', w 6M '+bias('6M')+', w 12M '+bias('12M')+'. Odbicie bez lepszej inflacji, rentowności lub EPS mogłoby być nietrwałe.'
        :'Base case: S&P 500 1M '+bias('1M')+', 6M '+bias('6M')+', 12M '+bias('12M')+'. A rebound without better inflation, yields or EPS may prove fragile.',
        watch_ids:['GDPC1','PCEPILFE','DGS10']},
      {id:'downside',[lang]:pl
        ?'Scenariusz spadkowy: uporczywa inflacja i wysokie rentowności ograniczają P/E, a koszty energii lub słabszy popyt obciążają EPS. S&P 500 byłby podatniejszy na dalszą korektę.'
        :'Downside: persistent inflation and elevated yields constrain P/E while energy costs or softer demand pressure EPS. The S&P 500 would be more exposed to further correction.',
        watch_ids:['PCEPILFE','DGS10','DCOILBRENTEU','GDPC1']},
      {id:'upside',[lang]:pl
        ?'Scenariusz wzrostowy: inflacja spada bez recesji, rentowności maleją, a poprawa cen jest wsparta rosnącym EPS. Wówczas trwalszy wzrost S&P 500 staje się lepiej uzasadniony.'
        :'Upside: inflation eases without recession, yields fall and improving prices are backed by EPS growth. A sustained S&P 500 advance would then be better supported.',
        watch_ids:['GDPC1','PCEPILFE','DGS10']}
    ];
    const gates=[
      {id:'inflation',[lang]:pl?'Core PCE poniżej 2,7% r/r: większa przestrzeń do łagodniejszego kosztu kapitału i poprawy P/E S&P 500.':'Core PCE below 2.7% y/y: more room for easing funding costs and S&P 500 P/E improvement.'},
      {id:'rates',[lang]:pl?'Rentowność 10Y poniżej 4,5% przy stabilnym PKB: argument za łagodniejszą presją na wyceny akcji.':'10-year yield below 4.5% with stable GDP: a less restrictive equity valuation backdrop.'},
      {id:'growth',[lang]:pl?'PKB poniżej 1% q/q SAAR: większe ryzyko słabszego EPS, które mogłoby obniżyć tezę 6M/12M.':'GDP below 1% q/q SAAR: higher risk of weaker EPS and a downgrade to the 6M/12M case.'},
      {id:'oil',[lang]:pl?'Brent powyżej 110 USD/bbl: ryzyko kosztów i inflacji może zwiększyć presję na S&P 500.':'Brent above USD 110/bbl: cost and inflation risks could pressure the S&P 500.'},
      {id:'momentum',[lang]:pl?'Momentum S&P 500 futures powyżej +3% w 20 sesjach: większe potwierdzenie odbicia krótkoterminowego.':'S&P 500 futures momentum above +3% over 20 sessions: stronger near-term rebound confirmation.'}
    ];
    return {sections,scenarios,gates};
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
      add(tile,'span','',labels.horizonMeaning[x.bias]);
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
    const r=doc.research,ed=investorEditorial(doc),parts=ed?.sections||r.sections,
      scenarios=ed?.scenarios||r.scenarios,gates=ed?.gates||r.thesis_change_gates,
      card=add(mount,'article','hvm-card');
    const head=add(card,'header','hvm-head');
    add(head,'span','hvm-kicker',labels.tag);
    add(head,'h2','',labels.title+' · '+doc.month);
    add(head,'p','hvm-lead',parts.find(x=>x.id==='thesis')[lang]);
    markStaleness(card,doc);
    horizonCards(card,doc);
    observations(card,doc);
    add(card,'p','hvm-editorial-version',labels.editorialVersion);
    const stories=add(card,'div','hvm-prose-grid');
    parts.filter(x=>x.id!=='thesis').forEach(item=>{
      const panel=section(stories,labels.sections[item.id]||item.id);
      add(panel,'p','',item[lang]);
      const foot=add(panel,'small','hvm-cite',item.source_ids.length?item.source_ids.join(' · '):labels.missing);
    });
    const scenarioPane=section(card,labels.scenarios);
    const scenarioGrid=add(scenarioPane,'div','hvm-triptych');
    scenarios.forEach(item=>{
      const el=add(scenarioGrid,'article','hvm-scenario');
      add(el,'span','hvm-kicker',labels.scenarioNames[item.id]||item.id);
      add(el,'p','',item[lang]);
      add(el,'small','hvm-cite',item.watch_ids.join(' · '));
    });
    const crossDetails=add(card,'details','hvm-details');
    add(crossDetails,'summary','',labels.modelDetails);
    const cross=section(crossDetails,labels.models);
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
    const gatePane=section(card,labels.gates);
    const list=add(gatePane,'ul','hvm-gates');
    gates.forEach(item=>add(list,'li','',item[lang]));
    const evidenceDetails=add(card,'details','hvm-details');
    add(evidenceDetails,'summary','',labels.sourceDetails);
    const method=section(evidenceDetails,labels.methodDetails);
    ['1M','6M','12M'].forEach(h=>{
      const view=doc.views[h];
      add(method,'p','hvm-note',h+' — '+labels.score+': '+(Number.isInteger(view.risk_balance_score)?view.risk_balance_score:'—')+'; '+labels.conv+': '+(Number.isInteger(view.conviction)?view.conviction+'/10':'—'));
    });
    const coverage=section(evidenceDetails,labels.coverage);
    const cov=r.coverage||{};
    add(coverage,'p','',labels.fundamental+': '+(cov.earnings_valuation||'unavailable')+' · '+labels.belief+': '+(cov.belief||'unavailable'));
    add(coverage,'p','hvm-note',labels.limitation);
    const evidence=section(evidenceDetails,labels.sources);
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