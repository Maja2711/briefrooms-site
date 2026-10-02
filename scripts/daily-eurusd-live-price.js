(() => {
  "use strict";

  const root = document.getElementById("eurusd-daily-root");
  if (!root) return;

  const isEn = document.documentElement.lang.toLowerCase().startsWith("en");
  const STATE_URL = "/data/investments/eurusd_daily_spot.json";
  const NOTIFICATION_CONFIG_URL = "/data/notifications/trading-notification-config.json";
  const BACKEND_LIVE_URL = "/data/investments/live_prices.json";
  const REFRESH_MS = 15_000;
  const LIVE_MAX_AGE_MS = 2 * 60_000;
  const REQUEST_TIMEOUT_MS = 3_000;

  const T = isEn ? {
    live: "Current", engine: "Last engine price", refreshing: "refreshing", sourceLive: "live mid-market",
    sourceEngine: "engine snapshot", stale: "stale", updated: "updated"
  } : {
    live: "Cena teraz", engine: "Ostatnia cena silnika", refreshing: "odświeżanie", sourceLive: "live mid-market",
    sourceEngine: "snapshot silnika", stale: "nieaktualne", updated: "aktualizacja"
  };

  let knownTradeId = null;
  let knownStatus = null;
  let initialized = false;
  let inFlight = false;

  const number = value => {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  };

  const formatPx = value => Number(value).toLocaleString(
    isEn ? "en-US" : "pl-PL", { minimumFractionDigits: 5, maximumFractionDigits: 5 }
  );

  const formatPct = value => {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    return `${n > 0 ? "+" : ""}${n.toLocaleString(isEn ? "en-US" : "pl-PL", {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    })}%`;
  };

  const formatDateTime = value => {
    const d = new Date(value);
    if (Number.isNaN(d.valueOf())) return "—";
    return d.toLocaleString(isEn ? "en-GB" : "pl-PL", { dateStyle: "short", timeStyle: "medium" });
  };

  const ageMinutes = value => {
    const d = new Date(value);
    if (Number.isNaN(d.valueOf())) return null;
    return Math.max(0, Math.round((Date.now() - d.valueOf()) / 60_000));
  };

  async function fetchJson(url) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(url, { cache: "no-store", mode: "cors", signal: controller.signal });
      if (!response.ok) throw new Error(`http_${response.status}`);
      return await response.json();
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async function fetchState() {
    const response = await fetch(`${STATE_URL}?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error("eurusd_state_unavailable");
    return response.json();
  }

  function validateQuote(price, timestamp, source) {
    const rate = number(price);
    if (rate == null || rate < 0.8 || rate > 1.5) throw new Error(`${source}_invalid_rate`);
    const sourceTime = timestamp ? new Date(timestamp) : new Date();
    if (Number.isNaN(sourceTime.valueOf())) throw new Error(`${source}_invalid_timestamp`);
    const age = Date.now() - sourceTime.valueOf();
    if (age < -60_000 || age > LIVE_MAX_AGE_MS) throw new Error(`${source}_stale`);
    return { price: rate, updatedAt: sourceTime.toISOString(), source };
  }


  async function quoteBackendLive() {
    const data = await fetchJson(`${BACKEND_LIVE_URL}?_=${Date.now()}`);
    const row = data?.prices?.eurusd;
    if (!row) throw new Error("backend_live_eurusd_missing");
    return validateQuote(
      row.price,
      row.current_price_updated_at || row.timestamp,
      `BriefRooms backend · ${row.source || "live_prices.json"}`
    );
  }
  async function quoteStooq() {
    const cfg = await fetchJson(`${NOTIFICATION_CONFIG_URL}?_=${Date.now()}`);
    const base = cfg?.background_push?.api_base || cfg?.analytics?.api_base;
    if (!base) throw new Error("stooq_worker_base_missing");
    const data = await fetchJson(`${String(base).replace(/\/$/, "")}/market/eurusd?_=${Date.now()}`);
    if (!data?.ok) throw new Error(data?.error || "stooq_worker_quote_error");
    return validateQuote(data.price, data.fetched_at, "Stooq");
  }

  async function quoteFxApi() {
    const data = await fetchJson(`https://fxapi.app/api/EUR/USD.json?_=${Date.now()}`);
    return validateQuote(data?.rate, data?.timestamp, "fxapi.app");
  }

  async function quoteYahoo(route) {
    const upstream = `https://query1.finance.yahoo.com/v8/finance/chart/EURUSD%3DX?interval=1m&range=1d&_=${Date.now()}`;
    const url = route === "allorigins"
      ? `https://api.allorigins.win/raw?url=${encodeURIComponent(upstream)}`
      : `https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(upstream)}`;
    const data = await fetchJson(url);
    const chart = data?.chart?.result?.[0];
    if (!chart) throw new Error("yahoo_missing_chart");
    const timestamps = Array.isArray(chart.timestamp) ? chart.timestamp : [];
    const closes = chart?.indicators?.quote?.[0]?.close || [];
    for (let i = Math.min(timestamps.length, closes.length) - 1; i >= 0; i -= 1) {
      const ts = number(timestamps[i]);
      const price = number(closes[i]);
      if (ts == null || price == null) continue;
      return validateQuote(price, new Date(ts * 1000).toISOString(), route === "allorigins" ? "Yahoo EURUSD=X · backup" : "Yahoo EURUSD=X");
    }
    throw new Error("yahoo_missing_quote");
  }

  async function quoteCurrencyExchangeTool() {
    const data = await fetchJson(`https://www.currencyexchangetool.com/api/v1/convert?amount=1&from=EUR&to=USD&_=${Date.now()}`);
    if (!data || data.success === false) throw new Error("currencyexchangetool_api_error");
    return validateQuote(data.rate ?? data.result, data.updatedAt, "Currency Exchange Tool");
  }

  function sourceFamily(source) {
    const s = String(source || "").toLowerCase();
    if (s.includes("stooq")) return "stooq";
    if (s.includes("yahoo")) return "yahoo";
    if (s.includes("fxapi")) return "fxapi";
    if (s.includes("currency exchange")) return "currency";
    return s || "unknown";
  }

  function chooseConsensusQuote(quotes) {
    const byFamily = new Map();
    for (const quote of quotes) {
      const family = sourceFamily(quote.source);
      const prev = byFamily.get(family);
      if (!prev || new Date(quote.updatedAt).valueOf() > new Date(prev.updatedAt).valueOf()) byFamily.set(family, quote);
    }
    const distinct = [...byFamily.values()];
    if (!distinct.length) return null;
    if (distinct.length === 1) return distinct[0];

    const prices = distinct.map(q => Number(q.price)).sort((a,b)=>a-b);
    const median = prices.length % 2 ? prices[(prices.length-1)/2] : (prices[prices.length/2-1] + prices[prices.length/2]) / 2;
    const inliers = distinct.filter(q => Math.abs(Number(q.price) - median) / 0.0001 <= 1.5);
    const pool = inliers.length >= 2 ? inliers : distinct;
    const backend = pool.find(q => String(q.source).startsWith("BriefRooms backend"));
    if (inliers.length < 2 && backend) return backend;
    return pool.sort((a,b)=>new Date(b.updatedAt).valueOf()-new Date(a.updatedAt).valueOf())[0];
  }

  async function fetchLiveQuote() {
    const providers = [
      ["BriefRooms backend", quoteBackendLive],
      ["Stooq", quoteStooq],
      ["Yahoo", () => quoteYahoo("codetabs")],
      ["Yahoo backup", () => quoteYahoo("allorigins")],
      ["fxapi.app", quoteFxApi],
      ["Currency Exchange Tool", quoteCurrencyExchangeTool],
    ];
    const settled = await Promise.allSettled(providers.map(async ([name, provider]) => {
      try {
        return await provider();
      } catch (error) {
        throw new Error(`${name}:${error?.message || String(error)}`);
      }
    }));
    const quotes = settled
      .filter(result => result.status === "fulfilled")
      .map(result => result.value)
      .sort((a, b) => new Date(b.updatedAt).valueOf() - new Date(a.updatedAt).valueOf());
    if (quotes.length) return chooseConsensusQuote(quotes);
    const errors = settled.filter(result => result.status === "rejected").map(result => result.reason?.message || String(result.reason));
    throw new Error(`all_live_providers_failed:${errors.join("|")}`);
  }

  function getOpenPosition(payload) {
    const position = payload?.metadata?.position;
    return position && position.status === "OPEN" ? position : null;
  }

  function pnlPercent(position, mark) {
    const entry = number(position?.entry);
    if (entry == null || entry <= 0 || mark == null) return null;
    const sign = String(position.direction).toUpperCase() === "SHORT" ? -1 : 1;
    return sign * ((mark - entry) / entry) * 100;
  }

  function rMultiple(position, mark) {
    const entry = number(position?.entry);
    const stop = number(position?.stop);
    if (entry == null || stop == null || mark == null) return null;
    const risk = Math.abs(entry - stop);
    if (risk <= 0) return null;
    const sign = String(position.direction).toUpperCase() === "SHORT" ? -1 : 1;
    return sign * (mark - entry) / risk;
  }

  function ensureLiveMeta(priceCell) {
    let meta = priceCell.querySelector(".brfx-live-price-meta");
    if (!meta) {
      meta = document.createElement("small");
      meta.className = "brfx-live-price-meta brfx-live-meta";
      priceCell.appendChild(meta);
    }
    return meta;
  }

  function updateCard(position, payload, liveQuote) {
    const card = root.querySelector(".brfx-card");
    const priceCell = root.querySelector(".brfx-plan-four > div:nth-child(4)");
    if (!card || !priceCell || !position) return;

    const label = priceCell.querySelector("span");
    const value = priceCell.querySelector("b");
    const pnl = priceCell.querySelector("small:not(.brfx-live-price-meta)");
    if (!label || !value || !pnl) return;

    const engineMark = number(position.mark_price);
    const usingLive = Boolean(liveQuote && number(liveQuote.price) != null);
    const mark = usingLive ? number(liveQuote.price) : engineMark;
    if (mark == null) return;

    const resultPct = pnlPercent(position, mark);
    const resultR = rMultiple(position, mark);
    const meta = ensureLiveMeta(priceCell);

    label.textContent = usingLive ? T.live : T.engine;
    value.textContent = formatPx(mark);
    pnl.textContent = `${formatPct(resultPct)}${resultR == null ? "" : ` · ${resultR >= 0 ? "+" : ""}${resultR.toFixed(2)}R`}`;
    pnl.classList.toggle("positive", Number(resultPct) >= 0);
    pnl.classList.toggle("negative", Number(resultPct) < 0);

    if (usingLive) {
      meta.textContent = `${T.sourceLive} · ${liveQuote.source} · ${T.updated} ${formatDateTime(liveQuote.updatedAt)}`;
      meta.classList.remove("brfx-live-stale");
      card.dataset.livePriceSource = liveQuote.source;
      card.dataset.livePriceAt = liveQuote.updatedAt;
    } else {
      const timestamp = payload?.timestamp;
      const minutes = ageMinutes(timestamp);
      const ageText = minutes == null ? "" : ` · ${minutes} min`;
      const timeText = timestamp ? ` · ${formatDateTime(timestamp)}` : "";
      meta.textContent = `${T.sourceEngine}${timeText}${ageText}${minutes != null && minutes >= 10 ? ` · ${T.stale}` : ""}`;
      meta.classList.toggle("brfx-live-stale", minutes != null && minutes >= 10);
      card.dataset.livePriceSource = "engine-fallback";
      card.dataset.livePriceAt = String(timestamp || "");
    }
  }

  function maybeReloadForStateChange(payload, position) {
    const status = String(payload?.status || "");
    const tradeId = position?.trade_id || null;
    if (!initialized) {
      knownStatus = status;
      knownTradeId = tradeId;
      initialized = true;
      return false;
    }
    if (status !== knownStatus || tradeId !== knownTradeId) {
      window.location.reload();
      return true;
    }
    return false;
  }

  async function refresh() {
    if (inFlight) return;
    inFlight = true;
    try {
      const payload = await fetchState();
      const position = getOpenPosition(payload);
      if (maybeReloadForStateChange(payload, position)) return;
      if (!position) return;

      const priceCell = root.querySelector(".brfx-plan-four > div:nth-child(4)");
      const pendingLabel = priceCell?.querySelector("span");
      const pendingMeta = priceCell ? ensureLiveMeta(priceCell) : null;
      if (pendingLabel) pendingLabel.textContent = `${T.live} · ${T.refreshing}`;
      if (pendingMeta) pendingMeta.textContent = T.refreshing;

      let liveQuote = null;
      try {
        liveQuote = await fetchLiveQuote();
      } catch (error) {
        console.warn("BriefRooms EUR/USD live quote fallback:", error?.message || error);
      }
      updateCard(position, payload, liveQuote);
    } catch (error) {
      console.warn("BriefRooms EUR/USD state refresh failed:", error?.message || error);
    } finally {
      inFlight = false;
    }
  }

  const style = document.createElement("style");
  style.textContent = `.brfx-live-meta{font-size:9px!important;color:#7f95aa!important;line-height:1.25;margin-top:4px}.brfx-live-stale{color:#ffb86b!important}`;
  document.head.appendChild(style);

  setTimeout(refresh, 250);
  const timer = window.setInterval(refresh, REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refresh();
  });
  window.addEventListener("pageshow", refresh);
  window.addEventListener("pagehide", () => window.clearInterval(timer), { once: true });
})();
