(() => {
  'use strict';

  const CLOSED = new Set(['zamknięta', 'zamknieta', 'closed']);
  const OPEN = new Set(['otwarta pozycja', 'open position', 'w trakcie', 'in progress', 'open']);

  function normalized(value){
    return String(value || '').trim().toLowerCase();
  }

  function statusNode(card){
    for (const cell of card.querySelectorAll('.cell')) {
      const dt = normalized(cell.querySelector('dt')?.textContent);
      if (dt !== 'status') continue;
      return cell.querySelector('dd') || null;
    }
    return null;
  }

  function cardStatus(card){
    return normalized(statusNode(card)?.textContent);
  }

  function syncBadge(card){
    if (card.classList.contains('integrity-withheld')) return;
    const heading = card.querySelector('.head h3');
    if (!heading) return;

    const status = cardStatus(card);
    const statusDd = statusNode(card);
    const isOpen = OPEN.has(status);
    statusDd?.classList.toggle('br-weekly-status-open', isOpen);

    let type = '';
    let label = '';

    if (isOpen) {
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

  function decorateCard(card){
    syncBadge(card);
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
