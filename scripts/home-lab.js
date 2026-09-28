(()=>{
  'use strict';
  const root=document.getElementById('home-lab-root');
  if(!root)return;

  const isEn=(document.documentElement.lang||'pl').toLowerCase().startsWith('en');
  const lang=isEn?'en':'pl';
  const locale=isEn?'en-GB':'pl-PL';
  const PATHS={
    gse:'/data/gse/gse_v2_lab_public.json',
    fx:'/data/investments/eurusd_daily_spot.json',
    fxHistory:'/data/investments/eurusd_daily_history.json',
    longView:isEn?'/en/investing/long-view.html':'/pl/inwestycje/long-view.html',
    daily:isEn?'/en/investing/daily-trading.html':'/pl/inwestycje/daily-trading.html',
    gseLab:isEn?'/en/geo/gse-lab.html':'/pl/geo/gse-lab.html'
  };
  const T=isEn?{
    unavailable:'Source unavailable',details:'Open →',research:'RESEARCH ONLY',
    gseLabel:'GEOPOLITICS · GSE v2',gseStatus:'FROZEN FORECAST',gseStale:'STALE FORECAST',
    thesisProbability:'Thesis probability',horizon:'Horizon',clusters:'N effective',epistemic:'Epistemic conf.',
    fxLabel:'INVESTING · DAILY EUR/USD',live:'LIVE',snapshot:'OPEN · SNAPSHOT',result:'RESULT',
    entry:'Entry',now:'Now',engineMark:'Engine mark',pnl:'P/L',exit:'Exit',closed:'Closed',
    roomLabel:'ROOMS · LATEST',newMaterial:'LATEST MATERIAL',published:'Published',
    longLabel:'LONG VIEW · S&P 500',house:'HOUSE VIEW',date:'View date',
    sourceLive:'live mid-market',sourceEngine:'engine snapshot',
    roomNames:{health:'Health',science:'Science',geo:'Geopolitics'}
  }:{
    unavailable:'Brak świeżego źródła',details:'Otwórz →',research:'TYLKO RESEARCH',
    gseLabel:'GEOPOLITYKA · GSE v2',gseStatus:'ZAMROŻONA PROGNOZA',gseStale:'PROGNOZA NIEŚWIEŻA',
    thesisProbability:'P(tezy)',horizon:'Horyzont',clusters:'N efektywne',epistemic:'Pewność epistem.',
    fxLabel:'INWESTYCJE · DAILY EUR/USD',live:'LIVE',snapshot:'OPEN · SNAPSHOT',result:'WYNIK',
    entry:'Wejście',now:'Teraz',engineMark:'Mark silnika',pnl:'P/L',exit:'Wyjście',closed:'Zamknięto',
    roomLabel:'POKOJE · NAJNOWSZE',newMaterial:'NOWY MATERIAŁ',published:'Publikacja',
    longLabel:'LONG VIEW · S&P 500',house:'HOUSE VIEW',date:'Data tezy',
    sourceLive:'live mid-market',sourceEngine:'snapshot silnika',
    roomNames:{health:'Zdrowie',science:'Nauka',geo:'Geopolityka'}
  };

  const el=(tag,cls,text)=>{
    const n=document.createElement(tag);
    if(cls)n.className=cls;
    if(text!==undefined&&text!==null)n.textContent=String(text);
    return n;
  };
  const num=v=>{const n=Number(v);return Number.isFinite(n)?n:null;};
  const clampText=(v,max=170)=>{
    const s=String(v||'').replace(/\s+/g,' ').trim();
    return s.length<=max?s:s.slice(0,max-1).trimEnd()+'…';
  };
  const fetchJson=async url=>{
    try{
      const r=await fetch(url+(url.includes('?')?'&':'?')+'v='+Date.now(),{cache:'no-store'});
      return r.ok?await r.json():null;
    }catch(_){return null;}
  };
  const fetchText=async url=>{
    try{
      const r=await fetch(url+(url.includes('?')?'&':'?')+'v='+Date.now(),{cache:'no-store'});
      return r.ok?await r.text():null;
    }catch(_){return null;}
  };
  const fmtDate=v=>{
    if(!v)return '—';
    const d=new Date(v);
    if(Number.isNaN(d.getTime()))return String(v);
    return new Intl.DateTimeFormat(locale,{timeZone:'Europe/Warsaw',day:'2-digit',month:'2-digit',year:'numeric'}).format(d);
  };
  const fmtTime=v=>{
    if(!v)return '—';
    const d=new Date(v);
    if(Number.isNaN(d.getTime()))return String(v);
    return new Intl.DateTimeFormat(locale,{timeZone:'Europe/Warsaw',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(d);
  };
  const fmtPx=v=>{
    const n=num(v); if(n===null)return '—';
    return n.toLocaleString(locale,{minimumFractionDigits:5,maximumFractionDigits:5});
  };
  const fmtPct=(v,digits=2)=>{
    const n=num(v); if(n===null)return '—';
    return (n>0?'+':'')+n.toLocaleString(locale,{minimumFractionDigits:digits,maximumFractionDigits:digits})+'%';
  };
  const fmtProb=v=>{
    const n=num(v); if(n===null)return '—';
    return Math.round(n*100)+'%';
  };

  function card(model){
    const a=el('a','home-lab-card '+(model.variant||''));
    a.href=model.href||'#';
    if(model.id)a.id=model.id;
    const top=el('div','home-lab-card__top');
    top.append(el('span','home-lab-card__label',model.label||''),el('span','home-lab-card__status '+(model.statusClass||''),model.status||''));
    a.append(top);
    if(model.value){
      const hero=el('div','home-lab-card__hero');
      hero.append(el('strong',model.valueClass||'',model.value));
      if(model.valueLabel)hero.append(el('span','',model.valueLabel));
      a.append(hero);
    }
    if(model.title)a.append(el('h3','',model.title));
    if(model.desc)a.append(el('p','home-lab-card__desc',model.desc));
    if(model.metrics&&model.metrics.length){
      const metrics=el('div','home-lab-card__metrics');
      model.metrics.slice(0,3).forEach(m=>{
        const box=el('div','home-lab-metric');
        box.append(el('strong',m.kind||'',m.value==null?'—':m.value),el('span','',m.label||''));
        metrics.append(box);
      });
      a.append(metrics);
    }
    if(model.note)a.append(el('p','home-lab-card__note',model.note));
    const foot=el('div','home-lab-card__foot');
    if(model.meta)foot.append(el('span','home-lab-card__meta',model.meta));
    foot.append(el('strong','home-lab-card__link',model.cta||T.details));
    a.append(foot);
    return a;
  }

  function unavailableModel(variant,label,href){
    return {variant:variant,label:label,status:'—',title:T.unavailable,href:href,cta:T.details};
  }

  function gseModel(data){
    const x=data&&data.featured_thesis;
    if(!x)return unavailableModel('is-gse',T.gseLabel,PATHS.gseLab);
    const fresh=x.freshness==='fresh';
    const question=x['question_'+lang]||x.question_en||x.question_pl||'GSE v2';
    const ec=num(x.epistemic_confidence);
    return {
      id:'home-live-gse',variant:'is-gse',label:T.gseLabel,
      status:fresh?T.gseStatus:T.gseStale,statusClass:fresh?'':'is-stale',
      value:fmtProb(x.probability),valueLabel:T.thesisProbability,
      title:question,
      metrics:[
        {label:T.horizon,value:x.horizon_label||(String(x.horizon_hours||'—')+'h')},
        {label:T.clusters,value:String(x.effective_cluster_n==null?'—':x.effective_cluster_n)},
        {label:T.epistemic,value:ec===null?'—':fmtProb(ec)}
      ],
      note:T.research+' · '+(x.expected_direction||'—')+' '+(x.asset||''),
      meta:fmtTime(x.forecast_at),
      href:x['href_'+lang]||PATHS.gseLab,cta:T.details
    };
  }

  const openPosition=state=>{
    const p=state&&state.metadata&&state.metadata.position;
    return p&&String(p.status).toUpperCase()==='OPEN'?p:null;
  };
  const latestClosed=history=>{
    const rows=Array.isArray(history&&history.trades)?history.trades:[];
    return rows.filter(x=>x&&x.closed_at).sort((a,b)=>new Date(b.closed_at)-new Date(a.closed_at))[0]||null;
  };
  const pnlPercent=(position,mark)=>{
    const entry=num(position&&position.entry), px=num(mark);
    if(entry===null||entry<=0||px===null)return null;
    const sign=String(position.direction).toUpperCase()==='SHORT'?-1:1;
    return sign*((px-entry)/entry)*100;
  };

  function fxModel(state,history,liveQuote){
    const p=openPosition(state);
    if(p){
      const engineMark=num(p.mark_price);
      const livePrice=liveQuote?num(liveQuote.price):null;
      const mark=livePrice===null?engineMark:livePrice;
      const pnl=pnlPercent(p,mark);
      const live=livePrice!==null;
      return {
        id:'home-live-fx',variant:'is-fx',label:T.fxLabel,
        status:live?T.live:T.snapshot,statusClass:live?'is-live':'',
        title:'EUR/USD · '+String(p.direction||(state&&state.direction)||'').toUpperCase(),
        metrics:[
          {label:T.entry,value:fmtPx(p.entry)},
          {label:live?T.now:T.engineMark,value:fmtPx(mark)},
          {label:T.pnl,value:fmtPct(pnl),kind:pnl===null?'':pnl>=0?'positive':'negative'}
        ],
        note:'TP '+fmtPx(p.target)+' · SL '+fmtPx(p.stop)+' · '+(live?T.sourceLive:T.sourceEngine),
        meta:live?fmtTime(liveQuote.updatedAt):fmtTime(state&&state.timestamp),
        href:PATHS.daily,cta:T.details
      };
    }
    const last=latestClosed(history);
    if(last){
      const result=num(last.result_percent);
      return {
        id:'home-live-fx',variant:'is-fx',label:T.fxLabel,status:T.result,
        title:'EUR/USD · '+String(last.direction||'').toUpperCase(),
        metrics:[
          {label:T.entry,value:fmtPx(last.entry)},
          {label:T.exit,value:fmtPx(last.exit_price)},
          {label:T.pnl,value:fmtPct(result),kind:result===null?'':result>=0?'positive':'negative'}
        ],
        note:String(last.exit_reason||''),
        meta:T.closed+': '+fmtTime(last.closed_at),
        href:PATHS.daily,cta:T.details
      };
    }
    return unavailableModel('is-fx',T.fxLabel,PATHS.daily);
  }

  const parser=()=>new DOMParser();
  const roomFromPath=path=>path.includes('/zdrowie/')||path.includes('/health/')?'health':path.includes('/nauka/')||path.includes('/science/')?'science':'geo';
  const isRoomArticlePath=path=>{
    const prefixes=isEn?['/en/health/','/en/science/','/en/geo/']:['/pl/zdrowie/','/pl/nauka/','/pl/geo/'];
    if(!prefixes.some(p=>path.startsWith(p))||!path.endsWith('.html'))return false;
    return !/(gse-lab|\/topic\.html|kalkulator|calculator|score2|bmi-waist|calorie-macro)/i.test(path);
  };

  async function latestRoomArticle(){
    const xmlText=await fetchText('/sitemap.xml');
    if(!xmlText)return null;
    const xml=parser().parseFromString(xmlText,'application/xml');
    const candidates=[...xml.querySelectorAll('url')].map(node=>{
      const loc=node.querySelector('loc')&&node.querySelector('loc').textContent&&node.querySelector('loc').textContent.trim();
      const lastmod=node.querySelector('lastmod')&&node.querySelector('lastmod').textContent&&node.querySelector('lastmod').textContent.trim();
      if(!loc)return null;
      try{
        const u=new URL(loc,window.location.origin);
        return isRoomArticlePath(u.pathname)?{url:u.pathname,lastmod:lastmod||null,room:roomFromPath(u.pathname)}:null;
      }catch(_){return null;}
    }).filter(Boolean);

    const perRoom=[];
    ['health','science','geo'].forEach(room=>{
      const rows=candidates.filter(x=>x.room===room).sort((a,b)=>new Date(b.lastmod||0)-new Date(a.lastmod||0));
      if(rows[0])perRoom.push(rows[0]);
    });
    const enriched=await Promise.all(perRoom.map(async item=>{
      const html=await fetchText(item.url);
      if(!html)return null;
      const doc=parser().parseFromString(html,'text/html');
      const h1=doc.querySelector('h1');
      const title=(h1&&h1.textContent&&h1.textContent.trim())||(doc.querySelector('title')&&doc.querySelector('title').textContent.split('|')[0].trim());
      if(!title)return null;
      const metaDesc=doc.querySelector('meta[name="description"]');
      const desc=metaDesc?metaDesc.getAttribute('content')||'':'';
      const pubMeta=doc.querySelector('meta[property="article:published_time"]');
      const time=doc.querySelector('time[datetime]');
      const published=(pubMeta&&pubMeta.getAttribute('content'))||(time&&time.getAttribute('datetime'))||item.lastmod;
      return Object.assign({},item,{title:title,desc:desc,published:published});
    }));
    return enriched.filter(Boolean).sort((a,b)=>new Date(b.published||b.lastmod||0)-new Date(a.published||a.lastmod||0))[0]||null;
  }

  function roomModel(item){
    if(!item)return unavailableModel('is-room',T.roomLabel,isEn?'/en/science.html':'/pl/nauka.html');
    const roomName=T.roomNames[item.room]||item.room;
    return {
      id:'home-live-room',variant:'is-room room-'+item.room,label:T.roomLabel+' · '+roomName,
      status:T.newMaterial,title:item.title,desc:clampText(item.desc,150),
      metrics:[{label:T.published,value:fmtDate(item.published||item.lastmod)},{label:isEn?'Room':'Pokój',value:roomName}],
      href:item.url,cta:T.details
    };
  }

  async function longViewModel(){
    const html=await fetchText(PATHS.longView);
    if(!html)return unavailableModel('is-long',T.longLabel,PATHS.longView);
    const doc=parser().parseFromString(html,'text/html');
    const snap=doc.querySelector('.lv-snapshot');
    if(!snap)return unavailableModel('is-long',T.longLabel,PATHS.longView);
    const head=snap.querySelector('.lv-head h2');
    const date=snap.querySelector('.lv-date');
    const title=head&&head.textContent?head.textContent.trim():'S&P 500 House View';
    const dateText=date&&date.textContent?date.textContent.trim():'';
    const metrics=[...snap.querySelectorAll('.lv-bias > div')].slice(0,3).map(x=>{
      const sm=x.querySelector('small'), b=x.querySelector('b');
      return {label:sm&&sm.textContent?sm.textContent.trim():'',value:b&&b.textContent?b.textContent.trim():'—'};
    });
    const p=snap.querySelector('.lv-head p');
    const desc=p&&p.textContent?p.textContent.trim():'';
    return {
      id:'home-live-long',variant:'is-long',label:T.longLabel,status:T.house,
      title:title,desc:clampText(desc,155),metrics:metrics,meta:dateText?(T.date+': '+dateText):'',
      href:PATHS.longView,cta:T.details
    };
  }

  const REQUEST_TIMEOUT_MS=7000;
  const LIVE_MAX_AGE_MS=10*60*1000;
  async function fetchExternalJson(url){
    const controller=new AbortController();
    const timeout=window.setTimeout(()=>controller.abort(),REQUEST_TIMEOUT_MS);
    try{
      const r=await fetch(url,{cache:'no-store',mode:'cors',signal:controller.signal});
      if(!r.ok)throw new Error('http_'+r.status);
      return await r.json();
    }finally{window.clearTimeout(timeout);}
  }
  function validateQuote(price,timestamp,source){
    const rate=num(price);
    if(rate===null||rate<0.8||rate>1.5)throw new Error(source+'_invalid_rate');
    const at=timestamp?new Date(timestamp):new Date();
    if(Number.isNaN(at.getTime()))throw new Error(source+'_invalid_timestamp');
    const age=Date.now()-at.getTime();
    if(age< -60000||age>LIVE_MAX_AGE_MS)throw new Error(source+'_stale');
    return {price:rate,updatedAt:at.toISOString(),source:source};
  }
  async function liveFxQuote(){
    const providers=[
      async()=>{const d=await fetchExternalJson('https://fxapi.app/api/EUR/USD.json?_='+Date.now());return validateQuote(d&&d.rate,d&&d.timestamp,'fxapi.app');},
      async()=>{const d=await fetchExternalJson('https://www.currencyexchangetool.com/api/v1/convert?amount=1&from=EUR&to=USD&_='+Date.now());if(!d||d.success===false)throw new Error('provider_error');return validateQuote(d.rate==null?d.result:d.rate,d.updatedAt,'Currency Exchange Tool');}
    ];
    for(const provider of providers){try{return await provider();}catch(_){}}
    return null;
  }

  function replaceFx(model){
    const old=document.getElementById('home-live-fx');
    if(old)old.replaceWith(card(model));
  }
  async function refreshFxLive(){
    const results=await Promise.all([fetchJson(PATHS.fx),fetchJson(PATHS.fxHistory),liveFxQuote()]);
    replaceFx(fxModel(results[0],results[1],results[2]));
  }

  async function run(){
    root.setAttribute('aria-busy','true');
    const results=await Promise.all([
      fetchJson(PATHS.gse),fetchJson(PATHS.fx),fetchJson(PATHS.fxHistory),latestRoomArticle(),longViewModel()
    ]);
    const gse=results[0],state=results[1],history=results[2],article=results[3],longView=results[4];
    const grid=el('div','home-lab__cards');
    grid.append(card(gseModel(gse)),card(fxModel(state,history,null)),card(roomModel(article)),card(longView));
    root.replaceChildren(grid);
    root.removeAttribute('aria-busy');

    if(openPosition(state)){
      window.setTimeout(refreshFxLive,250);
      const timer=window.setInterval(()=>{if(document.visibilityState==='visible')refreshFxLive();},60000);
      window.addEventListener('pagehide',()=>window.clearInterval(timer),{once:true});
    }
  }
  run();
})();
