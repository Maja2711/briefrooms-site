(function () {
  'use strict';
  const lang = (window.BR_WEEKLY || {}).lang === 'en' ? 'en' : 'pl';
  const monitorText = lang === 'pl'
    ? 'NO TRADE — obserwuję trigger do otwarcia pozycji'
    : 'NO TRADE — monitoring for an entry trigger';

  function apply(event) {
    const week = event && event.detail;
    if (!week || !Array.isArray(week.instruments)) return;
    const cards = Array.from(document.querySelectorAll('#app .cards .card'));
    week.instruments.forEach((item, index) => {
      if (!item || item.wes_status !== 'no_trade_monitoring_trigger') return;
      const card = cards[index];
      if (!card) return;
      const heading = card.querySelector('.head h3');
      if (heading) heading.textContent = 'NO TRADE';
      const cells = Array.from(card.querySelectorAll('.cell'));
      for (const cell of cells) {
        const dt = cell.querySelector('dt');
        const dd = cell.querySelector('dd');
        if (!dt || !dd) continue;
        const key = dt.textContent.trim().toLowerCase();
        if (key === 'status') dd.textContent = monitorText;
      }
      card.setAttribute('data-wes-status', 'monitoring-trigger');
    });
  }

  document.addEventListener('br:weekly-rendered', apply);
}());
