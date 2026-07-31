(function(){
  'use strict';

  var root=document.querySelector('[data-brace-lab-root]');
  if(!root||document.getElementById('brace-a3-panel')) return;

  var language=(document.documentElement.lang||'pl').toLowerCase().indexOf('en')===0?'en':'pl';
  var locale=language==='pl'?'pl-PL':'en-US';

  function pct(value,digits){
    var number=Number(value);
    return Number.isFinite(number)?(number*100).toLocaleString(locale,{minimumFractionDigits:digits,maximumFractionDigits:digits})+'%':'—';
  }
  function num(value,digits){
    var number=Number(value);
    return Number.isFinite(number)?number.toLocaleString(locale,{minimumFractionDigits:digits,maximumFractionDigits:digits}):'—';
  }
  function signedPct(value,digits){
    var number=Number(value);
    if(!Number.isFinite(number)) return '—';
    return (number>0?'+':'')+pct(number,digits);
  }
  function metric(label,value,description){
    var card=document.createElement('div');card.className='brace-metric';
    var small=document.createElement('small');small.textContent=label;
    var strong=document.createElement('strong');strong.textContent=value;
    var span=document.createElement('span');span.textContent=description;
    card.appendChild(small);card.appendChild(strong);card.appendChild(span);
    return card;
  }
  function text(pl,en){return language==='pl'?pl:en;}

  function markArchitecture2AsReference(){
    var heading=document.getElementById('architecture-heading');
    if(heading){
      heading.textContent=text('Architecture 2 — zamrożony benchmark Long / Flat','Architecture 2 — frozen Long / Flat reference');
    }
    var section=heading&&heading.closest?heading.closest('.brace-panel'):null;
    if(section&&!document.getElementById('brace-a2-reference-note')){
      var note=document.createElement('p');
      note.id='brace-a2-reference-note';
      note.className='brace-callout';
      note.innerHTML=text(
        '<strong>Rozdzielenie architektur:</strong> wszystkie wartości 0% short w sekcjach Architecture 2 są prawidłowe — A2 nigdy nie mogła zajmować pozycji short. Nowy mandat Long / Short / Flat jest badany oddzielnie w Architecture 3 pokazanej powyżej.',
        '<strong>Architecture separation:</strong> all 0% short values in the Architecture 2 sections are correct — A2 was never allowed to short. The new Long / Short / Flat mandate is researched separately in Architecture 3 shown above.'
      );
      var statusRow=section.querySelector('.brace-status-row');
      if(statusRow&&statusRow.nextSibling) section.insertBefore(note,statusRow.nextSibling);
      else section.appendChild(note);
    }
  }

  function render(report){
    var architecture=report.architecture||{};
    var diagnostic=report.diagnostic_leader||{};
    var metrics=diagnostic.metrics||{};
    var directional=diagnostic.directional_diagnostics||{};
    var validation=report.validation||{};
    var checks=validation.checks||{};
    var holdout=report.sealed_holdout||{};
    var progress=report.progress||{};

    var panel=document.createElement('section');
    panel.id='brace-a3-panel';
    panel.className='brace-panel';
    panel.setAttribute('aria-labelledby','brace-a3-heading');
    panel.style.border='2px solid rgba(124,58,237,.24)';
    panel.style.boxShadow='0 16px 34px rgba(76,29,149,.10)';

    var row=document.createElement('div');row.className='brace-status-row';
    var titleWrap=document.createElement('div');
    var eyebrow=document.createElement('p');eyebrow.style.margin='0 0 4px';eyebrow.innerHTML='<strong>'+text('Nowy kierunek badawczy','New research track')+'</strong>';
    var heading=document.createElement('h2');heading.id='brace-a3-heading';heading.textContent=(architecture.labels||{})[language]||'Architecture 3 — Long / Short / Flat';
    titleWrap.appendChild(eyebrow);titleWrap.appendChild(heading);
    var badge=document.createElement('span');badge.className='brace-status is-fail';badge.textContent=(report.status_labels||{})[language]||report.status||'—';
    row.appendChild(titleWrap);row.appendChild(badge);panel.appendChild(row);

    var warning=document.createElement('p');warning.className='brace-callout';
    warning.innerHTML=text(
      '<strong>To wyjaśnia 0% short na stronie:</strong> liczba 0,0% widoczna w dalszej części dotyczy zamrożonej Architecture 2 Long / Flat. Architecture 3 faktycznie zajmowała pozycje short przez <strong>'+pct(metrics.time_short,1)+'</strong> czasu, czyli przez <strong>'+num(directional.short_days,0)+' dni</strong>.',
      '<strong>This explains the 0% short value:</strong> the 0.0% shown later belongs to the frozen Architecture 2 Long / Flat reference. Architecture 3 actually held short exposure for <strong>'+pct(metrics.time_short,1)+'</strong> of the period, or <strong>'+num(directional.short_days,0)+' days</strong>.'
    );
    panel.appendChild(warning);

    var intro=document.createElement('p');intro.className='brace-note';
    intro.textContent=text(
      'Pierwszy zamrożony test A3 obejmuje 12 konstrukcji i 656 łącznych prób. Short miał dodatni wkład w agregacie, ale nie był jeszcze stabilny ani wystarczająco trafny, dlatego nie wybrano championa.',
      'The first frozen A3 test covers 12 constructions and 656 cumulative trials. The short sleeve added value in aggregate, but was not yet stable or accurate enough, so no champion was selected.'
    );
    panel.appendChild(intro);

    var grid=document.createElement('div');grid.className='brace-grid';
    grid.appendChild(metric(text('Mandat','Mandate'),'Long / Short / Flat',text('bez dźwigni i bez zleceń','no leverage and no orders')));
    grid.appendChild(metric(text('Czas long','Time long'),pct(metrics.time_long,1),text('hipotetyczna ekspozycja dodatnia','hypothetical positive exposure')));
    grid.appendChild(metric(text('Czas short','Time short'),pct(metrics.time_short,1),text(num(directional.short_days,0)+' dni pozycji short',num(directional.short_days,0)+' short days')));
    grid.appendChild(metric(text('Czas flat','Time flat'),pct(metrics.time_flat,1),text('brak ekspozycji rynkowej','no market exposure')));
    grid.appendChild(metric(text('Wkład short','Short contribution'),signedPct(directional.short_excess_contribution_annualized,2),text('annualizowany wkład ponad stopę wolną','annualized excess contribution')));
    grid.appendChild(metric(text('Trafność short','Short hit rate'),pct(directional.short_hit_rate,1),text('próg badawczy 50% niezaliczony','50% research threshold not passed')));
    grid.appendChild(metric(text('Dodatnie foldy short','Positive short folds'),num(diagnostic.positive_short_folds,0)+' / '+num((report.development||{}).folds,0),text('wynik nie jest powtarzalny','result is not repeatable')));
    grid.appendChild(metric('CAGR',pct(metrics.cagr,1),text('wynik po kosztach','result after costs')));
    grid.appendChild(metric('Sharpe excess',num(metrics.sharpe_excess,2),text('ponad stopę wolną od ryzyka','above the risk-free rate')));
    grid.appendChild(metric(text('Maks. obsunięcie','Max drawdown'),pct(metrics.max_drawdown,1),text('najgłębszy spadek kapitału','deepest capital decline')));
    grid.appendChild(metric('PBO',pct(validation.pbo,1),text('próg ≤20% zaliczony','≤20% threshold passed')));
    grid.appendChild(metric(text('Globalny DSR','Global DSR'),pct(validation.global_dsr,1),text('po 656 łącznych próbach','after 656 cumulative trials')));
    panel.appendChild(grid);

    var verdict=document.createElement('p');verdict.className='brace-callout';
    verdict.innerHTML=text(
      '<strong>Werdykt A3:</strong> short wniósł około '+signedPct(directional.short_excess_contribution_annualized,2)+' rocznie, lecz trafność wyniosła '+pct(directional.short_hit_rate,1)+' i tylko '+num(diagnostic.positive_short_folds,0)+' z '+num((report.development||{}).folds,0)+' foldów miało dodatni wkład short. Bramka pozostaje niezaliczona, holdout jest zamknięty, a champion nie istnieje.',
      '<strong>A3 verdict:</strong> the short sleeve contributed about '+signedPct(directional.short_excess_contribution_annualized,2)+' annually, but hit rate was '+pct(directional.short_hit_rate,1)+' and only '+num(diagnostic.positive_short_folds,0)+' of '+num((report.development||{}).folds,0)+' folds had positive short contribution. The gate remains unpassed, the holdout is sealed and no champion exists.'
    );
    panel.appendChild(verdict);

    var governance=document.createElement('div');governance.className='brace-grid brace-grid-3';
    governance.style.marginTop='12px';
    governance.appendChild(metric(text('Przestrzeń badań','Research space'),num(progress.experiments_completed,0)+' / '+num(progress.candidate_space_size,0),text('zamrożone konstrukcje','frozen constructions')));
    governance.appendChild(metric(text('Holdout','Holdout'),holdout.accessed?text('otwarty','opened'):text('zapieczętowany','sealed'),text('brak dostępu i brak podglądu','no access and no peeking')));
    governance.appendChild(metric(text('Walidacja niezależna','Independent validation'),checks.independent_validation_passed?text('zaliczona','passed'):text('niezaliczona','not passed'),text('wymagana przed championem','required before a champion')));
    panel.appendChild(governance);

    var a2Heading=document.getElementById('architecture-heading');
    var a2Section=a2Heading&&a2Heading.closest?a2Heading.closest('.brace-panel'):null;
    if(a2Section&&a2Section.parentNode) a2Section.parentNode.insertBefore(panel,a2Section);
    else root.insertBefore(panel,root.firstChild);
    markArchitecture2AsReference();
  }

  fetch('/data/public/brace_spx_architecture_v3_public.json?ts='+Date.now(),{cache:'no-store',credentials:'same-origin'})
    .then(function(response){if(!response.ok) throw new Error('HTTP '+response.status);return response.json();})
    .then(render)
    .catch(function(){markArchitecture2AsReference();});
})();
