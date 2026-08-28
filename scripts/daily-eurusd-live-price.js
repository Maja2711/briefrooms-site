(() => {
  "use strict";

  const root = document.getElementById("eurusd-daily-root");
  if (!root) return;

  const isEn = document.documentElement.lang.toLowerCase().startsWith("en");
  const STATE_URL = "/data/investments/eurusd_daily_spot.json";
  const LIVE_URL = "https://www.currencyexchangetool.com/api/v1/convert?amount=1&from=EUR&to=USD";
  const REFRESH_MS = 60_000;
  const LIVE_MAX_AGE_MS = 5 * 60_000;

  const T = isEn ? {
    live: "Current",
    engine: "Last engine price",
    sourceLive: "live mid-market",
    sourceEngine: "engine snapshot",
    stale: "stale",
    updated: "updated"
  } : {
    live: "Cena teraz",
    engine: "Ostatnia cena silnika",
    sourceLive: "live mid-market",
    sourceEngine: "snapshot silnika",
    stale: "nieaktualne",
    updated: "aktualizacja"
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
    isEn ? "en-US" : "pl-PL",
    { minimumFractionDigits: 5, maximumFractionDigits: 5 }
  );

  const formatPct = value => {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    return `${n > 0 ? "+" : ""}${n.toLocaleString(isEn ? "en-US" : "pl-PL", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    })}%`;
  };

  const formatTime = value => {
    const d = new Date(value);
    if (Number.isNaN(d.valueOf())) return "—";
    return d.toLocaleTimeString(isEn ? "en-GB" : "pl-PL", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit"
    });
  };

  const ageMinutes = value => {
    const d = new Date(value);
    if (Number.isNaN(d.valueOf())) return null;
    return Math.max(0, Math.round((Date.now() - d.valueOf()) / 60_000));
  };

  async function fetchState() {
    const response = await fetch(`${STATE_URL}?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error("eurusd_state_unavailable");
    return response.json();
  }

  async function fetchLiveQuote() {
    const response = await fetch(`${LIVE_URL}&_=${Date.now()}`, {
      cache: "no-store",
      mode: "cors"
    });
    if (!response.ok) throw new Error(`live_quote_http_${response.status}`);
    const data = await response.json();
    if (!data || data.success === false) throw new Error("live_quote_api_error");

    const rate = number(data.rate ?? data.result);
    if (rate == null || rate < 0.8 || rate > 1.5) throw new Error("live_quote_invalid_rate");

    const sourceTime = data.updatedAt ? new Date(data.updatedAt) : new Date();
    if (Number.isNaN(sourceTime.valueOf())) throw new Error("live_quote_invalid_timestamp");
    const age = Date.now() - sourceTime.valueOf();
    if (age < -60_000 || age > LIVE_MAX_AGE_MS) throw new Error("live_quote_stale");

    return {
      price: rate,
      updatedAt: sourceTime.toISOString(),
      source: "Currency Exchange Tool"
    };
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
      meta.textContent = `${T.sourceLive} · ${T.updated} ${formatTime(liveQuote.updatedAt)}`;
      meta.classList.remove("brfx-live-stale");
      card.dataset.livePriceSource = "browser-live";
      card.dataset.livePriceAt = liveQuote.updatedAt;
    } else {
      const minutes = ageMinutes(payload?.timestamp);
      const ageText = minutes == null ? "" : ` · ${minutes} min`;
      meta.textContent = `${T.sourceEngine}${ageText}${minutes != null && minutes >= 10 ? ` · ${T.stale}` : ""}`;
      meta.classList.toggle("brfx-live-stale", minutes != null && minutes >= 10);
      card.dataset.livePriceSource = "engine-fallback";
      card.dataset.livePriceAt = String(payload?.timestamp || "");
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

  setTimeout(refresh, 1200);
  const timer = window.setInterval(refresh, REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refresh();
  });
  window.addEventListener("pagehide", () => window.clearInterval(timer), { once: true });
})();
