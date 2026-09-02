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
    status: "W TOKU", eyebrow: "AKTYWNA POZYCJA", entry: "Cena wejścia", current: "Cena teraz", lastMark: "Ostatni kurs", stop: "SL", target: "TP",
    validUntil: "Do końca tygodnia", weeklyPolicy: "Pozycję trzymamy do końca tygodnia handlowego, chyba że wcześniej zadziała TP lub SL.",
    history: "Historia transakcji", historyGpw: "Rynek polski (GPW)", historyUs: "Rynek amerykański (USA)",
    open: "w toku", closed: "zamkniętych", entryShort: "WEJŚCIE", exitShort: "WYJŚCIE", markShort: "TERAZ", lastMarkShort: "OSTATNI",
    notActivated: "NIE AKTYWOWANO", targetHit: "TP", stopHit: "SL", horizon: "KONIEC TYGODNIA", rotation: "ROTACJA",
    copy: (ticker, entry, target, stop) => `Pozycja ${ticker} pozostaje aktywna. Wejście: ${entry}. TP: ${target}. SL: ${stop}.`,
  } : {
    status: "OPEN", eyebrow: "ACTIVE POSITION", entry: "Entry price", current: "Current price", lastMark: "Last mark", stop: "SL", target: "TP",
    validUntil: "End of trading week", weeklyPolicy: "The position is held through the end of the trading week unless TP or SL is hit first.",
    history: "Trade history", historyGpw: "Polish market (GPW)", historyUs: "US market",
    open: "open", closed: "closed", entryShort: "ENTRY", exitShort: "EXIT", markShort: "NOW", lastMarkShort: "LAST",
    notActivated: "NOT ACTIVATED", targetHit: "TP", stopHit: "SL", horizon: "WEEK END", rotation: "ROTATION",
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
    } catch (_) { return null; }
  };

  const money = (value, market) => {
    if (!Number.isFinite(Number(value))) return "—";
    return Number(value).toLocaleString(lang === "pl" ? "pl-PL" : "en-US", {
      style: "currency", currency: market === "gpw" ? "PLN" : "USD", minimumFractionDigits: 2, maximumFractionDigits: 2
    });
  };

  const marketDate = (market) => {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: market === "gpw" ? "Europe/Warsaw" : "America/New_York",
      year: "numeric", month: "2-digit", day: "2-digit"
    }).formatToParts(new Date());
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day}`;
  };

  const dateText = (value) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return String(value || "—");
    const [y, m, d] = String(value).split("-").map(Number);
    return new Intl.DateTimeFormat(lang === "pl" ? "pl-PL" : "en-GB", { day: "2-digit", month: "2-digit", year: "numeric" })
      .format(new Date(Date.UTC(y, m - 1, d, 12)));
  };

  const tradeAtText = (value) => {
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

  const latestHistoryActive = (rows, current) => {
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

  const activeFromCurrent = (market, rows, current) => {
    const position = current?.position || {};
    if (String(position?.status || "").toUpperCase() !== "OPEN" || !tickerOf(position)) return latestHistoryActive(rows, current);
    const sourceDate = String(position.source_history_date || "");
    const base = rows.find((row) => tickerOf(row) === tickerOf(position) && (!sourceDate || String(row.date || "") === sourceDate)) || {};
    const selection = current?.selection || {};
    const snapshot = selection?.market_snapshot || {};
    const markIsCurrent = Boolean(snapshot?.date && String(snapshot.date) === marketDate(market));
    return {
      ...base,
      date: sourceDate || base.date,
      ticker: tickerOf(position),
      symbol: tickerOf(position),
      name: selection.name ?? position.name ?? base.name,
      sector: selection.sector ?? position.sector ?? base.sector,
      stop: position.stop ?? selection.stop ?? base.stop,
      target: position.target ?? selection.target ?? base.target,
      valid_until: position.valid_until ?? selection.valid_until ?? base.valid_until,
      mark: position.mark,
      mark_is_current: markIsCurrent,
      unrealized_percent: position.unrealized_percent,
      current_r: position.current_r,
      holding_policy: selection.holding_policy || "END_OF_TRADING_WEEK",
      outcome: {
        ...(base.outcome || {}),
        status: "OPEN",
        activated: true,
        activated_at: position.opened_at || base?.outcome?.activated_at,
        entry_price: position.entry ?? base?.outcome?.entry_price,
        position_id: position.position_id || base?.outcome?.position_id,
      }
    };
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
    const entryAt = tradeAtText(trade?.outcome?.activated_at);
    const mark = money(trade?.mark, market);
    const markLabel = trade?.mark_is_current ? T.current : T.lastMark;
    const stop = money(trade?.stop, market);
    const target = money(trade?.target, market);
    const deadline = dateText(trade?.valid_until);
    const pnl = Number.isFinite(Number(trade?.unrealized_percent)) ? `${Number(trade.unrealized_percent) >= 0 ? "+" : ""}${Number(trade.unrealized_percent).toFixed(2)}%` : "";
    const r = Number.isFinite(Number(trade?.current_r)) ? `${Number(trade.current_r) >= 0 ? "+" : ""}${Number(trade.current_r).toFixed(2)}R` : "";

    const status = card.querySelector(".dsm-status");
    if (status) { status.className = "dsm-status open-position"; status.textContent = T.status; }
    card.querySelector(".dsm-pick")?.remove();

    const panel = document.createElement("div");
    panel.className = "dsm-open-position";
    panel.innerHTML = `
      <div class="dsm-open-position-head">
        <div><span>${escapeHtml(T.eyebrow)}</span><strong>${escapeHtml(ticker)}${name ? ` · ${escapeHtml(name)}` : ""}</strong></div>
        <b>${escapeHtml(T.validUntil)}: ${escapeHtml(deadline)}</b>
      </div>
      <div class="dsm-open-position-levels">
        <div><small>${escapeHtml(T.entry)}</small><b>${escapeHtml(entry)}</b><small>${escapeHtml(entryAt)}</small></div>
        <div><small>${escapeHtml(markLabel)}</small><b>${escapeHtml(mark)}</b><small>${escapeHtml([pnl, r].filter(Boolean).join(" · "))}</small></div>
        <div><small>${escapeHtml(T.stop)}</small><b>${escapeHtml(stop)}</b></div>
        <div><small>${escapeHtml(T.target)}</small><b>${escapeHtml(target)}</b></div>
      </div>
      <p>${escapeHtml(T.copy(ticker, entry, target, stop))} ${escapeHtml(T.weeklyPolicy)}</p>`;

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
    if (!isResolved(row)) return outcome.activated === true ? { text: T.status, cls: "" } : { text: T.notActivated, cls: "" };
    if (!outcome.activated) return { text: T.notActivated, cls: "" };
    const reason = outcome.exit_reason === "target" ? T.targetHit
      : outcome.exit_reason === "stop" ? T.stopHit
      : outcome.exit_reason === "rotation" ? T.rotation
      : T.horizon;
    const result = Number(outcome.return_percent || 0);
    const r = Number(outcome.r_multiple || 0);
    return { text: `${reason} · ${result >= 0 ? "+" : ""}${result.toFixed(2)}% · ${r.toFixed(2)}R`, cls: result > 0 ? "positive" : result < 0 ? "negative" : "" };
  };

  const openHistoryRow = (market, active) => {
    if (!active) return "";
    const result = outcomeLabel(active);
    const entryAt = tradeAtText(active?.outcome?.activated_at);
    const markLabel = active?.mark_is_current ? T.markShort : T.lastMarkShort;
    return `<div class="dsm-history-row dsm-history-row-open">
      <span class="dsm-history-date">${escapeHtml(active.date || "—")}</span>
      <b>${escapeHtml(tickerOf(active) || "—")}</b>
      <span class="dsm-history-name">${escapeHtml(active.name || active.sector || "")}</span>
      <span class="dsm-history-prices">
        <span><small>${escapeHtml(T.entryShort)}</small><b>${escapeHtml(money(active?.outcome?.entry_price, market))}</b><small>${escapeHtml(entryAt)}</small></span>
        <span><small>${escapeHtml(markLabel)}</small><b>${escapeHtml(money(active?.mark, market))}</b></span>
      </span>
      <span class="dsm-history-result ${result.cls}">${escapeHtml(result.text)}</span>
    </div>`;
  };

  const marketHistory = (market, rows, active) => {
    const closed = rows.filter(isResolved).slice(0, 12);
    const title = market === "gpw" ? T.historyGpw : T.historyUs;
    const openCount = active ? 1 : 0;
    const summary = `${openCount} ${escapeHtml(T.open)} · ${closed.length} ${escapeHtml(T.closed)}`;
    const openRow = openHistoryRow(market, active);
    const closedRows = closed.map((row) => {
      const outcome = row?.outcome || {};
      const result = outcomeLabel(row);
      const entryAt = tradeAtText(outcome.activated_at);
      const exitAt = tradeAtText(outcome.exit_at || outcome.exit_bar_at || outcome.closed_at);
      return `<div class="dsm-history-row dsm-history-row-closed">
        <span class="dsm-history-date">${escapeHtml(row.date || "—")}</span>
        <b>${escapeHtml(row.ticker || row.symbol || "—")}</b>
        <span class="dsm-history-name">${escapeHtml(row.name || row.sector || "")}</span>
        <span class="dsm-history-prices">
          <span><small>${escapeHtml(T.entryShort)}</small><b>${escapeHtml(money(outcome.entry_price, market))}</b><small>${escapeHtml(entryAt)}</small></span>
          <span><small>${escapeHtml(T.exitShort)}</small><b>${escapeHtml(money(outcome.exit_price, market))}</b><small>${escapeHtml(exitAt)}</small></span>
        </span>
        <span class="dsm-history-result ${result.cls}">${escapeHtml(result.text)}</span>
      </div>`;
    }).join("");
    return `<section class="dsm-history-market"><h4>${escapeHtml(title)}</h4><p class="dsm-history-summary">${summary}</p>${openRow}${closedRows}</section>`;
  };

  const renderHistory = (gpwRows, usRows, gpwActive, usActive) => {
    const holder = document.querySelector("[data-dsm-history]");
    if (!holder) return false;
    const wasOpen = Boolean(holder.querySelector("details.dsm-history")?.open);
    const body = lang === "pl"
      ? `${marketHistory("gpw", gpwRows, gpwActive)}${marketHistory("us", usRows, usActive)}`
      : marketHistory("us", usRows, usActive);
    holder.innerHTML = `<details class="dsm-history"${wasOpen ? " open" : ""}><summary>${escapeHtml(T.history)}</summary><div class="dsm-history-body">${body}</div></details>`;
    return true;
  };

  const applyOnce = async () => {
    const [gpwCurrent, gpwHistory, usCurrent, usHistory, fallback] = await Promise.all([
      fetchJson(URLS.gpwCurrent), fetchJson(URLS.gpwHistory), fetchJson(URLS.usCurrent), fetchJson(URLS.usHistory), fetchJson(URLS.fallback)
    ]);
    const gpwRows = rowsFor("gpw", gpwHistory, fallback);
    const usRows = rowsFor("us", usHistory, fallback);
    const gpwActive = activeFromCurrent("gpw", gpwRows, gpwCurrent);
    const usActive = activeFromCurrent("us", usRows, usCurrent);

    if (gpwActive) renderActive(gpwActive, "gpw");
    if (usActive) renderActive(usActive, "us");
    renderHistory(gpwRows, usRows, gpwActive, usActive);
  };

  const boot = () => {
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      const requiredCardsReady = lang === "pl" ? Boolean(marketCard("gpw") && marketCard("us")) : Boolean(marketCard("us"));
      if (requiredCardsReady && document.querySelector("[data-dsm-history]")) {
        window.clearInterval(timer);
        applyOnce();
        window.setInterval(applyOnce, 60000);
      } else if (attempts >= 80) window.clearInterval(timer);
    }, 250);
  };

  boot();
})();
