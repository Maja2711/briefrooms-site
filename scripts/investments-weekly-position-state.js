(() => {
  'use strict';

  const CLOSED = new Set(['zamknięta', 'zamknieta', 'closed']);
  const OPEN = new Set(['w trakcie', 'in progress', 'open']);
  const isPl = (document.documentElement.lang || 'pl').toLowerCase().startsWith('pl');

  function normalized(value){
    return String(value || '').trim().toLowerCase();
  }

  function cardStatus(card){
    for (const cell of card.querySelectorAll('.cell')) {
      const dt = normalized(cell.querySelector('dt')?.textContent);
      if (dt !== 'status') continue;
      return normalized(cell.querySelector('dd')?.textContent);
    }
    return '';
  }

  function syncBadge(card){
    if (card.classList.contains('integrity-withheld')) return;
    const heading = card.querySelector('.head h3');
    if (!heading) return;

    const status = cardStatus(card);
    let type = '';
    let label = '';

    if (OPEN.has(status)) {
      type = 'is-open';
      label = 'OPEN';
    } else if (CLOSED.has(status)) {
      type = 'is-closed';
      label = 'CLOSE POSITION';
    } else {
      heading.querySelector('.br-weekly-position-state')?.remove();
      return;
    }

    let badge = heading.querySelector('.br-weekly-position-state');
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'br-weekly-position-state';
      heading.appendChild(badge);
    }

    const nextClass = `br-weekly-position-state ${type}`;
    if (badge.className !== nextClass) badge.className = nextClass;
    if (badge.textContent !== label) badge.textContent = label;
    if (badge.getAttribute('aria-label') !== label) badge.setAttribute('aria-label', label);
  }

  function collapseAnalysis(card){
    const dd = card.querySelector('.cell.big.analysis dd');
    if (!dd || dd.querySelector('.br-weekly-analysis')) return;

    const text = dd.textContent.trim();
    if (!text) return;

    const details = document.createElement('details');
    details.className = 'br-weekly-analysis';

    const summary = document.createElement('summary');
    summary.textContent = isPl ? 'Pokaż opis' : 'Show description';

    const body = document.createElement('div');
    body.className = 'br-weekly-analysis-body';
    body.textContent = text;

    details.append(summary, body);
    dd.textContent = '';
    dd.appendChild(details);
  }

  function decorateCard(card){
    syncBadge(card);
    collapseAnalysis(card);
  }

  function decorate(){
    document.querySelectorAll('#app .card').forEach(decorateCard);
  }

  document.addEventListener('br:weekly-rendered', decorate);
  document.addEventListener('DOMContentLoaded', decorate, { once:true });

  const app = document.getElementById('app');
  if (app) {
    let scheduled = false;
    const observer = new MutationObserver(() => {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(() => {
        scheduled = false;
        decorate();
      });
    });
    observer.observe(app, { childList:true, subtree:true });
  }

  decorate();
})();
