(() => {
  "use strict";
  if (window.__BR_DAILY_STOCK_MARKETS_BOOTSTRAP__) return;
  window.__BR_DAILY_STOCK_MARKETS_BOOTSTRAP__ = true;
  if (!document.querySelector('link[href*="daily-stock-markets.css"]')) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/assets/daily-stock-markets.css?v=1";
    document.head.appendChild(link);
  }
  const script = document.createElement("script");
  script.src = "/scripts/daily-stock-markets-public.js?v=3";
  script.defer = true;
  document.head.appendChild(script);
})();