(function () {
  'use strict';

  const CFG = window.BR_WEEKLY || {};
  const L = CFG.lang === 'en' ? 'en' : 'pl';
  const LABELS = L === 'pl'
    ? { opened: 'Otwarto', closed: 'Zamknięto' }
    : { opened: 'Opened', closed: 'Closed' };

  function fmtTime(value) {
    if (!value) return '';
    const date = new Date(String(value));
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleString(L === 'pl' ? 'pl-PL' : 'en-GB', {
      timeZone: 'Europe/Warsaw',
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  function ensureStyle() {
    if (document.getElementById('br-weekly-trade-times-style')) return;
    const style = document.createElement('style');
    style.id = 'br-weekly-trade-times-style';
    style.textContent = '.trade-time{display:block;margin-top:5px;color:#8fa4b8;font-size:.72rem;font-weight:700;line-height:1.35;letter-spacing:0}.history .trade-time{font-size:.70rem;white-space:nowrap}';
    document.head.appendChild(style);
  }

  function appendTime(cell, label, value) {
    if (!cell) return;
    const time = fmtTime(value);
    const existing = cell.querySelector('.trade-time');
    if (!time) {
      if (existing) existing.remove();
      return;
    }
    const line = existing || document.createElement('small');
    line.className = 'trade-time';
    line.textContent = `${label}: ${time}`;
    if (!existing) cell.appendChild(line);
  }

  function decorateSelectedWeek(week) {
    const items = Array.isArray(week?.instruments) ? week.instruments : [];
    const cards = document.querySelectorAll('#app .cards .card');
    cards.forEach((card, index) => {
      if (card.classList.contains('integrity-withheld')) return;
      const item = items[index];
      if (!item) return;
      const cells = card.querySelectorAll('.grid .cell');
      appendTime(cells[0]?.querySelector('dd'), LABELS.opened, item.entry_captured_at);
      appendTime(cells[1]?.querySelector('dd'), LABELS.closed, item.exit_captured_at);
    });
  }

  ensureStyle();
  document.addEventListener('br:weekly-rendered', (event) => {
    decorateSelectedWeek(event.detail || null);
  });
}());
