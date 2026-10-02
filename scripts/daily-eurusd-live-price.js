(() => {
  "use strict";

  const root = document.getElementById("eurusd-daily-root");
  if (!root) return;

  const isEn = document.documentElement.lang.toLowerCase().startsWith("en");
  const STATE_URL = "/data/investments/eurusd_daily_spot.json";
  const REFRESH_MS = 15_000;
  const LIVE_MAX_AGE_MS = 2 * 60_000;
  const REQUEST_TIMEOUT_MS = 2_500;

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


  async function fetchText(url) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(url, { cache: "no-store", mode: "cors", signal: controller.signal });
      if (!response.ok) throw new Error(`http_${response.status}`);
      return await response.text();
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function timeZoneOffsetMs(date, timeZone) {
    const formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone, hour12: false, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit"
    });
    const parts = Object.fromEntries(formatter.formatToParts(date)
      .filter(part => part.type !== "literal").map(part => [part.type, part.value]));
    return Date.UTC(Number(parts.year), Number(parts.month)-1, Number(parts.day),
      Number(parts.hour)%24, Number(parts.minute), Number(parts.second)) - date.getTime();
  }

  function warsawTimestamp(dateText, timeText) {
    const normalized = String(timeText || "").length === 5 ? `${timeText}:00` : String(timeText || "");
    const [y,m,d] = String(dateText || "").split("-").map(Number);
    const [hh,mm,ss] = normalized.split(":").map(Number);
    if (![y,m,d,hh,mm,ss].every(Number.isFinite)) throw new Error("stooq_invalid_timestamp");
    const wallUtc = Date.UTC(y,m-1,d,hh,mm,ss);
    const guess = new Date(wallUtc);
    const first = timeZoneOffsetMs(guess, "Europe/Warsaw");
    let instant = new Date(wallUtc-first);
    const corrected = timeZoneOffsetMs(instant, "Europe/Warsaw");
    if (corrected !== first) instant = new Date(wallUtc-corrected);
    return instant;
  }

  function parseStooqCsv(text, source) {
    const lines = String(text || "").trim().split(/\r?\n/).filter(Boolean);
    if (lines.length < 2) throw new Error("stooq_missing_row");
    const headers = lines[0].split(",").map(v => v.trim().toLowerCase());
    const row = lines[lines.length-1].split(",").map(v => v.trim());
    const at = name => {
      const i = headers.indexOf(name);
      return i >= 0 ? (row[i] || "") : "";
    };
    const bid = number(at("bid"));
    const ask = number(at("ask"));
    const close = number(at("close"));
    const price = bid != null && ask != null ? (bid + ask) / 2 : close;
    if (price == null) throw new Error("stooq_invalid_price");
    return validateQuote(price, warsawTimestamp(at("date"), at("time")).toISOString(), source);
  }

  async function quoteStooq(route) {
    const upstream = `https://stooq.com/q/l/?s=eurusd&f=sd2t2ohlcvba&h&e=csv&_=${Date.now()}`;
    const url = route === "allorigins"
      ? `https://api.allorigins.win/raw?url=${encodeURIComponent(upstream)}`
      : route === "codetabs"
        ? `https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(upstream)}`
        : upstream;
    return parseStooqCsv(await fetchText(url), route === "direct" ? "Stooq EURUSD" : `Stooq EURUSD · ${route}`);
  }

  async function quoteFxApi() {
    const data = await fetchJson(`https://fxapi.app/api/EUR/USD.json?_=${Date.now()}`);
    return validateQuote(data?.rate, data?.timestamp, "fxapi.app");
  }

  async function fetchLiveQuote() {
    const providers = [
      ["Stooq direct", () => quoteStooq("direct")],
      ["Stooq proxy 1", () => quoteStooq("codetabs")],
      ["Stooq proxy 2", () => quoteStooq("allorigins")],
      ["fxapi.app", quoteFxApi],
    ];
    const settled = await Promise.allSettled(providers.map(async ([name, provider]) => {
      try { return await provider(); }
      catch (error) { throw new Error(`${name}:${error?.message || String(error)}`); }
    }));
    const quotes = settled.filter(x => x.status === "fulfilled").map(x => x.value);
    const stooq = quotes.filter(q => String(q.source).startsWith("Stooq"))
      .sort((a,b)=>new Date(b.updatedAt)-new Date(a.updatedAt));
    if (stooq.length) return stooq[0];
    const fx = quotes.filter(q => q.source === "fxapi.app")
      .sort((a,b)=>new Date(b.updatedAt)-new Date(a.updatedAt));
    if (fx.length) return fx[0];
    const errors = settled.filter(x => x.status === "rejected").map(x => x.reason?.message || String(x.reason));
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
