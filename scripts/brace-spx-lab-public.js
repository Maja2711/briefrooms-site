(function(){
  'use strict';

  var root=document.querySelector('[data-brace-lab-root]');
  if(!root)return;
  var lang=(document.documentElement.lang||'pl').toLowerCase().indexOf('en')===0?'en':'pl';
  var locale=lang==='pl'?'pl-PL':'en-GB';
  var t=lang==='pl'?{
    eyebrow:'BriefRooms Research · S&P 500',
    hero:'BRACE-SPX: jedna platforma, dwa tory uczenia',
    lead:'Generation 6 pozostaje zamrożonym eksperymentem out-of-sample. W tej samej platformie działa Adaptive Research Track, który uczy się wyłącznie na nowych, rozwiązanych obserwacjach i tworzy challengery bez przepisywania historii G6.',
    scenarios:'Scenariusze S&P',lab:'BRACE-SPX Lab',snapshot:'Stan',
    architecture:'Architektura BRACE-SPX',archNote:'Nie uruchamiamy drugiego silnika. Frozen Track, Adaptive Research i Promotion Gate korzystają z jednego pipeline’u danych i jednego governance, ale mają rozdzielone granice dowodowe.',
    frozen:'Frozen Track',frozenTitle:'G6 · uczciwa walidacja',frozenText:'Osiem oryginalnych kandydatów G6 pozostaje niezmiennych. Ich parametrów nie wolno dostrajać po zobaczeniu nowych wyników.',
    adaptive:'Adaptive Research',adaptiveTitle:'Learner · challengery',adaptiveText:'Nowe hipotezy mogą zmieniać wagi i reguły wyłącznie jako nowe identyfikowalne challengery z własną datą startu.',
    promotion:'Promotion Gate',promotionTitle:'G7 · tylko po dowodach',promotionText:'Challenger nie może automatycznie zastąpić G6. Po spełnieniu kryteriów trafia do ręcznego review i dopiero wtedy może zostać zamrożony jako G7.',
    immutable:'immutable',active:'ACTIVE',notReady:'NOT READY',ready:'READY FOR REVIEW',
    now:'Gdzie system jest teraz',development:'Development G6',devDone:'zakończony',gateFailed:'strict gate niezaliczony',shadow:'Frozen shadow',adaptiveStage:'Adaptive research',promotionStage:'Promotion G7',collecting:'zbieranie dowodów',waitingFeatures:'oczekuje na gotowość cech',
    observations:'obserwacje',remaining:'pozostało',cycles:'cykle research',challengers:'aktywni challengerzy',prospectiveN:'prospective N',nextCheckpoint:'następny checkpoint',best:'best challenger',noBest:'— za mało nowych danych',
    whyZero:'Dlaczego Adaptive startuje od N=0?',whyZeroText:'Challengery zostały utworzone po zobaczeniu pierwszych 30 obserwacji shadow G6. Gdybyśmy zaliczyli im te same 30 sesji jako prospective evidence, powstałby ukryty look-ahead. Dlatego G6 zachowuje 30/70, ale nowe challengery zaczynają licznik od zera.',
    learning:'Jak działa rzeczywista pętla uczenia',step1:'Forecast + zapis',step1d:'Każdy challenger generuje stan przed przyszłym wynikiem.',step2:'Outcome + lesson',step2d:'Po rozwiązaniu obserwacji system mierzy wynik i contribution rodzin sygnałów.',step3:'Nowy challenger',step3d:'Na checkpointach 20/35/50/70 powstaje nowa hipoteza z nową granicą prospective.',
    challengeTable:'Challengery Adaptive Research',id:'ID',rule:'Reguła',origin:'Pochodzenie',created:'Start',n:'N',ret:'Zwrot',sharpe:'Sharpe',dd:'Max DD',
    rules:{trend_anchor_macro_veto:'trend + veto makro',balanced_orthogonal:'zbalansowane 4 rodziny',robust_family_median:'mediana rodzin',trend_credit_vol_confirmation:'trend + credit/VIX confirmation'},
    origins:{initial_g6_diagnostic_lessons:'lekcja z G6',adaptive_checkpoint_20:'checkpoint 20',adaptive_checkpoint_35:'checkpoint 35',adaptive_checkpoint_50:'checkpoint 50',adaptive_checkpoint_70:'checkpoint 70'},
    event:'Ostatnie zdarzenie uczenia',eventInit:'G6 nie przeszedł strict gate. Niestabilność rankingu i słaby wynik wobec prostego Trend 200D zostały zamienione na cztery jawne hipotezy challengera.',eventCheckpoint:'Checkpoint zakończony: learner utworzył nowego challengera na podstawie wyłącznie wcześniejszych, rozwiązanych danych.',
    rankCorr:'mediana korelacji rang foldów',g6Sharpe:'G6 best Sharpe',trendSharpe:'Trend 200D Sharpe',
    promotionGate:'Promotion Gate → G7',promotionDesc:'Promocja wymaga co najmniej 70 własnych obserwacji prospective najlepszego challengera, przewagi skorygowanej o ryzyko wobec Buy & Hold, lepszego obsunięcia oraz ręcznej akceptacji. Automatyczna promocja jest wyłączona.',blockers:'Aktualne blokery',noBlockers:'Brak blokad — gotowe do ręcznego review.',
    governance:'Granice governance',g1:'G6 nie może być mutowany',g2:'sealed holdout nie jest otwierany',g3:'brak historycznego backfillu dla nowych challengerów',g4:'brak automatycznej promocji',g5:'brak zleceń i trade execution',g6:'parametry research pozostają prywatne',
    families:'Rodziny informacji',familyPrice:'Trend ceny',familyRates:'Stopy',familyLiquidity:'Płynność / credit',familyVix:'Opcje / VIX',
    methodology:'Interpretacja',methodText:'To nie jest „43% nauczonego modelu”. Frozen Track zbiera niezależny dowód dla starego G6, a Adaptive Track rozwija nowe hipotezy. Wartość BRACE rośnie dopiero wtedy, gdy nowy challenger ma własną historię prospective i potrafi pokonać benchmark po kosztach i ryzyku.',
    updated:'Aktualizacja',researchOnly:'research-only · brak zleceń',error:'Nie udało się pobrać aktualnego stanu BRACE-SPX.'
  }:{
    eyebrow:'BriefRooms Research · S&P 500',
    hero:'BRACE-SPX: one platform, two learning tracks',
    lead:'Generation 6 remains a frozen out-of-sample experiment. Inside the same platform, the Adaptive Research Track learns only from newly resolved observations and creates challengers without rewriting G6 history.',
    scenarios:'S&P scenarios',lab:'BRACE-SPX Lab',snapshot:'State',
    architecture:'BRACE-SPX architecture',archNote:'We are not launching a second engine. Frozen Track, Adaptive Research and the Promotion Gate share one data pipeline and one governance layer, while keeping evidence boundaries separate.',
    frozen:'Frozen Track',frozenTitle:'G6 · clean validation',frozenText:'The eight original G6 candidates remain unchanged. Their parameters cannot be tuned after new outcomes are observed.',
    adaptive:'Adaptive Research',adaptiveTitle:'Learner · challengers',adaptiveText:'New hypotheses may change weights and rules only as new identifiable challengers with their own start date.',
    promotion:'Promotion Gate',promotionTitle:'G7 · evidence first',promotionText:'A challenger cannot automatically replace G6. After meeting the gates it enters human review and only then may be frozen as G7.',
    immutable:'immutable',active:'ACTIVE',notReady:'NOT READY',ready:'READY FOR REVIEW',
    now:'Where the system is now',development:'G6 development',devDone:'completed',gateFailed:'strict gate failed',shadow:'Frozen shadow',adaptiveStage:'Adaptive research',promotionStage:'G7 promotion',collecting:'collecting evidence',waitingFeatures:'awaiting feature readiness',
    observations:'observations',remaining:'remaining',cycles:'research cycles',challengers:'active challengers',prospectiveN:'prospective N',nextCheckpoint:'next checkpoint',best:'best challenger',noBest:'— insufficient new evidence',
    whyZero:'Why does Adaptive start at N=0?',whyZeroText:'The challengers were created after the first 30 G6 shadow observations had already been seen. Crediting those same sessions as prospective evidence would introduce hidden look-ahead. G6 therefore keeps its 30/70 count, while new challengers start from zero.',
    learning:'How the actual learning loop works',step1:'Forecast + record',step1d:'Every challenger emits a state before the future outcome is known.',step2:'Outcome + lesson',step2d:'After resolution the system measures the result and information-family contribution.',step3:'New challenger',step3d:'At checkpoints 20/35/50/70 a new hypothesis is created with a new prospective boundary.',
    challengeTable:'Adaptive Research challengers',id:'ID',rule:'Rule',origin:'Origin',created:'Start',n:'N',ret:'Return',sharpe:'Sharpe',dd:'Max DD',
    rules:{trend_anchor_macro_veto:'trend + macro veto',balanced_orthogonal:'balanced four-family',robust_family_median:'family median',trend_credit_vol_confirmation:'trend + credit/VIX confirmation'},
    origins:{initial_g6_diagnostic_lessons:'G6 lesson',adaptive_checkpoint_20:'checkpoint 20',adaptive_checkpoint_35:'checkpoint 35',adaptive_checkpoint_50:'checkpoint 50',adaptive_checkpoint_70:'checkpoint 70'},
    event:'Latest learning event',eventInit:'G6 failed the strict gate. Rank instability and weak performance versus the simple 200D Trend were converted into four explicit challenger hypotheses.',eventCheckpoint:'Checkpoint resolved: the learner created a new challenger using only previously resolved evidence.',
    rankCorr:'median fold-rank correlation',g6Sharpe:'G6 best Sharpe',trendSharpe:'200D Trend Sharpe',
    promotionGate:'Promotion Gate → G7',promotionDesc:'Promotion requires at least 70 own prospective observations for the best challenger, risk-adjusted edge versus Buy & Hold, improved drawdown and human approval. Automatic promotion is disabled.',blockers:'Current blockers',noBlockers:'No blockers — ready for human review.',
    governance:'Governance boundaries',g1:'G6 cannot be mutated',g2:'sealed holdout remains unopened',g3:'no historical backfill for new challengers',g4:'no automatic promotion',g5:'no orders or trade execution',g6:'research parameters remain private',
    families:'Information families',familyPrice:'Price trend',familyRates:'Rates',familyLiquidity:'Liquidity / credit',familyVix:'Options / VIX',
    methodology:'Interpretation',methodText:'This is not a “43% trained model”. Frozen Track collects independent evidence for the old G6, while Adaptive Track develops new hypotheses. BRACE becomes decision-relevant only when a challenger has its own prospective history and can beat a benchmark after risk and costs.',
    updated:'Updated',researchOnly:'research-only · no orders',error:'The current BRACE-SPX state could not be loaded.'
  };

  function esc(v){return String(v==null?'—':v).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
  function num(v,d){var n=Number(v);return Number.isFinite(n)?n.toLocaleString(locale,{minimumFractionDigits:d,maximumFractionDigits:d}):'—';}
  function pct(v,d){var n=Number(v);return Number.isFinite(n)?(n*100).toLocaleString(locale,{minimumFractionDigits:d,maximumFractionDigits:d})+'%':'—';}
  function dt(v){var d=new Date(v);return Number.isNaN(d.getTime())?'—':new Intl.DateTimeFormat(locale,{dateStyle:'medium',timeStyle:'short',timeZone:'Europe/Warsaw'}).format(d);}
  function date(v){if(!v)return'—';var d=new Date(v+'T12:00:00Z');return Number.isNaN(d.getTime())?'—':new Intl.DateTimeFormat(locale,{dateStyle:'medium',timeZone:'Europe/Warsaw'}).format(d);}
  function progress(a,b){var x=Number(a),y=Number(b);return y>0?Math.max(0,Math.min(100,x/y*100)):0;}
  function blockerLabel(code){
    var pl={NO_CHALLENGER_WITH_MINIMUM_PROSPECTIVE_EVIDENCE:'brak challengera z minimalną liczbą nowych obserwacji',PROSPECTIVE_N_BELOW_PROMOTION_MINIMUM:'za mało własnych obserwacji prospective',NO_RISK_ADJUSTED_EDGE_VS_BUY_HOLD:'brak przewagi risk-adjusted vs Buy & Hold',DRAWDOWN_NOT_IMPROVED_VS_BUY_HOLD:'brak poprawy max drawdown vs Buy & Hold'};
    var en={NO_CHALLENGER_WITH_MINIMUM_PROSPECTIVE_EVIDENCE:'no challenger has the minimum prospective evidence',PROSPECTIVE_N_BELOW_PROMOTION_MINIMUM:'prospective N below promotion minimum',NO_RISK_ADJUSTED_EDGE_VS_BUY_HOLD:'no risk-adjusted edge vs Buy & Hold',DRAWDOWN_NOT_IMPROVED_VS_BUY_HOLD:'max drawdown not improved vs Buy & Hold'};
    return (lang==='pl'?pl:en)[code]||code;
  }
  function ruleLabel(v){return t.rules[v]||v||'—';}
  function originLabel(v){return t.origins[v]||v||'—';}
  function metric(label,value,desc){return '<div class="brace-metric"><small>'+esc(label)+'</small><strong>'+esc(value)+'</strong><span>'+esc(desc||'')+'</span></div>';}

  fetch('/data/public/brace_spx_platform_public.json?ts='+Date.now(),{cache:'no-store',credentials:'same-origin'})
    .then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();})
    .then(function(report){
      var f=report.frozen_track||{};
      var a=report.adaptive_research||{};
      var p=report.promotion_gate||{};
      var event=a.last_learning_event||{};
      var evEvidence=event.evidence||{};
      var frozenPct=progress(f.observations_collected,f.warmup_required);
      var adaptiveStatus=String(a.status||'');
      var gateReady=p.status==='READY_FOR_HUMAN_REVIEW';
      var challengers=Array.isArray(a.challengers)?a.challengers:[];
      var blockers=Array.isArray(p.blockers)?p.blockers:[];
      var eventText=event.event_type==='CHECKPOINT_CHALLENGER_CREATED'?t.eventCheckpoint:t.eventInit;
      var rows=challengers.map(function(c){
        return '<tr><td><strong>'+esc(c.candidate_id)+'</strong></td><td>'+esc(ruleLabel(c.rule))+'</td><td>'+esc(originLabel(c.origin))+'</td><td>'+esc(date(c.created_market_date))+'</td><td>'+esc(c.prospective_n==null?0:c.prospective_n)+'</td><td>'+esc(c.cumulative_return==null?'—':pct(c.cumulative_return,2))+'</td><td>'+esc(c.sharpe_excess==null?'—':num(c.sharpe_excess,2))+'</td><td>'+esc(c.max_drawdown==null?'—':pct(c.max_drawdown,2))+'</td></tr>';
      }).join('');
      var blockerHtml=blockers.length?'<ul class="brace-method-list">'+blockers.map(function(b){return '<li><strong>'+esc(blockerLabel(b))+'</strong></li>';}).join('')+'</ul>':'<p class="brace-callout is-good">'+esc(t.noBlockers)+'</p>';
      var adaptiveState=adaptiveStatus.indexOf('AWAITING')>=0?t.waitingFeatures:t.collecting;
      var best=a.best_challenger||t.noBest;
      var next=a.next_checkpoint_n==null?'—':a.next_checkpoint_n;

      root.classList.remove('brace-skeleton');
      root.innerHTML='\
        <header class="brace-hero">\
          <span class="brace-eyebrow">'+esc(t.eyebrow)+'</span>\
          <h1>'+esc(t.hero)+'</h1>\
          <p>'+esc(t.lead)+'</p>\
        </header>\
        <nav class="brace-tabs" aria-label="BRACE-SPX navigation">\
          <a href="'+(lang==='pl'?'/pl/inwestycje/spx-scenariusze-2026.html':'/en/investing/spx-scenarios-2026.html')+'">'+esc(t.scenarios)+'</a>\
          <a href="'+(lang==='pl'?'/pl/inwestycje/brace-spx-lab.html':'/en/investing/brace-spx-lab.html')+'" aria-current="page">'+esc(t.lab)+'</a>\
        </nav>\
        <section class="brace-panel brace-platform">\
          <div class="brace-status-row"><div><h2>'+esc(t.architecture)+'</h2><p class="brace-note">'+esc(t.archNote)+'</p></div><span class="brace-status is-info">ONE PLATFORM</span></div>\
          <div class="brace-platform-grid">\
            <article class="brace-track-card"><small>'+esc(t.frozen)+'</small><h3>'+esc(t.frozenTitle)+'</h3><p>'+esc(t.frozenText)+'</p><strong class="big">'+esc((f.observations_collected||0)+' / '+(f.warmup_required||70))+'</strong><p>'+esc(t.immutable)+'</p></article>\
            <article class="brace-track-card"><small>'+esc(t.adaptive)+'</small><h3>'+esc(t.adaptiveTitle)+'</h3><p>'+esc(t.adaptiveText)+'</p><strong class="big">'+esc(a.active_challengers||0)+' challengers</strong><p>'+esc(t.active)+'</p></article>\
            <article class="brace-track-card"><small>'+esc(t.promotion)+'</small><h3>'+esc(t.promotionTitle)+'</h3><p>'+esc(t.promotionText)+'</p><strong class="big">'+esc(p.target_generation||'G7')+'</strong><p>'+esc(gateReady?t.ready:t.notReady)+'</p></article>\
          </div>\
        </section>\
        <section class="brace-panel">\
          <div class="brace-status-row"><h2>'+esc(t.now)+'</h2><span class="brace-updated">'+esc(t.updated)+': '+esc(dt(report.generated_at))+'</span></div>\
          <ul class="brace-stage-list">\
            <li class="is-done"><span class="brace-stage-number">1</span><div><b>'+esc(t.development)+'</b><span>'+esc(t.devDone)+' · '+esc(t.gateFailed)+'</span></div><span class="brace-stage-state">DONE / FAILED GATE</span></li>\
            <li class="is-active"><span class="brace-stage-number">2</span><div><b>'+esc(t.shadow)+'</b><span>'+esc((f.observations_collected||0)+' / '+(f.warmup_required||70))+' · '+esc(f.observations_remaining||0)+' '+esc(t.remaining)+'</span></div><span class="brace-stage-state">'+esc(f.shadow_status||'warming_up')+'</span></li>\
            <li class="is-active"><span class="brace-stage-number">3</span><div><b>'+esc(t.adaptiveStage)+'</b><span>'+esc(adaptiveState)+' · N='+esc(a.initial_challenger_prospective_n||0)+'</span></div><span class="brace-stage-state">'+esc(t.active)+'</span></li>\
            <li><span class="brace-stage-number">4</span><div><b>'+esc(t.promotionStage)+'</b><span>'+esc(p.target_generation||'G7')+' · '+esc(t.promotionDesc)+'</span></div><span class="brace-stage-state">'+esc(gateReady?t.ready:t.notReady)+'</span></li>\
          </ul>\
          <div class="brace-progress-track"><div class="brace-progress-bar" style="width:'+frozenPct.toFixed(1)+'%"></div></div>\
          <div class="brace-progress-copy"><span>'+esc(t.shadow)+': '+esc(f.observations_collected||0)+' '+esc(t.observations)+'</span><strong>'+esc(frozenPct.toFixed(1)+'%')+'</strong><span>'+esc(f.observations_remaining||0)+' '+esc(t.remaining)+'</span></div>\
        </section>\
        <section class="brace-panel brace-adaptive">\
          <div class="brace-status-row"><h2>'+esc(t.adaptiveTitle)+'</h2><span class="brace-status is-pass">'+esc(t.active)+'</span></div>\
          <div class="brace-grid brace-grid-3">\
            '+metric(t.cycles,a.research_cycles||0,adaptiveState)+metric(t.challengers,a.active_challengers||0,'G6-R-Cxx')+metric(t.prospectiveN,a.initial_challenger_prospective_n||0,lang==='pl'?'bez historycznego backfillu':'no historical backfill')+metric(t.nextCheckpoint,next,lang==='pl'?'checkpointy: 20 / 35 / 50 / 70':'checkpoints: 20 / 35 / 50 / 70')+metric(t.best,best,lang==='pl'?'wybór dopiero po min. N=20':'selection only after min. N=20')+metric('Feature rows',a.feature_eligible_rows||0,a.feature_ready?(lang==='pl'?'cechy gotowe':'features ready'):(lang==='pl'?'rozgrzewanie cech':'feature warm-up'))+'\
          </div>\
          <p class="brace-callout is-warning"><strong>'+esc(t.whyZero)+'</strong> '+esc(t.whyZeroText)+'</p>\
        </section>\
        <section class="brace-panel">\
          <h2>'+esc(t.learning)+'</h2>\
          <div class="brace-flow"><div class="brace-flow-step"><b>'+esc(t.step1)+'</b><span>'+esc(t.step1d)+'</span></div><div class="brace-flow-arrow">→</div><div class="brace-flow-step"><b>'+esc(t.step2)+'</b><span>'+esc(t.step2d)+'</span></div><div class="brace-flow-arrow">→</div><div class="brace-flow-step"><b>'+esc(t.step3)+'</b><span>'+esc(t.step3d)+'</span></div></div>\
          <div class="brace-source-list"><span class="brace-source-chip">'+esc(t.familyPrice)+'</span><span class="brace-source-chip">'+esc(t.familyRates)+'</span><span class="brace-source-chip">'+esc(t.familyLiquidity)+'</span><span class="brace-source-chip">'+esc(t.familyVix)+'</span></div>\
        </section>\
        <section class="brace-panel">\
          <h2>'+esc(t.challengeTable)+'</h2>\
          <p class="brace-note">'+esc(lang==='pl'?'Parametry i wagi pozostają na gałęzi badawczej. Publicznie pokazujemy tylko identyfikator, granicę czasu i późniejsze wyniki prospective.':'Parameters and weights remain on the research branch. Publicly we expose only the identifier, evidence boundary and later prospective results.')+'</p>\
          <div class="brace-table-wrap"><table class="brace-table"><thead><tr><th>'+esc(t.id)+'</th><th>'+esc(t.rule)+'</th><th>'+esc(t.origin)+'</th><th>'+esc(t.created)+'</th><th>'+esc(t.n)+'</th><th>'+esc(t.ret)+'</th><th>'+esc(t.sharpe)+'</th><th>'+esc(t.dd)+'</th></tr></thead><tbody>'+rows+'</tbody></table></div>\
        </section>\
        <section class="brace-panel">\
          <h2>'+esc(t.event)+'</h2><div class="brace-event"><span class="brace-event-code">'+esc(event.event_id||'L0001')+'</span><div><strong>'+esc(eventText)+'</strong><p class="brace-note">'+esc(t.rankCorr)+': '+esc(num(evEvidence.median_fold_rank_correlation,3))+' · '+esc(t.g6Sharpe)+': '+esc(num(evEvidence.g6_best_sharpe,2))+' · '+esc(t.trendSharpe)+': '+esc(num(evEvidence.trend_200d_sharpe,2))+'</p></div></div>\
        </section>\
        <section class="brace-panel brace-promotion">\
          <div class="brace-status-row"><h2>'+esc(t.promotionGate)+'</h2><span class="brace-status '+(gateReady?'is-pass':'is-fail')+'">'+esc(gateReady?t.ready:t.notReady)+'</span></div><p class="brace-note">'+esc(t.promotionDesc)+'</p><h3>'+esc(t.blockers)+'</h3>'+blockerHtml+'\
        </section>\
        <section class="brace-panel brace-sealed">\
          <h2>'+esc(t.governance)+'</h2><ul class="brace-method-list"><li><strong>✓ '+esc(t.g1)+'</strong></li><li><strong>✓ '+esc(t.g2)+'</strong></li><li><strong>✓ '+esc(t.g3)+'</strong></li><li><strong>✓ '+esc(t.g4)+'</strong></li><li><strong>✓ '+esc(t.g5)+'</strong></li><li><strong>✓ '+esc(t.g6)+'</strong></li></ul>\
          <p class="brace-callout"><strong>'+esc(t.methodology)+':</strong> '+esc(t.methodText)+'</p>\
        </section>\
        <footer class="brace-footer">BRACE-SPX · '+esc(t.researchOnly)+' · '+esc(t.snapshot)+': '+esc(date(f.latest_market_date))+'</footer>';
    })
    .catch(function(err){
      root.classList.remove('brace-skeleton');
      root.innerHTML='<section class="brace-panel brace-error is-visible"><strong>'+esc(t.error)+'</strong><br><small>'+esc(err&&err.message?err.message:'')+'</small></section>';
    });
})();
