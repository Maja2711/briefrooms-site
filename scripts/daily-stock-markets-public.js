(() => {
  "use strict";

  if (window.__BR_DAILY_STOCK_MARKETS_RENDERED__) return;
  const root = document.getElementById("gpw-daily-pick-root") || document.getElementById("us-daily-stock-root");
  if (!root) return;
  window.__BR_DAILY_STOCK_MARKETS_RENDERED__ = true;

  const lang = document.documentElement.lang === "en" ? "en" : "pl";
  const URLS = {
    gpw: "/data/investments/gpw_daily_pick.json",
    us: "/data/investments/us_daily_stock.json",
    gpwHistory: "/data/investments/gpw_daily_pick_history_index.json",
    usHistory: "/data/investments/us_daily_stock_history/index.json",
    historyFallback: "/data/investments/daily_stock_history_index.json"
  };

  const T = lang === "pl" ? {
    kicker: "BriefRooms Research · Daily Stock Core",
    title: "DAILY TRADE — GPW + USA",
    intro: "Jeden wspólny rdzeń scoringu, momentum, ryzyka, R/R i uczenia. Dwa adaptery zachowują lokalne źródła, walutę, kalendarz i moment potwierdzenia sesji.",
    core: "WSPÓLNY CORE · ODDZIELNA PAMIĘĆ RYNKÓW",
    gpwMarket: "Rynek polski",
    usMarket: "Rynek amerykański",
    gpwTitle: "GPW DAILY TRADE",
    usTitle: "US DAILY STOCK",
    updated: "Aktualizacja",
    checking: "SPRAWDZANIE",
    trade: "TRANSAKCJA",
    noTrade: "BRAK TRANSAKCJI",
    pending: "ANALIZA / OCZEKIWANIE",
    stale: "DANE NIEAKTUALNE",
    loading: "Ładowanie bieżącego sygnału…",
    noSelection: "Brak potwierdzonego wyboru dla tej sesji.",
    score: "Ocena",
    thesis: "Teza na 1–2 sesje",
    why: "Dlaczego teraz",
    entry: "Strefa wejścia",
    stop: "Stop",
    target: "Cel",
    valid: "Ważność planu",
    details: "Metoda, ocena i wyniki",
    scoreBreakdown: "Skład oceny",
    metrics: "Wyniki zakończonych transakcji",
    trades: "Transakcje",
    winRate: "Skuteczność",
    avgReturn: "Średni wynik",
    avgR: "Średnio R",
    learning: "Uczenie",
    learningActive: "Aktywne",
    collecting: "Zbieranie próby",
    history: "Wspólna historia Daily Trade — GPW i USA",
    historyGpw: "Rynek polski (GPW)",
    historyUs: "Rynek amerykański (USA)",
    selected: "wyborów",
    resolved: "rozliczonych",
    current: "W TOKU",
    notActivated: "NIE AKTYWOWANO",
    horizon: "KONIEC HORYZONTU",
    targetHit: "CEL",
    stopHit: "STOP",
    originalPl: "oryginał PL",
    originalEn: "oryginał EN",
    gpwTime: "GPW · PLN · potwierdzenie od 09:05 Warszawa · ESPI/EBI",
    usTime: "NYSE/Nasdaq · USD · potwierdzenie od 09:35 ET · SEC/company releases",
    legal: "Moduł badawczy paper-trading. Nie stanowi rekomendacji inwestycyjnej.",
    coreFooter: "Core: scoring · momentum · risk · R/R · learning",
    catalyst: "Katalizator",
    relative_momentum: "Momentum relatywne",
    volume_liquidity: "Wolumen i płynność",
    market_context: "Rynek i sektor",
    risk_reward: "Zysk / ryzyko",
    historical_expectancy: "Historyczna skuteczność"
  } : {
    kicker: "BriefRooms Research · Daily Stock Core",
    title: "DAILY TRADE — GPW + US",
    intro: "One shared core for scoring, momentum, risk, R/R and learning. Two market adapters preserve local evidence, currency, calendar and session confirmation.",
    core: "SHARED CORE · MARKET MEMORY KEPT SEPARATE",
    gpwMarket: "Polish market",
    usMarket: "US market",
    gpwTitle: "GPW DAILY TRADE",
    usTitle: "US DAILY STOCK",
    updated: "Updated",
    checking: "CHECKING",
    trade: "TRADE",
    noTrade: "NO TRADE",
    pending: "ANALYSIS / WAITING",
    stale: "STALE DATA",
    loading: "Loading the current signal…",
    noSelection: "No validated selection for this session.",
    score: "Score",
    thesis: "1–2 session thesis",
    why: "Why now",
    entry: "Entry zone",
    stop: "Stop",
    target: "Target",
    valid: "Plan valid through",
    details: "Method, score and track record",
    scoreBreakdown: "Score breakdown",
    metrics: "Resolved paper trades",
    trades: "Trades",
    winRate: "Win rate",
    avgReturn: "Average return",
    avgR: "Average R",
    learning: "Learning",
    learningActive: "Active",
    collecting: "Building sample",
    history: "Combined Daily Trade history — GPW and US",
    historyGpw: "Polish market (GPW)",
    historyUs: "US market",
    selected: "selections",
    resolved: "resolved",
    current: "OPEN / PENDING",
    notActivated: "NOT ACTIVATED",
    horizon: "HORIZON END",
    targetHit: "TARGET",
    stopHit: "STOP",
    originalPl: "PL original",
    originalEn: "EN original",
    gpwTime: "GPW · PLN · confirmation from 09:05 Warsaw · ESPI/EBI",
    usTime: "NYSE/Nasdaq · USD · confirmation from 09:35 ET · SEC/company releases",
    legal: "Research paper-trading module. Not investment advice.",
    coreFooter: "Core: scoring · momentum · risk · R/R · learning",
    catalyst: "Catalyst",
    relative_momentum: "Relative momentum",
    volume_liquidity: "Volume & liquidity",
    market_context: "Market & sector",
    risk_reward: "Risk / reward",
    historical_expectancy: "Historical expectancy"
  };

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");

  const safeUrl = (value) => {
    try {
      const parsed = new URL(String(value || ""), window.location.origin);
      return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "#";
    } catch (_) { return "#"; }
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

  const formatTimestamp = (value, market) => {
    if (!value) return "—";
    try {
      return new Intl.DateTimeFormat(lang === "pl" ? "pl-PL" : "en-GB", {
        timeZone: market === "gpw" ? "Europe/Warsaw" : "America/New_York",
        day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit"
      }).format(new Date(value));
    } catch (_) { return "—"; }
  };

  const marketDate = (market) => {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: market === "gpw" ? "Europe/Warsaw" : "America/New_York",
      year: "numeric", month: "2-digit", day: "2-digit"
    }).formatToParts(new Date());
    const v = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${v.year}-${v.month}-${v.day}`;
  };

  const isWeekday = (market) => ["Mon", "Tue", "Wed", "Thu", "Fri"].includes(
    new Intl.DateTimeFormat("en-US", {
      timeZone: market === "gpw" ? "Europe/Warsaw" : "America/New_York",
      weekday: "short"
    }).format(new Date())
  );

  const statusFor = (payload, market) => {
    const stale = Boolean(payload?.date && payload.date !== marketDate(market) && isWeekday(market));
    if (stale) return { text: T.stale, cls: "pending", stale: true };
    const decision = String(payload?.decision || "");
    if ((market === "gpw" && decision === "TRANSAKCJA") || (market === "us" && decision === "TRADE")) {
      return { text: T.trade, cls: "trade", stale: false };
    }
    if ((market === "gpw" && decision === "BRAK_TRANSAKCJI") || (market === "us" && decision === "NO_TRADE")) {
      return { text: T.noTrade, cls: "no-trade", stale: false };
    }
    if ((market === "gpw" && decision === "AWARIA_DANYCH" && payload?.locked) || (market === "us" && decision === "DATA_ERROR" && payload?.locked)) {
      return { text: T.noTrade, cls: "no-trade", stale: false };
    }
    return { text: T.pending, cls: "pending", stale: false };
  };

  const isTrade = (payload, market) => market === "gpw"
    ? payload?.decision === "TRANSAKCJA"
    : payload?.decision === "TRADE";

  const scoreRows = (payload) => {
    const scores = payload?.selection?.scores || {};
    return ["catalyst", "relative_momentum", "volume_liquidity", "market_context", "risk_reward", "historical_expectancy"]
      .filter((key) => Number.isFinite(Number(scores[key])))
      .map((key) => `<div><span>${escapeHtml(T[key])}</span><b>${Number(scores[key]).toFixed(1)}/100</b></div>`)
      .join("");
  };

  const metricsRows = (payload) => {
    const m = payload?.metrics || {};
    const count = Number(m.resolved_trades || 0);
    if (!count) return "";
    const win = m.win_rate == null ? "—" : `${Math.round(Number(m.win_rate) * 100)}%`;
    const avg = m.average_return_percent == null ? "—" : `${Number(m.average_return_percent).toFixed(2)}%`;
    const r = m.average_r == null ? "—" : `${Number(m.average_r).toFixed(2)}R`;
    return `<section><h4>${escapeHtml(T.metrics)}</h4><div class="dsm-detail-grid">
      <div><span>${escapeHtml(T.trades)}</span><b>${count}</b></div>
      <div><span>${escapeHtml(T.winRate)}</span><b>${win}</b></div>
      <div><span>${escapeHtml(T.avgReturn)}</span><b>${avg}</b></div>
      <div><span>${escapeHtml(T.avgR)}</span><b>${r}</b></div>
    </div></section>`;
  };

  const learningBlock = (payload, market) => {
    const learning = payload?.learning || {};
    const contract = payload?.methodology?.daily_stock_core?.learning || {};
    if (!learning.method && !contract.method) return "";
    const learned = Number(learning.resolved_trades || 0);
    const minimum = Number(learning.minimum_sample || 8);
    const active = Boolean(learning.adaptation_active);
    const state = active ? `${T.learningActive} · ${learned}` : `${T.collecting} · ${learned}/${minimum}`;
    const preservation = market === "gpw"
      ? (lang === "pl" ? "Dotychczasowa pamięć i event-learning GPW pozostają aktywne; wagi strategii są zamrożone." : "Existing GPW memory and event learning remain active; strategy weights stay frozen.")
      : (lang === "pl" ? "Pamięć USA jest liczona osobno od GPW i nie zmienia wag po pojedynczej transakcji." : "US memory is isolated from GPW and cannot mutate weights after a single trade.");
    return `<section><h4>${escapeHtml(T.learning)}</h4><p>${escapeHtml(state)}. ${escapeHtml(preservation)}</p></section>`;
  };

  const renderDetails = (payload, market) => {
    const rows = scoreRows(payload);
    const blocks = [];
    if (rows) blocks.push(`<section><h4>${escapeHtml(T.scoreBreakdown)}</h4><div class="dsm-detail-grid">${rows}</div></section>`);
    const metrics = metricsRows(payload);
    if (metrics) blocks.push(metrics);
    const learning = learningBlock(payload, market);
    if (learning) blocks.push(learning);
    return blocks.length ? `<details class="dsm-market-details"><summary>${escapeHtml(T.details)}</summary>${blocks.join("")}</details>` : "";
  };

  const renderMarket = (payload, market) => {
    const status = statusFor(payload, market);
    const title = market === "gpw" ? T.gpwTitle : T.usTitle;
    const marketName = market === "gpw" ? T.gpwMarket : T.usMarket;
    const timing = market === "gpw" ? T.gpwTime : T.usTime;
    const updated = payload?.generated_at ? `${T.updated}: ${formatTimestamp(payload.generated_at, market)}` : T.checking;
    const selection = payload?.selection || {};
    let body = "";

    if (isTrade(payload, market) && !status.stale && selection.ticker) {
      const sources = Array.isArray(selection.sources) ? selection.sources : [];
      const sourceHtml = sources.length ? `<ul class="dsm-sources">${sources.slice(0, 8).map((source) =>
        `<li><a href="${escapeHtml(safeUrl(source.url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(source.publisher || "Source")}</a>: ${escapeHtml(source.title || "")}</li>`
      ).join("")}</ul>` : "";
      const originalBadge = (market === "gpw" && lang === "en")
        ? `<span class="dsm-original-badge">${escapeHtml(T.originalPl)}</span>`
        : (market === "us" && lang === "pl") ? `<span class="dsm-original-badge">${escapeHtml(T.originalEn)}</span>` : "";
      body = `<div class="dsm-pick"><div class="dsm-symbol"><strong>${escapeHtml(selection.ticker)}</strong><span>${escapeHtml(selection.name || "")}</span></div><span class="dsm-score">${escapeHtml(T.score)} <b>${Number(selection.score || 0).toFixed(1)}</b>/100</span></div>
        <p class="dsm-thesis"><b>${escapeHtml(T.thesis)}:</b> ${originalBadge}${escapeHtml(selection.thesis || "")}</p>
        <p class="dsm-why"><b>${escapeHtml(T.why)}:</b> ${originalBadge}${escapeHtml(selection.why_now || "")}</p>
        <div class="dsm-levels">
          <div class="dsm-level"><small>${escapeHtml(T.entry)}</small><b>${money(selection.entry_zone?.[0], market)}–${money(selection.entry_zone?.[1], market)}</b></div>
          <div class="dsm-level stop"><small>${escapeHtml(T.stop)}</small><b>${money(selection.stop, market)}</b></div>
          <div class="dsm-level target"><small>${escapeHtml(T.target)}</small><b>${money(selection.target, market)}</b></div>
        </div>
        <p class="dsm-activation">${escapeHtml(selection.activation || "")} ${selection.valid_until ? `${escapeHtml(T.valid)}: ${escapeHtml(selection.valid_until)}.` : ""}</p>
        ${sourceHtml}`;
    } else {
      const reason = status.stale ? T.stale : (payload?.reason || T.noSelection);
      body = `<div class="dsm-empty"><strong>${escapeHtml(reason)}</strong></div>`;
    }

    return `<article class="dsm-market-card" data-dsm-market="${market}">
      <header class="dsm-market-head"><div class="dsm-market-title"><small>${escapeHtml(marketName)}</small><h3>${escapeHtml(title)}</h3><div class="dsm-market-meta">${escapeHtml(updated)}</div></div><span class="dsm-status ${status.cls}">${escapeHtml(status.text)}</span></header>
      ${body}
      ${renderDetails(payload, market)}
      <div class="dsm-footer"><span>${escapeHtml(timing)}</span></div>
    </article>`;
  };

  const outcomeLabel = (row) => {
    const o = row?.outcome || {};
    if (o.status !== "RESOLVED") return { text: T.current, cls: "" };
    if (!o.activated) return { text: T.notActivated, cls: "" };
    const reason = o.exit_reason === "target" ? T.targetHit : o.exit_reason === "stop" ? T.stopHit : T.horizon;
    const result = Number(o.return_percent || 0);
    const r = Number(o.r_multiple || 0);
    return {
      text: `${reason} · ${result >= 0 ? "+" : ""}${result.toFixed(2)}% · ${r.toFixed(2)}R`,
      cls: result > 0 ? "positive" : result < 0 ? "negative" : ""
    };
  };

  const historyMarket = (history, market) => {
    const block = history?.markets?.[market] || {};
    const rows = Array.isArray(block.trades) ? block.trades.slice(0, 12) : [];
    const title = market === "gpw" ? T.historyGpw : T.historyUs;
    const summary = `${Number(block.selected_trades || rows.length)} ${T.selected} · ${Number(block.resolved_trades || 0)} ${T.resolved}`;
    if (!rows.length) return `<section class="dsm-history-market"><h4>${escapeHtml(title)}</h4><p class="dsm-history-summary">${escapeHtml(summary)}</p></section>`;
    return `<section class="dsm-history-market"><h4>${escapeHtml(title)}</h4><p class="dsm-history-summary">${escapeHtml(summary)}</p>${rows.map((row) => {
      const result = outcomeLabel(row);
      return `<div class="dsm-history-row"><span>${escapeHtml(row.date || "—")}</span><b>${escapeHtml(row.ticker || row.symbol || "—")}</b><span class="dsm-history-name">${escapeHtml(row.name || row.sector || "")}</span><span class="dsm-history-result ${result.cls}">${escapeHtml(result.text)}</span></div>`;
    }).join("")}</section>`;
  };

  const renderHistory = (history) => `<details class="dsm-history"><summary>${escapeHtml(T.history)}</summary><div class="dsm-history-body">${historyMarket(history, "gpw")}${historyMarket(history, "us")}</div></details>`;

  const mergeHistory = (gpwIndex, usIndex, fallback) => ({
    schema_version: "daily-stock-history-ui-v1",
    markets: {
      gpw: gpwIndex || fallback?.markets?.gpw || { selected_trades: 0, resolved_trades: 0, trades: [] },
      us: usIndex || fallback?.markets?.us || { selected_trades: 0, resolved_trades: 0, trades: [] }
    }
  });

  const shell = () => {
    root.className = "dash-card page-card dsm-root";
    root.removeAttribute("aria-labelledby");
    root.innerHTML = `<header class="dsm-head"><div><span class="dsm-kicker">${escapeHtml(T.kicker)}</span><h2>${escapeHtml(T.title)}</h2><p>${escapeHtml(T.intro)}</p></div><span class="dsm-common-chip">${escapeHtml(T.core)}</span></header><div class="dsm-market-grid"><article class="dsm-market-card"><div class="dsm-empty">${escapeHtml(T.loading)}</div></article><article class="dsm-market-card"><div class="dsm-empty">${escapeHtml(T.loading)}</div></article></div><div data-dsm-history></div><p class="dsm-legal">${escapeHtml(T.legal)}</p><div class="dsm-footer"><span>${escapeHtml(T.coreFooter)}</span></div>`;
  };

  const fetchJson = async (url, required = true) => {
    try {
      const response = await fetch(`${url}?v=${Date.now()}`, { cache: "no-store", headers: { "Cache-Control": "no-cache" } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json();
    } catch (error) {
      if (required) throw error;
      return null;
    }
  };

  const load = async () => {
    shell();
    try {
      const [gpw, us, gpwIndex, usIndex, fallback] = await Promise.all([
        fetchJson(URLS.gpw),
        fetchJson(URLS.us),
        fetchJson(URLS.gpwHistory, false),
        fetchJson(URLS.usHistory, false),
        fetchJson(URLS.historyFallback, false)
      ]);
      root.querySelector(".dsm-market-grid").innerHTML = `${renderMarket(gpw, "gpw")}${renderMarket(us, "us")}`;
      const history = mergeHistory(gpwIndex, usIndex, fallback);
      root.querySelector("[data-dsm-history]").innerHTML = renderHistory(history);
    } catch (error) {
      root.querySelector(".dsm-market-grid").innerHTML = `<div class="dsm-error">${escapeHtml(lang === "pl" ? "Nie udało się załadować obu rynków. Bieżące dane są ponownie pobierane." : "Both market feeds could not be loaded. Current data will be retried automatically.")}</div>`;
    }
  };

  load();
})();
