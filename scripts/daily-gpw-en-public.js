(() => {
  "use strict";
  const root = document.getElementById("gpw-pol-daily-root");
  if (!root) return;

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (m) => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"
  }[m]));

  const money = (v) => Number.isFinite(Number(v))
    ? Number(v).toLocaleString("en-US",{style:"currency",currency:"PLN",minimumFractionDigits:2,maximumFractionDigits:2})
    : "—";

  const stamp = (v) => {
    if (!v) return "—";
    try {
      return new Intl.DateTimeFormat("en-GB",{timeZone:"Europe/Warsaw",day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"}).format(new Date(v));
    } catch (_) { return "—"; }
  };

  const englishFallback = (s) => {
    const name = String(s?.name || s?.ticker || s?.symbol || "The selected stock");
    return {
      thesis: `${name} is the active GPW Daily Trade selection after the highest validated ranking among eligible Polish-market candidates.`,
      why_now: "The selection combines relative momentum, market and sector context, liquidity, risk/reward, current-session confirmation and the strategy's historical evidence.",
      activation: "Enter only inside the stated entry zone and do not chase the price above its upper limit."
    };
  };

  const render = (p) => {
    const decision = String(p?.decision || "");
    const isTrade = decision === "TRANSAKCJA" && p?.selection;
    const status = isTrade ? ["TRADE","trade"] : decision === "BRAK_TRANSAKCJI" ? ["NO TRADE","no-trade"] : ["ANALYSIS / WAITING","pending"];
    const s = p?.selection || {};
    let body;
    if (isTrade) {
      const ticker = s.ticker || s.symbol || "—";
      const entry = Array.isArray(s.entry_zone) ? `${money(s.entry_zone[0])}–${money(s.entry_zone[1])}` : money(s.reference_price);
      const fallback = englishFallback(s);
      const loc = s?.localized?.en || fallback;
      const thesis = String(loc.thesis || fallback.thesis);
      const why = String(loc.why_now || fallback.why_now);
      const activation = String(loc.activation || fallback.activation);
      body = `<div class="dsm-pick"><div class="dsm-symbol"><strong>${esc(ticker)}</strong><span>${esc(s.name||"")}</span></div><span class="dsm-score">Score <b>${Number(s.score||0).toFixed(1)}</b>/100</span></div>
      <p class="dsm-thesis"><b>1–2 session thesis:</b> ${esc(thesis)}</p>
      <p class="dsm-why"><b>Why now:</b> ${esc(why)}</p>
      <div class="dsm-levels"><div class="dsm-level"><small>Entry zone</small><b>${entry}</b></div><div class="dsm-level stop"><small>Stop</small><b>${money(s.stop)}</b></div><div class="dsm-level target"><small>Target</small><b>${money(s.target)}</b></div></div>
      <p class="dsm-activation">${esc(activation)}</p>`;
    } else {
      const reason = decision === "BRAK_TRANSAKCJI"
        ? "Market data is complete, but no stock passed the liquidity and risk screening."
        : "No validated GPW Poland selection for this session.";
      body = `<div class="dsm-empty"><strong>${reason}</strong></div>`;
    }
    root.className = "dash-card page-card dsm-root";
    root.innerHTML = `<header class="dsm-head"><div><span class="dsm-kicker">BriefRooms Research · Daily Stock Core</span><h2>DAILY GPW POLAND</h2><p>Polish-market adapter with PLN, Warsaw session timing, ESPI/EBI evidence and market memory isolated from the US engine.</p></div><span class="dsm-common-chip">SHARED CORE · GPW MEMORY ISOLATED</span></header>
    <div class="dsm-market-grid" style="grid-template-columns:1fr"><article class="dsm-market-card" data-dsm-market="gpw"><header class="dsm-market-head"><div class="dsm-market-title"><small>Polish market</small><h3>GPW DAILY TRADE</h3><div class="dsm-market-meta">Updated: ${esc(stamp(p?.generated_at))}</div></div><span class="dsm-status ${status[1]}">${status[0]}</span></header>${body}<div class="dsm-footer"><span>GPW · PLN · confirmation from 09:05 Warsaw · ESPI/EBI</span></div></article></div>
    <p class="dsm-legal">Research module. Not investment advice.</p>`;
  };

  fetch(`/data/investments/gpw_daily_pick.json?v=${Date.now()}`,{cache:"no-store",headers:{"Cache-Control":"no-cache"}})
    .then((r)=>{if(!r.ok) throw new Error(`HTTP ${r.status}`);return r.json();})
    .then(render)
    .catch(()=>{root.innerHTML='<div class="dsm-error">The GPW Poland feed could not be loaded. Current data will be retried automatically.</div>';});
})();
