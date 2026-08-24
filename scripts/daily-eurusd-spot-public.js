(() => {
  "use strict";
  const root = document.getElementById("eurusd-daily-root");
  if (!root) return;

  const isEn = document.documentElement.lang.toLowerCase().startsWith("en");
  const T = isEn ? {
    engine: "Daily Engine · WITHOUT Belief",
    monitoring: "MONITORING",
    open: "POSITION OPEN",
    closedSl: "STOP LOSS HIT",
    closedTp: "TARGET HIT",
    closedTime: "POSITION CLOSED",
    noTrade: "NO POSITION",
    entry: "Entry", stop: "SL", target: "TP", mark: "Current", horizon: "Max horizon",
    result: "Result", r: "R multiple", exit: "Exit", opened: "Opened", closed: "Closed",
    history: "EUR/USD history", noHistory: "No closed EUR/USD positions yet.",
    learning: "Method and outcome learning",
    learningStats: "Learning state",
    trades: "closed", winRate: "win rate", avgR: "avg R", streak: "loss streak",
    thresholds: "Adaptive entry thresholds",
    candidate: "Current candidate",
    rejected: "Entry rejected by gates",
    gate: {
      raw_score_neutral: "score is neutral",
      score_below_adaptive_long_threshold: "LONG score below adaptive threshold",
      score_above_adaptive_short_threshold: "SHORT score above adaptive threshold",
      confidence_below_minimum: "decision strength too low",
      signal_not_persistent: "signal did not persist across two 30m observations",
      entry_overextended_vs_ema20: "price too extended from EMA20",
      latest_30m_bar_is_shock_bar: "latest 30m bar is too volatile",
      post_stop_cooldown: "post-stop cooldown active",
      daily_entry_limit_reached: "daily entry limit reached",
      entry_disabled_this_cycle: "new entry disabled in the closing cycle"
    },
    reasons: "Why no entry",
    components: "Components",
    weights: "adaptive weights",
    error: "Could not load Daily EUR/USD state."
  } : {
    engine: "Daily Engine · WITHOUT Belief",
    monitoring: "MONITORING",
    open: "POZYCJA OTWARTA",
    closedSl: "STOP LOSS OSIĄGNIĘTY",
    closedTp: "CEL OSIĄGNIĘTY",
    closedTime: "ZAMKNIĘCIE POZYCJI",
    noTrade: "BRAK POZYCJI",
    entry: "Wejście", stop: "SL", target: "TP", mark: "Cena teraz", horizon: "Maks. horyzont",
    result: "Wynik", r: "Wynik R", exit: "Wyjście", opened: "Otwarcie", closed: "Zamknięcie",
    history: "Historia EUR/USD", noHistory: "Brak zamkniętych pozycji EUR/USD.",
    learning: "Metoda i uczenie z wyników",
    learningStats: "Stan uczenia",
    trades: "zamkniętych", winRate: "skuteczność", avgR: "śr. R", streak: "seria strat",
    thresholds: "Adaptacyjne progi wejścia",
    candidate: "Bieżący kandydat",
    rejected: "Wejście odrzucone przez filtry",
    gate: {
      raw_score_neutral: "score jest neutralny",
      score_below_adaptive_long_threshold: "score LONG poniżej adaptacyjnego progu",
      score_above_adaptive_short_threshold: "score SHORT powyżej adaptacyjnego progu",
      confidence_below_minimum: "zbyt mała siła decyzji",
      signal_not_persistent: "sygnał nie utrzymał się w dwóch obserwacjach 30m",
      entry_overextended_vs_ema20: "cena zbyt daleko od EMA20",
      latest_30m_bar_is_shock_bar: "ostatnia świeca 30m ma zbyt duży zakres",
      post_stop_cooldown: "aktywny cooldown po SL",
      daily_entry_limit_reached: "osiągnięty dzienny limit wejść",
      entry_disabled_this_cycle: "nowe wejście wyłączone w cyklu zamykającym pozycję"
    },
    reasons: "Dlaczego bez wejścia",
    components: "Komponenty",
    weights: "wagi po uczeniu",
    error: "Nie udało się wczytać stanu Daily EUR/USD."
  };

  const esc = value => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const px = value => Number.isFinite(Number(value))
    ? Number(value).toLocaleString(isEn ? "en-US" : "pl-PL", {minimumFractionDigits:5, maximumFractionDigits:5})
    : "—";
  const pct = value => Number.isFinite(Number(value))
    ? `${Number(value) > 0 ? "+" : ""}${Number(value).toLocaleString(isEn ? "en-US" : "pl-PL", {minimumFractionDigits:2, maximumFractionDigits:2})}%`
    : "—";
  const num = (value, digits=2) => Number.isFinite(Number(value))
    ? Number(value).toLocaleString(isEn ? "en-US" : "pl-PL", {minimumFractionDigits:digits, maximumFractionDigits:digits})
    : "—";
  const date = value => {
    if (!value) return "—";
    const d = new Date(value);
    return Number.isNaN(d.valueOf()) ? esc(value) : d.toLocaleString(isEn ? "en-GB" : "pl-PL", {dateStyle:"short", timeStyle:"short"});
  };

  const statusLabel = status => ({
    OPEN: T.open, CLOSED_SL: T.closedSl, CLOSED_TP: T.closedTp, CLOSED_TIME: T.closedTime,
    NO_TRADE: T.noTrade, SIGNAL: T.open
  }[String(status || "").toUpperCase()] || String(status || T.noTrade));

  function historyRows(trades) {
    if (!trades.length) return `<p class="brfx-muted">${esc(T.noHistory)}</p>`;
    return `<div class="brfx-history-wrap"><table class="brfx-history"><thead><tr>
      <th>${esc(T.opened)}</th><th>Side</th><th>${esc(T.entry)}</th><th>${esc(T.exit)}</th><th>${esc(T.result)}</th><th>${esc(T.r)}</th><th>Status</th>
    </tr></thead><tbody>${[...trades].reverse().slice(0, 10).map(trade => `<tr>
      <td>${esc(date(trade.opened_at))}</td><td><b>${esc(trade.direction || "—")}</b></td><td>${esc(px(trade.entry))}</td><td>${esc(px(trade.exit_price))}</td>
      <td class="${Number(trade.result_percent) >= 0 ? "positive" : "negative"}"><b>${esc(pct(trade.result_percent))}</b></td>
      <td>${esc(num(trade.r_multiple, 2))}R</td><td>${esc(statusLabel(trade.exit_reason === "STOP_LOSS" ? "CLOSED_SL" : trade.exit_reason === "TAKE_PROFIT" ? "CLOSED_TP" : "CLOSED_TIME"))}</td>
    </tr>`).join("")}</tbody></table></div>`;
  }

  function render(payload, historyPayload) {
    const md = payload.metadata || {};
    const position = md.position && md.position.status === "OPEN" ? md.position : null;
    const lastTrade = md.last_trade || null;
    const candidate = md.candidate || {};
    const learning = historyPayload?.learning_state || md.learning || {};
    const trades = Array.isArray(historyPayload?.trades) ? historyPayload.trades : (lastTrade ? [lastTrade] : []);
    const components = md.components || {};
    const weights = md.weights || learning.adaptive_weights || {};
    const status = String(payload.status || "NO_TRADE").toUpperCase();

    let main = "";
    if (position) {
      main = `<div class="brfx-signal"><strong>${esc(position.direction)}</strong><span>${esc(T.open)} · score ${num(position.entry_score,1)}/100 · confidence ${Math.round(Number(position.entry_confidence || 0)*100)}%</span></div>
        <div class="brfx-plan brfx-plan-four"><div><span>${esc(T.entry)}</span><b>${px(position.entry)}</b></div><div><span>${esc(T.stop)}</span><b>${px(position.stop)}</b></div><div><span>${esc(T.target)}</span><b>${px(position.target)}</b></div><div><span>${esc(T.mark)}</span><b>${px(position.mark_price)}</b><small class="${Number(position.unrealized_percent) >= 0 ? "positive" : "negative"}">${pct(position.unrealized_percent)}</small></div></div>
        <p class="brfx-foot">${esc(T.opened)}: ${esc(date(position.opened_at))} · ${esc(T.horizon)}: ${esc(date(position.expires_at))}</p>`;
    } else if (lastTrade) {
      main = `<div class="brfx-signal"><strong class="${Number(lastTrade.result_percent) >= 0 ? "positive" : "negative"}">${esc(statusLabel(status))}</strong><span>${esc(lastTrade.direction)} · ${esc(T.result)} ${esc(pct(lastTrade.result_percent))} · ${esc(T.r)} ${esc(num(lastTrade.r_multiple,2))}R</span></div>
        <div class="brfx-plan"><div><span>${esc(T.entry)}</span><b>${px(lastTrade.entry)}</b></div><div><span>${esc(T.exit)}</span><b>${px(lastTrade.exit_price)}</b></div><div><span>${esc(T.closed)}</span><b class="brfx-small-value">${esc(date(lastTrade.closed_at))}</b></div></div>`;
    } else {
      const reasons = (candidate.gate_reasons || []).map(reason => T.gate[reason] || reason);
      main = `<div class="brfx-signal"><strong>${esc(T.noTrade)}</strong><span>${esc(T.candidate)}: ${esc(candidate.direction || "FLAT")} · score ${num(candidate.score ?? payload.score,1)}/100 · confidence ${Math.round(Number(candidate.confidence ?? payload.confidence ?? 0)*100)}%</span></div>
        ${reasons.length ? `<div class="brfx-gates"><b>${esc(T.rejected)}</b><ul>${reasons.map(reason => `<li>${esc(reason)}</li>`).join("")}</ul></div>` : ""}`;
    }

    const thresholds = learning.entry_thresholds || {};
    const winRate = learning.win_rate_percent == null ? "—" : `${num(learning.win_rate_percent,1)}%`;
    root.innerHTML = `<article class="brfx-card">
      <div class="brfx-head"><div><small>${esc(T.engine)}</small><h3>EUR/USD Spot</h3></div><span class="brfx-stage">${esc(T.monitoring)}</span></div>
      ${main}
      <details><summary>${esc(T.learning)}</summary><div class="brfx-details">
        <p>${esc(T.components)}: trend <b>${num(components.trend,3)}</b> · USD <b>${num(components.broad_usd_environment,3)}</b> · rates <b>${num(components.us_rates_pressure_proxy,3)}</b>.</p>
        <p>${esc(T.weights)}: trend <b>${num((weights.trend || 0)*100,1)}%</b> · USD <b>${num((weights.broad_usd_environment || 0)*100,1)}%</b> · rates <b>${num((weights.us_rates_pressure_proxy || 0)*100,1)}%</b>.</p>
        <p>${esc(T.learningStats)}: ${Number(learning.total_closed || 0)} ${esc(T.trades)} · ${esc(T.winRate)} ${esc(winRate)} · ${esc(T.avgR)} ${esc(num(learning.average_r,2))} · ${esc(T.streak)} ${Number(learning.consecutive_losses || 0)}.</p>
        <p>${esc(T.thresholds)}: LONG ≥ <b>${esc(num(thresholds.long,1))}</b> · SHORT ≤ <b>${esc(num(thresholds.short,1))}</b> · confidence ≥ <b>${thresholds.min_confidence == null ? "—" : Math.round(Number(thresholds.min_confidence)*100)+"%"}</b>.</p>
      </div></details>
      <details class="brfx-history-details" ${trades.length ? "open" : ""}><summary>${esc(T.history)}</summary>${historyRows(trades)}</details>
      <p class="brfx-foot">${esc(payload.engine_version || "")} · ${esc(date(payload.timestamp))}</p>
    </article>`;
  }

  const style = document.createElement("style");
  style.textContent = `.brfx-card{padding:18px;border:1px solid rgba(255,191,63,.28);border-radius:18px;background:linear-gradient(135deg,rgba(255,191,63,.08),rgba(255,255,255,.025));color:#eef7ff}.brfx-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}.brfx-head small{display:block;color:#8fa3b8;font-size:9px;font-weight:900;text-transform:uppercase;letter-spacing:.06em}.brfx-head h3{margin:5px 0 0;font-size:26px;letter-spacing:-.03em}.brfx-stage{padding:6px 9px;border-radius:999px;background:rgba(59,223,163,.12);color:#8ff0cc;font-size:10px;font-weight:950}.brfx-signal{display:flex;align-items:baseline;gap:12px;margin:18px 0;flex-wrap:wrap}.brfx-signal strong{font-size:27px}.brfx-signal span,.brfx-muted,.brfx-foot,.brfx-details{color:#96aabe;font-size:11px;line-height:1.5}.brfx-plan{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:14px 0}.brfx-plan-four{grid-template-columns:repeat(4,minmax(0,1fr))}.brfx-plan div{padding:10px;border:1px solid rgba(255,255,255,.09);border-radius:12px;background:rgba(0,0,0,.16)}.brfx-plan span{display:block;color:#7f95aa;font-size:9px;text-transform:uppercase}.brfx-plan b{display:block;margin-top:4px;font-size:16px}.brfx-plan small{display:block;margin-top:3px;font-size:10px}.brfx-small-value{font-size:12px!important}.brfx-card details{margin-top:14px}.brfx-card summary{cursor:pointer;color:#9ffff6;font-size:11px;font-weight:850}.brfx-foot{margin:14px 0 0}.brfx-gates{padding:12px;border:1px solid rgba(255,191,63,.18);border-radius:12px;background:rgba(255,191,63,.05);font-size:11px;color:#c7d4e1}.brfx-gates ul{margin:7px 0 0;padding-left:18px}.brfx-history-wrap{overflow:auto;margin-top:10px}.brfx-history{width:100%;border-collapse:collapse;font-size:10px}.brfx-history th,.brfx-history td{padding:8px 7px;border-bottom:1px solid rgba(255,255,255,.08);text-align:left;white-space:nowrap}.brfx-history th{color:#7f95aa;text-transform:uppercase;font-size:8px}.positive{color:#65e6a4!important}.negative{color:#ff8ca3!important}.brfx-error{padding:16px;border:1px solid rgba(255,77,109,.25);border-radius:14px;color:#ffafbd;background:rgba(255,77,109,.06)}@media(max-width:760px){.brfx-plan,.brfx-plan-four{grid-template-columns:1fr 1fr}}@media(max-width:480px){.brfx-plan,.brfx-plan-four{grid-template-columns:1fr}.brfx-signal{align-items:flex-start;flex-direction:column;gap:3px}}`;
  document.head.appendChild(style);

  Promise.all([
    fetch("/data/investments/eurusd_daily_spot.json?v=" + Date.now(), {cache:"no-store"}).then(response => { if (!response.ok) throw new Error("feed"); return response.json(); }),
    fetch("/data/investments/eurusd_daily_history.json?v=" + Date.now(), {cache:"no-store"}).then(response => response.ok ? response.json() : null).catch(() => null)
  ]).then(([payload, history]) => render(payload, history))
    .catch(() => { root.innerHTML = `<div class="brfx-error">${esc(T.error)}</div>`; });
})();
