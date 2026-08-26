(() => {
  "use strict";

  const lang = document.documentElement.lang === "en" ? "en" : "pl";
  const URLS = {
    gpwCurrent: "/data/investments/gpw_daily_pick.json",
    gpwHistory: "/data/investments/gpw_daily_pick_history_index.json",
    usCurrent: "/data/investments/us_daily_stock.json",
    usHistory: "/data/investments/us_daily_stock_history/index.json",
    fallback: "/data/investments/daily_stock_history_index.json"
  };

  const T = lang === "pl" ? {
    status: "W TOKU",
    eyebrow: "AKTYWNA POZYCJA",
    entry: "Cena wejścia",
    stop: "SL",
    target: "TP",
    validUntil: "Ważna do",
    history: "Historia transakcji",
    historyGpw: "Rynek polski (GPW)",
    historyUs: "Rynek amerykański (USA)",
    closed: "zamkniętych",
    entryShort: "WEJŚCIE",
    exitShort: "WYJŚCIE",
    notActivated: "NIE AKTYWOWANO",
    targetHit: "CEL",
    stopHit: "STOP",
    horizon: "KONIEC HORYZONTU",
    copy: (ticker, entry, target, stop) => `Pozycja ${ticker} pozostaje aktywna. Wejście: ${entry}. TP: ${target}. SL: ${stop}.`,
  } : {
    status: "OPEN",
    eyebrow: "ACTIVE POSITION",
    entry: "Entry price",
    stop: "SL",
    target: "TP",
    validUntil: "Valid through",
    history: "Trade history",
    historyGpw: "Polish market (GPW)",
    historyUs: "US market",
    closed: "closed",
    entryShort: "ENTRY",
    exitShort: "EXIT",
    notActivated: "NOT ACTIVATED",
    targetHit: "TARGET",
    stopHit: "STOP",
    horizon: "HORIZON END",
    copy: (ticker, entry, target, stop) => `${ticker} remains open. Entry: ${entry}. TP: ${target}. SL: ${stop}.`,
  };

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");

  const fetchJson = async (url) => {
    try {
      const response = await fetch(`${url}?v=${Date.now()}`, { cache: "no-store", headers: { "Cache-Control": "no-cache" } });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) {
      return null;
    }
  };

  const money = (value, market) => {
    if (!Number.isFinite(Number(value))) return "—";
    return Number(value).toLocaleString(lang === "pl" ? "pl-PL" : "en-US", {
      style: "currency",
      currency: market === "gpw" ? "PLN" : "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  };

  const dateText = (value) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return String(value || "—");
    const [y, m, d] = String(value).split("-").map(Number);
    return new Intl.DateTimeFormat(lang === "pl" ? "pl-PL" : "en-GB", {
      day: "2-digit", month: "2-digit", year: "numeric"
    }).format(new Date(Date.UTC(y, m - 1, d, 12)));
  };

  const timestampText = (value) => {
    const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
    if (!match) return "";
    const [, y, m, d, hh, mm] = match;
    return lang === "pl" ? `${d}.${m}.${y}, ${hh}:${mm}` : `${d}/${m}/${y}, ${hh}:${mm}`;
  };

  const tickerOf = (row) => String(row?.ticker || row?.symbol || "").trim().toUpperCase();
  const isResolved = (row) => String(row?.outcome?.status || "").toUpperCase() === "RESOLVED";
  const isActive = (row) => !isResolved(row) && row?.outcome?.activated === true;

  const rowsFor = (market, dedicated, fallback) => {
    if (Array.isArray(dedicated?.trades)) return dedicated.trades;
    const block = fallback?.markets?.[market];
    return Array.isArray(block?.trades) ? block.trades : [];
  };

  const latestActive = (rows, current) => {
    const active = rows.filter(isActive).slice().sort((a, b) =>
      String(b.valid_until || b.date || "").localeCompare(String(a.valid_until || a.date || "")) ||
      String(b.date || "").localeCompare(String(a.date || ""))
    );
    if (!active.length) return null;
    const latest = { ...active[0], outcome: { ...(active[0].outcome || {}) } };
    const selection = current?.selection || {};
    if (tickerOf(latest) && tickerOf(latest) === tickerOf(selection)) {
      latest.name = selection.name ?? latest.name;
      latest.stop = selection.stop ?? latest.stop;
      latest.target = selection.target ?? latest.target;
      latest.entry_zone = selection.entry_zone ?? latest.entry_zone;
      latest.valid_until = selection.valid_until ?? latest.valid_until;
    }
    return latest;
  };

  const entryText = (trade, market) => {
    if (Number.isFinite(Number(trade?.outcome?.entry_price))) return money(trade.outcome.entry_price, market);
    if (Number.isFinite(Number(trade?.reference_price))) return money(trade.reference_price, market);
    const zone = Array.isArray(trade?.entry_zone) ? trade.entry_zone : [];
    if (zone.length >= 2) return `${money(zone[0], market)}–${money(zone[1], market)}`;
    return "—";
  };

  const marketCard = (market) => document.querySelector(`.dsm-market-card[data-dsm-market="${market}"]`);

  const renderActive = (trade, market) => {
    const card = marketCard(market);
    if (!card || !trade) return false;
    const ticker = tickerOf(trade) || "—";
    const name = String(trade?.name || "").trim();
    const entry = entryText(trade, market);
    const entryAt = timestampText(trade?.outcome?.activated_at);
    const stop = money(trade?.stop, market);
    const target = money(trade?.target, market);
    const deadline = dateText(trade?.valid_until);

    const status = card.querySelector(".dsm-status");
    if (status) {
      status.className = "dsm-status open-position";
      status.textContent = T.status;
    }

    const panel = document.createElement("div");
    panel.className = "dsm-open-position";
    panel.innerHTML = `
      <div class="dsm-open-position-head">
        <div><span>${escapeHtml(T.eyebrow)}</span><strong>${escapeHtml(ticker)}${name ? ` · ${escapeHtml(name)}` : ""}</strong></div>
        <b>${escapeHtml(T.validUntil)}: ${escapeHtml(deadline)}</b>
      </div>
      <div class="dsm-open-position-levels">
        <div><small>${escapeHtml(T.entry)}</small><b>${escapeHtml(entry)}</b>${entryAt ? `<small>${escapeHtml(entryAt)}</small>` : ""}</div>
        <div><small>${escapeHtml(T.stop)}</small><b>${escapeHtml(stop)}</b></div>
        <div><small>${escapeHtml(T.target)}</small><b>${escapeHtml(target)}</b></div>
      </div>
      <p>${escapeHtml(T.copy(ticker, entry, target, stop))}</p>`;

    const existing = card.querySelector(".dsm-open-position");
    if (existing) existing.replaceWith(panel);
    else {
      const empty = card.querySelector(".dsm-empty");
      if (empty) empty.replaceWith(panel);
      else card.querySelector(".dsm-market-head")?.insertAdjacentElement("afterend", panel);
    }
    return true;
  };

  const outcomeLabel = (row) => {
    const outcome = row?.outcome || {};
    if (!outcome.activated) return { text: T.notActivated, cls: "" };
    const reason = outcome.exit_reason === "target" ? T.targetHit : outcome.exit_reason === "stop" ? T.stopHit : T.horizon;
    const result = Number(outcome.return_percent || 0);
    const r = Number(outcome.r_multiple || 0);
    return {
      text: `${reason} · ${result >= 0 ? "+" : ""}${result.toFixed(2)}% · ${r.toFixed(2)}R`,
      cls: result > 0 ? "positive" : result < 0 ? "negative" : ""
    };
  };

  const closedMarket = (market, rows) => {
    const closed = rows.filter(isResolved).slice(0, 12);
    const title = market === "gpw" ? T.historyGpw : T.historyUs;
    if (!closed.length) return `<section class="dsm-history-market"><h4>${escapeHtml(title)}</h4><p class="dsm-history-summary">0 ${escapeHtml(T.closed)}</p></section>`;
    return `<section class="dsm-history-market"><h4>${escapeHtml(title)}</h4><p class="dsm-history-summary">${closed.length} ${escapeHtml(T.closed)}</p>${closed.map((row) => {
      const outcome = row?.outcome || {};
      const result = outcomeLabel(row);
      const entryAt = timestampText(outcome.activated_at);
      const exitAt = timestampText(outcome.exit_bar_at || outcome.closed_at);
      return `<div class="dsm-history-row dsm-history-row-closed">
        <span class="dsm-history-date">${escapeHtml(row.date || "—")}</span>
        <b>${escapeHtml(row.ticker || row.symbol || "—")}</b>
        <span class="dsm-history-name">${escapeHtml(row.name || row.sector || "")}</span>
        <span class="dsm-history-prices">
          <span><small>${escapeHtml(T.entryShort)}</small><b>${escapeHtml(money(outcome.entry_price, market))}</b>${entryAt ? `<small>${escapeHtml(entryAt)}</small>` : ""}</span>
          <span><small>${escapeHtml(T.exitShort)}</small><b>${escapeHtml(money(outcome.exit_price, market))}</b>${exitAt ? `<small>${escapeHtml(exitAt)}</small>` : ""}</span>
        </span>
        <span class="dsm-history-result ${result.cls}">${escapeHtml(result.text)}</span>
      </div>`;
    }).join("")}</section>`;
  };

  const renderClosedHistory = (gpwRows, usRows) => {
    const holder = document.querySelector("[data-dsm-history]");
    if (!holder) return false;
    const wasOpen = Boolean(holder.querySelector("details.dsm-history")?.open);
    const body = lang === "pl" ? `${closedMarket("gpw", gpwRows)}${closedMarket("us", usRows)}` : closedMarket("us", usRows);
    holder.innerHTML = `<details class="dsm-history"${wasOpen ? " open" : ""}><summary>${escapeHtml(T.history)}</summary><div class="dsm-history-body">${body}</div></details>`;
    return true;
  };

  const apply = async () => {
    const [gpwCurrent, gpwHistory, usCurrent, usHistory, fallback] = await Promise.all([
      fetchJson(URLS.gpwCurrent), fetchJson(URLS.gpwHistory), fetchJson(URLS.usCurrent), fetchJson(URLS.usHistory), fetchJson(URLS.fallback)
    ]);
    const gpwRows = rowsFor("gpw", gpwHistory, fallback);
    const usRows = rowsFor("us", usHistory, fallback);
    const gpwActive = latestActive(gpwRows, gpwCurrent);
    const usActive = latestActive(usRows, usCurrent);

    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      const requiredCardsReady = lang === "pl" ? Boolean(marketCard("gpw") && marketCard("us")) : Boolean(marketCard("us"));
      const historyReady = Boolean(document.querySelector("[data-dsm-history]"));
      if (requiredCardsReady && historyReady) {
        if (gpwActive) renderActive(gpwActive, "gpw");
        if (usActive) renderActive(usActive, "us");
        renderClosedHistory(gpwRows, usRows);
        window.clearInterval(timer);
      } else if (attempts >= 80) {
        window.clearInterval(timer);
      }
    }, 250);
  };

  apply();
})();
