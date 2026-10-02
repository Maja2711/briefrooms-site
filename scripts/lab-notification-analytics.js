(() => {
  'use strict';

  const root = document.getElementById('notification-analytics');
  if (!root) return;

  const isPl = (document.documentElement.lang || 'pl').toLowerCase().startsWith('pl');
  const T = isPl ? {
    title: 'Notification Analytics',
    lead: 'Zagregowany monitoring powiadomień tradingowych. Liczba subskrypcji oznacza aktywne endpointy urządzeń/przeglądarek, nie unikalnych użytkowników.',
    active: 'Aktywne subskrypcje',
    sent: 'Wysłane push',
    clicked: 'Kliknięcia',
    ctr: 'CTR',
    failed: 'Błędy delivery',
    lastDispatchFailed: 'ostatnia wysyłka',
    sentNow: 'wysłano',
    failedNow: 'błędy',
    expiredNow: 'wygasłe',
    noRecentErrors: 'brak nowych błędów',
    expired: 'Usunięte wygasłe',
    daily: 'Daily',
    weekly: 'Weekly',
    stock: 'Stock',
    open: 'Otwarcie',
    close: 'Zamknięcie',
    last: 'Ostatnia wysyłka',
    unavailable: 'Dane Notification Analytics są chwilowo niedostępne.',
    endpoints: 'endpointów',
  } : {
    title: 'Notification Analytics',
    lead: 'Aggregated monitoring of Trading notifications. Subscription count represents active device/browser endpoints, not unique users.',
    active: 'Active subscriptions',
    sent: 'Pushes sent',
    clicked: 'Clicks',
    ctr: 'CTR',
    failed: 'Delivery errors',
    lastDispatchFailed: 'last dispatch',
    sentNow: 'sent',
    failedNow: 'errors',
    expiredNow: 'expired',
    noRecentErrors: 'no new errors',
    expired: 'Expired removed',
    daily: 'Daily',
    weekly: 'Weekly',
    stock: 'Stock',
    open: 'Open',
    close: 'Close',
    last: 'Last dispatch',
    unavailable: 'Notification Analytics data is temporarily unavailable.',
    endpoints: 'endpoints',
  };

  const nf = new Intl.NumberFormat(isPl ? 'pl-PL' : 'en-US');
  const dtf = new Intl.DateTimeFormat(isPl ? 'pl-PL' : 'en-US', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    timeZone: 'Europe/Warsaw'
  });

  function metric(label, value, cls = '', detail = '') {
    return `<div class="na-metric ${cls}"><small>${label}</small><strong>${value}</strong>${detail ? `<em class="na-metric-detail">${detail}</em>` : ''}</div>`;
  }

  async function load() {
    try {
      const cfgRes = await fetch('/data/notifications/trading-notification-config.json?v=' + Date.now(), {cache: 'no-store'});
      if (!cfgRes.ok) throw new Error('config_http_' + cfgRes.status);
      const cfg = await cfgRes.json();
      const base = String(cfg?.analytics?.api_base || cfg?.background_push?.api_base || '').replace(/\/$/, '');
      if (!base) throw new Error('analytics_base_missing');

      const res = await fetch(base + '/analytics/public?_=' + Date.now(), {cache: 'no-store'});
      if (!res.ok) throw new Error('analytics_http_' + res.status);
      const d = await res.json();
      const b = d.breakdown || {};
      const last = d.last_dispatch_at ? dtf.format(new Date(d.last_dispatch_at)) : '—';
      const recentSent = Number(d.last_dispatch_sent || 0);
      const recentFailed = Number(d.last_dispatch_failed || 0);
      const recentExpired = Number(d.last_dispatch_expired || 0);
      const statusPairs = Object.entries(d.last_failed_statuses || {})
        .filter(([, count]) => Number(count) > 0)
        .map(([status, count]) => `${status}: ${nf.format(Number(count))}`)
        .join(' · ');
      const failureDetail = `${T.lastDispatchFailed}: ${T.sentNow} ${nf.format(recentSent)} · ${T.failedNow} ${nf.format(recentFailed)} · ${T.expiredNow} ${nf.format(recentExpired)}${statusPairs ? ` · ${statusPairs}` : (recentFailed === 0 ? ` · ${T.noRecentErrors}` : '')}`;

      root.innerHTML = `
        <article class="panel notification-analytics-panel">
          <header>
            <div><h2>${T.title}</h2><p>${T.lead}</p></div>
            <span class="status shadow">LIVE AGGREGATES</span>
          </header>
          <div class="na-primary">
            ${metric(T.active, nf.format(Number(d.active_subscriptions || 0)), 'is-blue')}
            ${metric(T.sent, nf.format(Number(d.sent || 0)))}
            ${metric(T.clicked, nf.format(Number(d.clicked || 0)))}
            ${metric(T.ctr, Number(d.ctr_percent || 0).toLocaleString(isPl ? 'pl-PL' : 'en-US', {minimumFractionDigits: 1, maximumFractionDigits: 2}) + '%')}
            ${metric(T.failed, nf.format(Number(d.failed || 0)), Number(d.failed || 0) ? 'is-warn' : '', failureDetail)}
            ${metric(T.expired, nf.format(Number(d.expired_removed || 0)))}
          </div>
          <div class="na-breakdown">
            <div><span>${T.daily}</span><b>${nf.format(Number(b.daily || 0))}</b></div>
            <div><span>${T.weekly}</span><b>${nf.format(Number(b.weekly || 0))}</b></div>
            <div><span>${T.stock}</span><b>${nf.format(Number(b.stock || 0))}</b></div>
            <div><span>${T.open}</span><b>${nf.format(Number(b.open || 0))}</b></div>
            <div><span>${T.close}</span><b>${nf.format(Number(b.close || 0))}</b></div>
          </div>
          <p class="na-foot"><span>${T.last}: <b>${last}</b></span><span>${T.active}: <b>${nf.format(Number(d.active_subscriptions || 0))} ${T.endpoints}</b></span></p>
        </article>`;
    } catch (err) {
      console.error('Notification Analytics:', err);
      root.innerHTML = `<article class="panel notification-analytics-panel"><header><div><h2>${T.title}</h2><p>${T.unavailable}</p></div><span class="status">OFFLINE</span></header></article>`;
    }
  }

  load();
  window.setInterval(load, 60_000);
})();
