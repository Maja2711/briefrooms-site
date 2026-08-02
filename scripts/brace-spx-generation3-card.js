(function(){
  'use strict';

  var roots=Array.prototype.slice.call(document.querySelectorAll('[data-brace-spx-generation3],[data-brace-spx-generation4],[data-brace-spx-generation5]'));
  if(!roots.length) return;
  var language=(document.documentElement.lang||'pl').toLowerCase().indexOf('en')===0?'en':'pl';
  var locale=language==='pl'?'pl-PL':'en-US';

  function pct(value,digits){var n=Number(value);return Number.isFinite(n)?(n*100).toLocaleString(locale,{minimumFractionDigits:digits,maximumFractionDigits:digits})+'%':'—';}
  function number(value,digits){var n=Number(value);return Number.isFinite(n)?n.toLocaleString(locale,{minimumFractionDigits:digits,maximumFractionDigits:digits}):'—';}
  function set(root,field,value){var selector='[data-v3-field="'+field+'"],[data-v4-field="'+field+'"],[data-v5-field="'+field+'"]';Array.prototype.forEach.call(root.querySelectorAll(selector),function(node){node.textContent=value==null?'—':String(value);});}
  function bar(root,ratio){Array.prototype.forEach.call(root.querySelectorAll('[data-v3-progress-bar],[data-v4-progress-bar],[data-v5-progress-bar]'),function(node){node.style.width=(ratio*100).toFixed(2)+'%';node.setAttribute('aria-valuenow',String(Math.round(ratio*100)));});}
  function diagnosticNode(root){var node=root.querySelector('[data-brace-diagnostics]');if(!node){node=document.createElement('p');node.setAttribute('data-brace-diagnostics','true');node.className='brace-note';root.appendChild(node);}return node;}

  function isLongShort(report,architecture){
    var mandate=report.mandate||{};
    return mandate.short_allowed===true || mandate.position_set==='long_short_flat' || String(architecture.id||'').indexOf('a2s')!==-1;
  }

  function renderArchitecture2(root,report){
    var architecture=report.architecture||{};
    var progress=report.progress||{};
    var development=report.development||{};
    var leader=development.diagnostic_leader||{};
    var metrics=leader.metrics||{};
    var gate=development.strict_gate||{};
    var holdout=report.sealed_holdout||{};
    var completed=Number(progress.experiments_completed||0);
    var total=Number(progress.candidate_space_size||completed||0);
    var remaining=Number(progress.experiments_remaining||Math.max(0,total-completed));
    var ratio=Math.max(0,Math.min(1,Number(progress.completion_ratio)||(total?completed/total:0)));
    var heading=root.querySelector('h2');
    var note=root.querySelector('.brace-note')||root.querySelector('.brace-overview-body p')||root.querySelector('p');
    var longShort=isLongShort(report,architecture);
    var architectureLabel=longShort?'Architecture 2S':'Architecture 2';
    var mandateLabel=longShort?'Long / Short / Flat':'Long / Flat';
    if(heading) heading.textContent='BRACE-SPX LAB — '+architectureLabel;
    if(note) note.textContent=language==='pl'
      ?(longShort
        ?'Wieloźródłowe badanie long/short/flat z reżimami rynku. To nowa, niezależnie walidowana rodzina kandydatów; stare wyniki Architecture 2 long/flat pozostają zamrożonym punktem odniesienia.'
        :'Wieloźródłowe badanie long/flat z reżimami rynku. Architecture 2 pozostaje zamrożonym punktem odniesienia; holdout jest zapieczętowany, a pojedynczy champion nie został autoryzowany.')
      :(longShort
        ?'Multi-source long/short/flat research with market regimes. This is a newly validated candidate family; the old Architecture 2 long/flat evidence remains a frozen reference.'
        :'Multi-source long/flat research with market regimes. Architecture 2 remains a frozen reference; the holdout is sealed and no single champion has been authorized.');
    set(root,'generation',architecture.id||report.architecture_id||'spx-multisignal-regime-a2');
    set(root,'status',(report.status_labels||{})[language]||report.status||'—');
    set(root,'completed',completed.toLocaleString(locale));
    set(root,'total',total.toLocaleString(locale));
    set(root,'remaining',remaining.toLocaleString(locale));
    set(root,'progress',pct(ratio,1));
    set(root,'signature',String(architecture.candidate_signature||report.candidate_signature||'—').slice(0,16)+'…');
    set(root,'holdout',holdout.accessed?(language==='pl'?'otwarty':'opened'):(language==='pl'?'zapieczętowany':'sealed'));
    set(root,'cagr',pct(metrics.cagr,1));
    set(root,'sharpe',number(metrics.sharpe_excess,2));
    set(root,'drawdown',pct(metrics.max_drawdown,1));
    set(root,'gate',gate.passed?(language==='pl'?'zaliczona':'passed'):(language==='pl'?'niezaliczona':'not passed'));
    diagnosticNode(root).textContent='PBO: '+pct(gate.pbo,1)
      +(language==='pl'?' · globalny DSR: ':' · global DSR: ')+pct(gate.global_dsr,1)
      +(language==='pl'?' · zwycięzcy foldów: ':' · fold winners: ')+(gate.unique_fold_winners==null?'—':gate.unique_fold_winners)
      +(language==='pl'?' · mandat: ':' · mandate: ')+mandateLabel;
    bar(root,ratio);
    root.classList.remove('is-loading');
  }

  function load(url){return fetch(url+'?ts='+Date.now(),{cache:'no-store',credentials:'same-origin'}).then(function(response){if(!response.ok) throw new Error('HTTP '+response.status);return response.json();});}

  load('/data/public/brace_spx_public.json').then(function(report){
    if(String(report.schema_version||'').indexOf('2.')!==0) throw new Error('Unsupported schema');
    roots.forEach(function(root){renderArchitecture2(root,report);});
  }).catch(function(){
    roots.forEach(function(root){set(root,'status',language==='pl'?'Brak aktualnego raportu':'No current report');root.classList.remove('is-loading');});
  });
})();
