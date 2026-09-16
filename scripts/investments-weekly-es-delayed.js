(() => {
  'use strict';

  const POLL_MS = 30000;
  const MAX_DELAY_MS = 15 * 60 * 1000;
  const isEn = (document.documentElement.lang || 'pl').toLowerCase().startsWith('en');
  let selectedWeek = null;
  let observer = null;
  let applying = false;
  let cached = null;

  function valid(row) {
    const price = Number(row?.price);
    const stamp = new Date(row?.current_price_updated_at || row?.timestamp);
    if (!Number.isFinite(price) || price <= 0 || Number.isNaN(stamp.valueOf())) return null;
    const age = Date.now() - stamp.valueOf();
    if (age < -60000 || age > MAX_DELAY_MS) return null;
    return { price, updatedAt: stamp.toISOString(), source: row?.source || 'ES=F' };
  }

  function card() {
    const items = selectedWeek?.instruments;
    if (!Array.isArray(items)) return null;
    const index = items.findIndex((item) => item?.instrument_id === 'sp500_futures');
    return index < 0 ? null : document.querySelectorAll('#app .cards > .card')[index];
  }

  function formatTime(value) {
    return new Date(value).toLocaleString(isEn ? 'en-GB' : 'pl-PL', {
      timeZone: 'Europe/Warsaw', day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit'
    });
  }

  function apply() {
    if (applying || !cached) return;
    const target = card();
    const nowBox = target?.querySelector('.now');
    const priceNode = nowBox?.querySelector('strong');
    const timeNode = nowBox?.querySelector('small');
    if (!nowBox || !priceNode || !timeNode) return;

    const currentAt = new Date(nowBox.dataset.liveAt || 0).valueOf() || 0;
    const quoteAt = new Date(cached.updatedAt).valueOf() || 0;
    const status = nowBox.dataset.feedStatus || '';
    if ((status === 'live' || status === 'fallback') && currentAt >= quoteAt) return;
    if (status === 'delayed' && currentAt >= quoteAt) return;

    applying = true;
    try {
      const minutes = Math.max(0, Math.round((Date.now() - quoteAt) / 60000));
      priceNode.textContent = cached.price.toLocaleString(isEn ? 'en-US' : 'pl-PL', {
        minimumFractionDigits: 2, maximumFractionDigits: 2
      });
      const label = isEn ? `DELAYED ~${minutes} min` : `OPÓŹNIONY ~${minutes} min`;
      timeNode.textContent = `${isEn ? 'As of' : 'Stan na'}: ${formatTime(cached.updatedAt)} · ${label} · BriefRooms backend · ${cached.source}`;
      timeNode.style.color = '#9fe8ff';
      nowBox.dataset.feedStatus = 'delayed';
      nowBox.dataset.liveAt = cached.updatedAt;
    } finally {
      applying = false;
    }
  }

  async function refresh() {
    try {
      const response = await fetch(`/data/investments/live_prices.json?_=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) return;
      const data = await response.json();
      const quote = valid(data?.prices?.sp500_futures);
      if (!quote) return;
      if (!cached || new Date(quote.updatedAt) > new Date(cached.updatedAt)) cached = quote;
      apply();
    } catch (error) {
      console.warn('BriefRooms Weekly delayed ES fallback failed:', error?.message || error);
    }
  }

  function observe() {
    if (observer) observer.disconnect();
    const nowBox = card()?.querySelector('.now');
    if (!nowBox) return;
    observer = new MutationObserver(apply);
    observer.observe(nowBox, { childList: true, subtree: true, characterData: true, attributes: true });
  }

  document.addEventListener('br:weekly-rendered', (event) => {
    selectedWeek = event?.detail || null;
    observe();
    refresh();
  });

  const timer = window.setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.addEventListener('online', refresh);
  window.addEventListener('pagehide', () => {
    window.clearInterval(timer);
    if (observer) observer.disconnect();
  }, { once: true });
})();
