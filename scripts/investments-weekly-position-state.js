(() => {
  'use strict';

  const CLOSED = new Set(['zamknięta', 'zamknieta', 'closed']);
  const OPEN = new Set(['w trakcie', 'in progress', 'open']);

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

  function decorateCard(card){
    if (card.classList.contains('integrity-withheld')) return;
    const heading = card.querySelector('.head h3');
    if (!heading) return;

    const previous = heading.querySelector('.br-weekly-position-state');
    if (previous) previous.remove();

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
      return;
    }

    const badge = document.createElement('span');
    badge.className = `br-weekly-position-state ${type}`;
    badge.textContent = label;
    badge.setAttribute('aria-label', label);
    heading.appendChild(badge);
  }

  function decorate(){
    document.querySelectorAll('#app .card').forEach(decorateCard);
  }

  document.addEventListener('br:weekly-rendered', decorate);
  document.addEventListener('DOMContentLoaded', decorate, { once:true });

  const app = document.getElementById('app');
  if (app) {
    const observer = new MutationObserver(decorate);
    observer.observe(app, { childList:true, subtree:true });
  }
})();
