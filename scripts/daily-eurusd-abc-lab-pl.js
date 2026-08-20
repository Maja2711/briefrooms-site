(() => {
  "use strict";
  const root = document.getElementById("eurusd-abc-lab-pl-root");
  if (!root) return;

  const esc = value => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const num = (value, digits=2) => Number.isFinite(Number(value))
    ? Number(value).toLocaleString("pl-PL", {minimumFractionDigits:digits, maximumFractionDigits:digits}) : "—";
  const pct = value => Number.isFinite(Number(value)) ? `${num(Number(value) * 100, 1)}%` : "—";
  const bps = value => Number.isFinite(Number(value)) ? `${Number(value) > 0 ? "+" : ""}${num(value, 2)} bp` : "—";
  const price = value => Number.isFinite(Number(value)) ? num(value, 5) : "—";
  const mins = value => Number.isFinite(Number(value)) ? `${num(value, 0)} min` : "—";
  const date = value => {
    if (!value) return "—";
    const d = new Date(value);
    return Number.isNaN(d.valueOf()) ? esc(value) : d.toLocaleString("pl-PL", {dateStyle:"short", timeStyle:"short"});
  };
  const directionClass = value => value === "LONG" ? "abc-long" : value === "SHORT" ? "abc-short" : "abc-flat";
  const outcomeMark = value => value === true ? "✓" : value === false ? "✕" : "—";
  const touchLabel = value => ({
    TAKE_PROFIT:"TP", STOP_LOSS:"SL", TIME_EXIT_24H:"TIME 24H",
    AMBIGUOUS_SAME_1M_BAR:"TP+SL / 1m"
  }[value] || (value ? esc(value) : "—"));
  const tradeStatus = row => {
    if (!row) return "—";
    if (row.status === "OPEN") return "W TRAKCIE";
    if (row.status === "NO_TRADE") return "BRAK POZYCJI";
    if (row.status === "AMBIGUOUS") return "NIEJEDNOZNACZNE";
    if (row.status === "NOT_TRACKED_PRE_V13") return "PRZED PR24";
    if (row.status === "UNAVAILABLE") return "NIEDOSTĘPNE";
    if (row.status === "CLOSED") return touchLabel(row.exit_reason) === "—" ? "ZAMKNIĘTA" : `ZAMKNIĘTA · ${touchLabel(row.exit_reason)}`;
    return esc(row.status || "—");
  };

  function currentArm(arm) {
    return `<article class="abc-arm">
      <div class="abc-arm-head"><span>${esc(arm.arm_id)} · ${esc(arm.label_pl)}</span><b class="${directionClass(arm.direction)}">SYGNAŁ ${esc(arm.direction)}</b></div>
      <div class="abc-score">${arm.available ? `${num(arm.score,1)}<small>/100</small>` : "NIEDOSTĘPNY"}</div>
      <p>confidence: <b>${arm.available ? pct(arm.confidence) : "—"}</b></p>
    </article>`;
  }

  function latestOutcomeCell(row) {
    if (!row || !row.available) return `<span class="abc-muted">—</span>`;
    return `<b class="${Number(row.signed_return_bps) >= 0 ? "abc-positive" : "abc-negative"}">${bps(row.signed_return_bps)}</b> <span>${outcomeMark(row.directional_correct)}</span>`;
  }

  function cumulativeCell(row) {
    if (!row) return "—";
    return `<div class="abc-cum-cell"><b>${row.hit_rate == null ? "—" : pct(row.hit_rate)}</b><span>hit rate</span><b>${bps(row.mean_signed_return_bps_signal_only)}</b><span>śr. sygnał</span><small>${Number(row.signals || 0)} sygnałów / ${Number(row.matured_captures || 0)} dojrzałych</small></div>`;
  }

  function tradeRow(armId, row) {
    const signal = row?.direction || "UNAVAILABLE";
    const resultClass = Number(row?.realized_bps) >= 0 ? "abc-positive" : "abc-negative";
    const terminal = row?.status === "CLOSED" || row?.status === "AMBIGUOUS";
    const firstTouch = row?.first_touch ? `${touchLabel(row.first_touch)}${row.minutes_to_first_touch != null ? ` · ${mins(row.minutes_to_first_touch)}` : ""}` : "—";
    return `<tr>
      <td><b>${esc(armId)}</b></td>
      <td><b class="${directionClass(signal)}">${esc(signal)}</b></td>
      <td>${price(row?.entry_price)}</td><td>${price(row?.stop_price)}</td><td>${price(row?.target_price)}</td>
      <td><b>${tradeStatus(row)}</b></td>
      <td>${terminal ? bps(row?.mfe_bps) : "—"}</td><td>${terminal ? bps(row?.mae_bps) : "—"}</td>
      <td>${firstTouch}</td>
      <td>${row?.realized_bps == null ? "—" : `<b class="${resultClass}">${bps(row.realized_bps)}</b>`}</td>
    </tr>`;
  }

  function tradeCumulativeRow(armId, row) {
    row = row || {};
    return `<tr>
      <td><b>${esc(armId)}</b></td>
      <td>${Number(row.signals || 0)}</td>
      <td>${Number(row.open_trades || 0)} / ${Number(row.closed_trades || 0)}</td>
      <td>${Number(row.take_profit || 0)} / ${Number(row.stop_loss || 0)} / ${Number(row.time_exit_24h || 0)}</td>
      <td>${Number(row.ambiguous_same_1m_bar || 0)}</td>
      <td>${row.win_rate == null ? "—" : pct(row.win_rate)}</td>
      <td>${bps(row.mean_realized_bps)}</td>
      <td>${bps(row.mean_mfe_bps)} / ${bps(row.mean_mae_bps)}</td>
      <td>${mins(row.mean_minutes_to_first_touch)}</td>
    </tr>`;
  }

  function render(payload) {
    const latest = payload.latest || {};
    const arms = latest.arms || {};
    const horizons = latest.horizons || {};
    const comparison = payload.comparison || {};
    const virtualTrade = latest.virtual_trade || {};
    const tradeArms = virtualTrade.arms || {};
    const tradeComparison = payload.trade_comparison?.arms || {};
    const ordered = ["30m", "60m", "120m", "240m", "1440m"];
    const risk = virtualTrade.risk || {};

    root.innerHTML = `<article class="abc-lab">
      <div class="abc-title-row"><div><span class="abc-eyebrow">PR24 · EUR/USD</span><h3>A/B/C Research Lab</h3><p>Równoległe, prospektywne porównanie trzech silników na tym samym frozen market reference.</p></div><span class="abc-chip">LIVE SHADOW</span></div>
      <div class="abc-boundary">Tylko obserwacja · brak wpływu na aktywną decyzję Daily EUR/USD · brak trade execution</div>
      <div class="abc-capture-meta"><span>Sygnał wygenerowany: <b>${esc(date(latest.signal_generated_at))}</b></span><span>Obserwacja rynku: <b>${esc(date(latest.market_observed_at))}</b></span><span>Reference: <b>${num(latest.reference_price,5)}</b></span></div>
      <div class="abc-arms">${["A","B","C"].map(key => currentArm(arms[key] || {arm_id:key,label_pl:key,direction:"UNAVAILABLE",available:false})).join("")}</div>

      <div class="abc-explain"><b>1. Wynik punktowy</b><span>Sprawdza, gdzie EUR/USD jest dokładnie po 30m / 1h / 2h / 4h / 24h. Nie uwzględnia wcześniejszego TP lub SL.</span></div>
      <h4>Bieżący capture — wyniki forward</h4>
      <div class="abc-table-wrap"><table class="abc-table"><thead><tr><th>Horyzont</th><th>Status</th><th>Ruch EUR/USD</th><th>A · Techniczny</th><th>B · Belief</th><th>C · Hybrydowy</th></tr></thead><tbody>
      ${ordered.map(key => { const row=horizons[key]||{}; return `<tr><td><b>${esc(row.label || key)}</b></td><td>${row.status === "RESOLVED" ? "ROZLICZONY" : "OCZEKUJE"}</td><td>${row.status === "RESOLVED" ? bps(row.raw_return_bps) : "—"}</td><td>${latestOutcomeCell(row.arms?.A)}</td><td>${latestOutcomeCell(row.arms?.B)}</td><td>${latestOutcomeCell(row.arms?.C)}</td></tr>`; }).join("")}
      </tbody></table></div>

      <h4>Narastające porównanie kierunku</h4>
      <div class="abc-table-wrap"><table class="abc-table abc-cumulative"><thead><tr><th>Horyzont</th><th>A · Techniczny</th><th>B · Belief</th><th>C · Hybrydowy</th></tr></thead><tbody>
      ${ordered.map(key => { const row=comparison[key]||{}; return `<tr><td><b>${esc(row.label || key)}</b></td><td>${cumulativeCell(row.A)}</td><td>${cumulativeCell(row.B)}</td><td>${cumulativeCell(row.C)}</td></tr>`; }).join("")}
      </tbody></table></div>

      <div class="abc-explain abc-trade-explain"><b>2. Wirtualna pozycja</b><span>Dla LONG/SHORT zamrażamy wejście przy reference i śledzimy TP/SL na świecach 1m. To osobny test wartości tradingowej sygnału.</span></div>
      <h4>Wirtualna ścieżka pozycji — TP / SL / MFE / MAE</h4>
      ${virtualTrade.available ? `<div class="abc-risk"><span>Risk: max(1,35 × ATR26 30m, 0,27%)</span><span>TP: 1,8R</span><span>Max: 24h</span><span>Monitoring: 1m</span></div>` : `<p class="abc-foot">Ścieżka TP/SL jest liczona prospektywnie dopiero od engine v1.3; starszych capture’ów nie backfillujemy.</p>`}
      <div class="abc-table-wrap"><table class="abc-table abc-trade-table"><thead><tr><th>Silnik</th><th>Sygnał</th><th>Entry</th><th>SL</th><th>TP</th><th>Status</th><th>MFE</th><th>MAE</th><th>First touch</th><th>Wynik</th></tr></thead><tbody>
      ${["A","B","C"].map(key => tradeRow(key, tradeArms[key] || {})).join("")}
      </tbody></table></div>
      <p class="abc-method-note">MFE/MAE finalizujemy przy zamknięciu wirtualnej pozycji. Jeśli TP i SL wystąpią w tej samej świecy 1m, wynik oznaczamy jako niejednoznaczny i nie zgadujemy kolejności. Wyniki są brutto — bez spreadu i slippage; reference nie jest executable quote.</p>

      <h4>Narastające wyniki wirtualnych pozycji</h4>
      <div class="abc-table-wrap"><table class="abc-table abc-trade-cumulative"><thead><tr><th>Silnik</th><th>Sygnały</th><th>Otwarte / zamknięte</th><th>TP / SL / 24h</th><th>Amb.</th><th>Win rate</th><th>Śr. wynik</th><th>Śr. MFE / MAE</th><th>Śr. czas</th></tr></thead><tbody>
      ${["A","B","C"].map(key => tradeCumulativeRow(key, tradeComparison[key])).join("")}
      </tbody></table></div>

      <p class="abc-foot">Próba: <b>${Number(payload.sample?.captures || 0)}</b> capture · ${esc(payload.engine_version || "")}</p>
    </article>`;
  }

  const style = document.createElement("style");
  style.textContent = `.abc-lab{padding:20px;border:1px solid rgba(87,194,255,.28);border-radius:18px;background:linear-gradient(135deg,rgba(47,128,237,.09),rgba(255,255,255,.025));color:#eef7ff}.abc-title-row{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}.abc-eyebrow{font-size:9px;font-weight:900;letter-spacing:.08em;text-transform:uppercase;color:#79c8ff}.abc-title-row h3{margin:5px 0 5px;font-size:27px}.abc-title-row p,.abc-foot,.abc-arm p,.abc-method-note{margin:0;color:#96aabe;font-size:11px;line-height:1.5}.abc-chip{padding:6px 10px;border-radius:999px;background:rgba(59,223,163,.12);color:#8ff0cc;font-size:10px;font-weight:950;white-space:nowrap}.abc-boundary{margin:15px 0 8px;padding:10px 12px;border:1px solid rgba(59,223,163,.15);border-radius:12px;color:#a7e7d0;background:rgba(59,223,163,.05);font-size:10px}.abc-capture-meta,.abc-risk{display:flex;flex-wrap:wrap;gap:8px 18px;padding:8px 12px;color:#9fb3c7;font-size:10px}.abc-capture-meta b{color:#eef7ff}.abc-risk{margin-bottom:7px;border:1px solid rgba(121,200,255,.12);border-radius:10px;background:rgba(121,200,255,.04)}.abc-arms{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:8px 0 22px}.abc-arm{padding:14px;border:1px solid rgba(255,255,255,.09);border-radius:14px;background:rgba(0,0,0,.15)}.abc-arm-head{display:flex;justify-content:space-between;gap:8px;font-size:10px;color:#9fb3c7;font-weight:850}.abc-score{margin:12px 0 4px;font-size:30px;font-weight:950}.abc-score small{font-size:12px;color:#7e93a7}.abc-long,.abc-positive{color:#65e6a4}.abc-short,.abc-negative{color:#ff8ca3}.abc-flat{color:#ffd36b}.abc-lab h4{margin:20px 0 9px;font-size:13px}.abc-explain{display:grid;grid-template-columns:auto 1fr;gap:10px;margin:18px 0 3px;padding:10px 12px;border-left:3px solid rgba(121,200,255,.55);background:rgba(121,200,255,.05);font-size:10px}.abc-explain span{color:#9fb3c7}.abc-trade-explain{margin-top:28px;border-left-color:rgba(59,223,163,.55);background:rgba(59,223,163,.04)}.abc-table-wrap{overflow:auto}.abc-table{width:100%;border-collapse:collapse;font-size:10px}.abc-table th,.abc-table td{padding:9px 8px;border-bottom:1px solid rgba(255,255,255,.08);text-align:left;vertical-align:top;white-space:nowrap}.abc-table th{font-size:8px;text-transform:uppercase;color:#7f95aa}.abc-cum-cell{display:grid;grid-template-columns:auto auto;gap:2px 7px;align-items:baseline}.abc-cum-cell span{color:#7f95aa;font-size:8px}.abc-cum-cell small{grid-column:1/-1;color:#8297aa;margin-top:3px}.abc-muted{color:#718598}.abc-method-note{margin:8px 0 4px;padding:0 2px}.abc-foot{margin-top:14px}@media(max-width:760px){.abc-arms{grid-template-columns:1fr}.abc-title-row{flex-direction:column}.abc-explain{grid-template-columns:1fr}.abc-table{min-width:760px}.abc-trade-table,.abc-trade-cumulative{min-width:980px}}`;
  document.head.appendChild(style);

  fetch("/data/investments/eurusd_abc_public_pl.json?v=" + Date.now(), {cache:"no-store"})
    .then(response => { if (!response.ok) throw new Error(String(response.status)); return response.json(); })
    .then(render)
    .catch(() => { root.innerHTML = `<div class="abc-lab"><b>A/B/C Research Lab</b><p class="abc-foot">Oczekiwanie na publiczną projekcję danych live-shadow.</p></div>`; });
})();
