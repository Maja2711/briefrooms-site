(() => {
  "use strict";

  const lang = document.documentElement.lang === "en" ? "en" : "pl";
  const CURRENT_URL = "/data/investments/us_daily_stock.json";
  const US_HISTORY_URL = "/data/investments/us_daily_stock_history/index.json";
  const FALLBACK_HISTORY_URL = "/data/investments/daily_stock_history_index.json";

  const T = lang === "pl" ? {
    status: "POZYCJA OTWARTA",
    eyebrow: "AKTYWNA POZYCJA",
    entry: "Cena wejścia",
    stop: "SL",
    target: "TP",
    holdUntil: "Maks. horyzont",
    copy: (ticker, entry, deadline, target, stop) => `Pozycja ${ticker} pozostaje otwarta. Cena wejścia: ${entry}. Trzymamy ją maksymalnie przez 3 sesje od wyboru, do ${deadline}, chyba że wcześniej zostanie osiągnięty TP ${target} albo SL ${stop}.`,
    extension: (ticker) => `Jeżeli metodologia Daily Trading ponownie wybierze ${ticker} przed zamknięciem, aktywny horyzont zostanie przedłużony do najnowszego valid_until. TP i SL pozostają nadrzędnymi warunkami wyjścia.`,
    legal: "Moduł badawczy. Nie stanowi rekomendacji inwestycyjnej.",
  } : {
    status: "POSITION OPEN",
    eyebrow: "ACTIVE POSITION",
    entry: "Entry",
    stop: "SL",
    target: "TP",
    holdUntil: "Max horizon",
    copy: (ticker, entry, deadline, target, stop) => `${ticker} remains open. Entry: ${entry}. We hold it for a maximum of 3 trading sessions from selection, through ${deadline}, unless TP ${target} or SL ${stop} is reached earlier.`,
    extension: (ticker) => `If the Daily Trading methodology selects ${ticker} again before the position closes, the active horizon is extended to the newest valid_until. TP and SL remain the primary exit conditions.`,
    legal: "Research module. Not investment advice.",
  };

  const fetchJson = async (url) => {
    const response = await fetch(`${url}?v=${Date.now()}`, { cache: "no-store", headers: { "Cache-Control": "no-cache" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  };

  const tryJson = async (url) => {
    try { return await fetchJson(url); } catch (_) { return null; }
  };

  const cleanVisibleCopy = () => {
    document.querySelectorAll(".dsm-legal").forEach((node) => { node.textContent = T.legal; });
    document.querySelectorAll(".dsm-market-details h4").forEach((node) => {
      if (node.textContent.trim() === "Resolved paper trades") node.textContent = "Resolved trades";
    });
  };

  cleanVisibleCopy();
  let cleanAttempts = 0;
  const cleanTimer = window.setInterval(() => {
    cleanAttempts += 1;
    cleanVisibleCopy();
    if (cleanAttempts >= 40) window.clearInterval(cleanTimer);
  }, 250);

  const nyDate = () => {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit"
    }).formatToParts(new Date());
    const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${map.year}-${map.month}-${map.day}`;
  };

  const fmtMoney = (value) => Number.isFinite(Number(value))
    ? Number(value).toLocaleString(lang === "pl" ? "pl-PL" : "en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : "—";

  const fmtDate = (value) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return "—";
    const [y, m, d] = String(value).split("-").map(Number);
    return new Intl.DateTimeFormat(lang === "pl" ? "pl-PL" : "en-GB", { day: "2-digit", month: "2-digit", year: "numeric" })
      .format(new Date(Date.UTC(y, m - 1, d, 12)));
  };

  const tickerOf = (row) => String(row?.ticker || row?.symbol || "").trim().toUpperCase();
  const unresolved = (row) => String(row?.outcome?.status || "PENDING").toUpperCase() !== "RESOLVED";

  const historyRows = (usHistory, fallback) => {
    if (Array.isArray(usHistory?.trades) && usHistory.trades.length) return usHistory.trades;
    const fallbackUs = fallback?.markets?.us;
    if (Array.isArray(fallbackUs?.trades)) return fallbackUs.trades;
    return [];
  };

  const latestOpenTrade = (rows, current) => {
    const open = rows.filter(unresolved).slice();
    open.sort((a, b) => String(b.valid_until || b.date || "").localeCompare(String(a.valid_until || a.date || "")) || String(b.date || "").localeCompare(String(a.date || "")));
    if (!open.length) return null;

    const latest = { ...open[0] };
    const currentSelection = current?.selection || {};
    if (tickerOf(latest) && tickerOf(latest) === tickerOf(currentSelection)) {
      latest.reference_price = currentSelection.reference_price ?? latest.reference_price;
      latest.entry_zone = currentSelection.entry_zone ?? latest.entry_zone;
      latest.stop = currentSelection.stop ?? latest.stop;
      latest.target = currentSelection.target ?? latest.target;
      latest.name = currentSelection.name ?? latest.name;
      if (currentSelection.valid_until && String(currentSelection.valid_until) > String(latest.valid_until || "")) {
        latest.valid_until = currentSelection.valid_until;
      }
    }
    return latest;
  };

  const entryText = (trade) => {
    if (Number.isFinite(Number(trade?.reference_price))) return fmtMoney(trade.reference_price);
    const zone = Array.isArray(trade?.entry_zone) ? trade.entry_zone : [];
    if (zone.length >= 2 && Number.isFinite(Number(zone[0])) && Number.isFinite(Number(zone[1]))) {
      return `${fmtMoney(zone[0])}–${fmtMoney(zone[1])}`;
    }
    return "—";
  };

  const cardNode = () => document.querySelector(
    '#gpw-daily-pick-root .dsm-market-card[data-dsm-market="us"], #us-daily-stock-root .dsm-market-card[data-dsm-market="us"]'
  );

  const renderOpen = (trade) => {
    const card = cardNode();
    if (!card) return false;

    const ticker = tickerOf(trade) || "—";
    const name = String(trade?.name || "").trim();
    const deadline = fmtDate(trade?.valid_until);
    const entry = entryText(trade);
    const stop = fmtMoney(trade?.stop);
    const target = fmtMoney(trade?.target);

    const status = card.querySelector(".dsm-status");
    if (status) {
      status.className = "dsm-status open-position";
      status.textContent = T.status;
    }

    const body = document.createElement("div");
    body.className = "dsm-open-position";
    body.innerHTML = `
      <div class="dsm-open-position-head">
        <div><span>${T.eyebrow}</span><strong>${ticker}${name ? ` · ${name}` : ""}</strong></div>
        <b>${T.holdUntil}: ${deadline}</b>
      </div>
      <div class="dsm-open-position-levels">
        <div><small>${T.entry}</small><b>${entry}</b></div>
        <div><small>${T.stop}</small><b>${stop}</b></div>
        <div><small>${T.target}</small><b>${target}</b></div>
      </div>
      <p>${T.copy(ticker, entry, deadline, target, stop)}</p>
      <p class="dsm-open-position-rule">${T.extension(ticker)}</p>`;

    const existing = card.querySelector(".dsm-open-position");
    if (existing) existing.replaceWith(body);
    else {
      const staleBody = card.querySelector(".dsm-empty");
      if (staleBody) staleBody.replaceWith(body);
      else card.querySelector(".dsm-market-head")?.insertAdjacentElement("afterend", body);
    }
    cleanVisibleCopy();
    return true;
  };

  const apply = async () => {
    const [current, usHistory, fallback] = await Promise.all([
      tryJson(CURRENT_URL),
      tryJson(US_HISTORY_URL),
      tryJson(FALLBACK_HISTORY_URL),
    ]);

    const trade = latestOpenTrade(historyRows(usHistory, fallback), current);
    if (!trade?.valid_until || nyDate() > String(trade.valid_until)) return;

    const currentDate = String(current?.date || "");
    const currentTicker = tickerOf(current?.selection || {});
    const freshDifferentTrade = currentDate === nyDate()
      && current?.decision === "TRADE"
      && currentTicker
      && currentTicker !== tickerOf(trade);
    if (freshDifferentTrade) return;

    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      cleanVisibleCopy();
      if (renderOpen(trade) || attempts >= 80) window.clearInterval(timer);
    }, 250);
    renderOpen(trade);
  };

  apply();
})();
