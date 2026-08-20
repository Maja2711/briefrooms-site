(() => {
  "use strict";

  const lang = document.documentElement.lang === "en" ? "en" : "pl";
  const CURRENT_URL = "/data/investments/us_daily_stock.json";
  const HISTORY_URL = "/data/investments/us_daily_stock_history/index.json";

  const T = lang === "pl" ? {
    status: "POZYCJA OTWARTA",
    eyebrow: "AKTYWNA POZYCJA PAPER",
    entry: "Cena wejścia (paper/reference)",
    stop: "SL",
    target: "TP",
    holdUntil: "Maks. horyzont",
    copy: (ticker, entry, deadline, target, stop) => `Pozycja ${ticker} pozostaje otwarta. Cena wejścia (paper/reference): ${entry}. Trzymamy ją maksymalnie przez 3 sesje od wyboru, do ${deadline}, chyba że wcześniej zostanie osiągnięty TP ${target} albo SL ${stop}.`,
    extension: (ticker) => `Jeżeli metodologia Daily Trading ponownie wybierze ${ticker} przed zamknięciem, aktywny horyzont zostanie przedłużony do najnowszego valid_until. TP i SL pozostają nadrzędnymi warunkami wyjścia.`,
  } : {
    status: "POSITION OPEN",
    eyebrow: "ACTIVE PAPER POSITION",
    entry: "Entry (paper/reference)",
    stop: "SL",
    target: "TP",
    holdUntil: "Max horizon",
    copy: (ticker, entry, deadline, target, stop) => `${ticker} remains an open paper position. Entry (paper/reference): ${entry}. We hold it for a maximum of 3 trading sessions from selection, through ${deadline}, unless TP ${target} or SL ${stop} is reached earlier.`,
    extension: (ticker) => `If the Daily Trading methodology selects ${ticker} again before the position closes, the active horizon is extended to the newest valid_until. TP and SL remain the primary exit conditions.`,
  };

  const fetchJson = async (url) => {
    const response = await fetch(`${url}?v=${Date.now()}`, { cache: "no-store", headers: { "Cache-Control": "no-cache" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  };

  const nyDate = () => {
    const parts = new Intl.DateTimeFormat("en-CA", {
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

  const latestOpenTrade = (history, current) => {
    const rows = Array.isArray(history?.trades) ? history.trades.filter(unresolved) : [];
    rows.sort((a, b) => String(b.valid_until || b.date || "").localeCompare(String(a.valid_until || a.date || "")) || String(b.date || "").localeCompare(String(a.date || "")));
    if (!rows.length) return null;

    const latest = { ...rows[0] };
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

    const staleBody = card.querySelector(".dsm-empty");
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

    if (staleBody) staleBody.replaceWith(body);
    else if (!card.querySelector(".dsm-open-position")) card.querySelector(".dsm-market-head")?.insertAdjacentElement("afterend", body);
    return true;
  };

  const apply = async () => {
    try {
      const [current, history] = await Promise.all([fetchJson(CURRENT_URL), fetchJson(HISTORY_URL)]);
      const trade = latestOpenTrade(history, current);
      if (!trade?.valid_until || nyDate() > String(trade.valid_until)) return;

      const currentIsStale = Boolean(current?.date && String(current.date) !== nyDate());
      if (!currentIsStale) return;

      if (renderOpen(trade)) return;
      const observer = new MutationObserver(() => {
        if (renderOpen(trade)) observer.disconnect();
      });
      observer.observe(document.body, { childList: true, subtree: true });
      setTimeout(() => observer.disconnect(), 10000);
    } catch (_) {
      // Existing fail-closed renderer remains authoritative if lifecycle data cannot be read.
    }
  };

  apply();
})();
