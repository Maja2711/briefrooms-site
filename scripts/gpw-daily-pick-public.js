(() => {
  "use strict";

  // Presentation ownership moved to /pl/inwestycje/daily-trading.html.
  // Keep the legacy Portfolio 10K markup source-compatible with automated
  // renderers, but remove the Daily Trading card at runtime so Portfolio stays
  // a pure BRACE / long-horizon portfolio surface during the non-breaking cutover.
  if (window.location.pathname === "/pl/inwestycje/portfel-10k.html") {
    const legacyRoot = document.getElementById("gpw-daily-pick-root");
    if (legacyRoot) {
      const legacyGrid = legacyRoot.closest(".gpw-pick-grid");
      (legacyGrid || legacyRoot).remove();
    }
    return;
  }

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
