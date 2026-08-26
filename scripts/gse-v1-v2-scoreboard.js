(()=>{
  'use strict';
  const root=document.getElementById('gse-lab-root');
  if(!root)return;
  const lang=(document.documentElement.lang||'pl').toLowerCase().startsWith('en')?'en':'pl';
  const copy=lang==='pl'?{
    title:'GSE v1 vs GSE v2 — pojedynek live',
    sub:'Obie wersje są oceniane na tych samych późniejszych wynikach rynku. Niższy Brier i Log Loss są lepsze.',
    champion:'GSE v1 · champion',challenger:'GSE v2 · challenger',
    brier:'Brier',log:'Log Loss',pairs:'Wspólne testy',delta:'Δ Brier v2−v1',bias:'Bias kalibracji v2',
    leaderV2:'GSE v2 prowadzi',leaderV1:'GSE v1 prowadzi',tie:'Praktycznie remis',
    tiny:'Przewaga v2 jest obecnie bardzo mała i nie wystarcza do promocji.',
    tinyV1:'Przewaga v1 jest obecnie bardzo mała; v2 pozostaje w shadow.',
    decisiveV2:'GSE v2 ma mierzalną przewagę, ale nadal musi przejść bramki kalibracji i próbę prospective.',
    decisiveV1:'GSE v1 nadal wygrywa w bieżącej próbie prospective.',
    gate:'Bramka promocji',gateText:'wymaga Δ Brier ≤ -0,005 oraz |bias| ≤ 0,10. Promocja nie jest automatyczna.',
    rel:'Względna poprawa Brier'
  }:{
    title:'GSE v1 vs GSE v2 — live head-to-head',
    sub:'Both versions are scored on the same later market outcomes. Lower Brier and Log Loss are better.',
    champion:'GSE v1 · champion',challenger:'GSE v2 · challenger',
    brier:'Brier',log:'Log Loss',pairs:'Paired tests',delta:'Δ Brier v2−v1',bias:'v2 calibration bias',
    leaderV2:'GSE v2 leads',leaderV1:'GSE v1 leads',tie:'Practical tie',
    tiny:'The v2 edge is currently tiny and is not enough for promotion.',
    tinyV1:'The v1 edge is currently tiny; v2 remains in shadow.',
    decisiveV2:'GSE v2 has a measurable edge, but it still must pass calibration and prospective gates.',
    decisiveV1:'GSE v1 still leads in the current prospective sample.',
    gate:'Promotion gate',gateText:'requires Δ Brier ≤ -0.005 and |bias| ≤ 0.10. Promotion is never automatic.',
    rel:'Relative Brier improvement'
  };
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n};
  const valid=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
  const num=(v,d=6)=>valid(v)?Number(v).toFixed(d):'—';
  const signed=(v,d=6)=>valid(v)?`${Number(v)>=0?'+':''}${Number(v).toFixed(d)}`:'—';
  const row=(label,value,kind='')=>{const r=el('div','gse-metric-row');r.append(el('span','',label),el('strong',kind,value));return r};
  function render(data){
    if(document.getElementById('gse-v1-v2-scoreboard'))return;
    const p=data.prospective||{};
    if(!valid(p.mean_brier_v1)||!valid(p.mean_brier_v2))return;
    const delta=Number(p.delta_brier_v2_minus_v1);
    const rel=(Number(p.mean_brier_v1)-Number(p.mean_brier_v2))/Number(p.mean_brier_v1)*100;
    const practicalTie=Math.abs(delta)<0.001;
    const v2Leads=delta<0;
    const section=el('section','gse-section');section.id='gse-v1-v2-scoreboard';
    const head=el('div','gse-section-head');const hc=el('div');hc.append(el('h2','',copy.title),el('p','',copy.sub));head.append(hc);section.append(head);
    const grid=el('div','gse-two');
    const v1=el('div','gse-status-box');v1.append(el('h3','',copy.champion),row(copy.brier,num(p.mean_brier_v1)),row(copy.log,num(p.mean_log_loss_v1)),row(copy.pairs,String(p.paired_n??'—')));
    const v2=el('div','gse-status-box');v2.append(el('h3','',copy.challenger),row(copy.brier,num(p.mean_brier_v2),v2Leads?'gse-positive':''),row(copy.log,num(p.mean_log_loss_v2),v2Leads?'gse-positive':''),row(copy.bias,num(p.calibration_bias_v2,4),Math.abs(Number(p.calibration_bias_v2))<=.1?'gse-positive':'gse-negative'));
    grid.append(v1,v2);section.append(grid);
    const verdict=el('div','gse-highlight');verdict.style.marginTop='14px';
    let title,body;
    if(practicalTie){title=copy.tie;body=v2Leads?copy.tiny:copy.tinyV1}
    else if(v2Leads){title=copy.leaderV2;body=copy.decisiveV2}
    else{title=copy.leaderV1;body=copy.decisiveV1}
    verdict.append(el('strong','',title),el('p','',`${copy.delta}: ${signed(delta)} · ${copy.rel}: ${rel.toFixed(3)}% · ${body}`));
    const gate=el('p','gse-footnote',`${copy.gate}: ${copy.gateText}`);verdict.append(gate);section.append(verdict);
    const children=[...root.children];
    const anchor=children[2]||null;
    root.insertBefore(section,anchor);
  }
  const load=()=>fetch('/data/gse/gse_v2_lab_public.json?v='+Date.now(),{cache:'no-store'}).then(r=>r.ok?r.json():Promise.reject()).then(render).catch(()=>{});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',load,{once:true});else load();
})();
