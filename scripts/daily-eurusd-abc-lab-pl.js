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
  const date = value => {
    if (!value) return "—";
    const d = new Date(value);
    return Number.isNaN(d.valueOf()) ? esc(value) : d.toLocaleString("pl-PL", {dateStyle:"short", timeStyle:"short"});
  };
  const directionClass = value => value === "LONG" ? "abc-long" : value === "SHORT" ? "abc-short" : "abc-flat";
  const outcomeMark = value => value === true ? "✓" : value === false ? "✕" : "—";

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

  function render(payload) {
    const latest = payload.latest || {};
    const arms = latest.arms || {};
    const horizons = latest.horizons || {};
    const comparison = payload.comparison || {};
    const ordered = ["30m", "60m", "120m", "240m", "1440m"];

    root.innerHTML = `<article class="abc-lab">
      <div class="abc-title-row"><div><span class="abc-eyebrow">PR23 · EUR/USD</span><h3>A/B/C Research Lab</h3><p>Równoległe, prospektywne porównanie trzech silników na tym samym frozen market reference.</p></div><span class="abc-chip">LIVE SHADOW</span></div>
      <div class="abc-boundary">Tylko obserwacja · brak wpływu na aktywną decyzję Daily EUR/USD · brak trade execution</div>
      <div class="abc-capture-meta"><span>Sygnał wygenerowany: <b>${esc(date(latest.signal_generated_at))}</b></span><span>Obserwacja rynku: <b>${esc(date(latest.market_observed_at))}</b></span><span>Reference: <b>${num(latest.reference_price,5)}</b></span></div>
      <div class="abc-arms">${["A","B","C"].map(key => currentArm(arms[key] || {arm_id:key,label_pl:key,direction:"UNAVAILABLE",available:false})).join("")}</div>

      <h4>Bieżący capture — wyniki forward</h4>
      <div class="abc-table-wrap"><table class="abc-table"><thead><tr><th>Horyzont</th><th>Status</th><th>Ruch EUR/USD</th><th>A · Techniczny</th><th>B · Belief</th><th>C · Hybrydowy</th></tr></thead><tbody>
      ${ordered.map(key => { const row=horizons[key]||{}; return `<tr><td><b>${esc(row.label || key)}</b></td><td>${row.status === "RESOLVED" ? "ROZLICZONY" : "OCZEKUJE"}</td><td>${row.status === "RESOLVED" ? bps(row.raw_return_bps) : "—"}</td><td>${latestOutcomeCell(row.arms?.A)}</td><td>${latestOutcomeCell(row.arms?.B)}</td><td>${latestOutcomeCell(row.arms?.C)}</td></tr>`; }).join("")}
      </tbody></table></div>

      <h4>Narastające porównanie</h4>
      <div class="abc-table-wrap"><table class="abc-table abc-cumulative"><thead><tr><th>Horyzont</th><th>A · Techniczny</th><th>B · Belief</th><th>C · Hybrydowy</th></tr></thead><tbody>
      ${ordered.map(key => { const row=comparison[key]||{}; return `<tr><td><b>${esc(row.label || key)}</b></td><td>${cumulativeCell(row.A)}</td><td>${cumulativeCell(row.B)}</td><td>${cumulativeCell(row.C)}</td></tr>`; }).join("")}
      </tbody></table></div>
      <p class="abc-foot">Próba: <b>${Number(payload.sample?.captures || 0)}</b> capture · ${esc(payload.engine_version || "")}</p>
    </article>`;
  }

  const style = document.createElement("style");
  style.textContent = `.abc-lab{padding:20px;border:1px solid rgba(87,194,255,.28);border-radius:18px;background:linear-gradient(135deg,rgba(47,128,237,.09),rgba(255,255,255,.025));color:#eef7ff}.abc-title-row{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}.abc-eyebrow{font-size:9px;font-weight:900;letter-spacing:.08em;text-transform:uppercase;color:#79c8ff}.abc-title-row h3{margin:5px 0 5px;font-size:27px}.abc-title-row p,.abc-foot,.abc-arm p{margin:0;color:#96aabe;font-size:11px;line-height:1.5}.abc-chip{padding:6px 10px;border-radius:999px;background:rgba(59,223,163,.12);color:#8ff0cc;font-size:10px;font-weight:950;white-space:nowrap}.abc-boundary{margin:15px 0 8px;padding:10px 12px;border:1px solid rgba(59,223,163,.15);border-radius:12px;color:#a7e7d0;background:rgba(59,223,163,.05);font-size:10px}.abc-capture-meta{display:flex;flex-wrap:wrap;gap:8px 18px;padding:8px 12px;color:#9fb3c7;font-size:10px}.abc-capture-meta b{color:#eef7ff}.abc-arms{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:8px 0 22px}.abc-arm{padding:14px;border:1px solid rgba(255,255,255,.09);border-radius:14px;background:rgba(0,0,0,.15)}.abc-arm-head{display:flex;justify-content:space-between;gap:8px;font-size:10px;color:#9fb3c7;font-weight:850}.abc-score{margin:12px 0 4px;font-size:30px;font-weight:950}.abc-score small{font-size:12px;color:#7e93a7}.abc-long,.abc-positive{color:#65e6a4}.abc-short,.abc-negative{color:#ff8ca3}.abc-flat{color:#ffd36b}.abc-lab h4{margin:20px 0 9px;font-size:13px}.abc-table-wrap{overflow:auto}.abc-table{width:100%;border-collapse:collapse;font-size:10px}.abc-table th,.abc-table td{padding:9px 8px;border-bottom:1px solid rgba(255,255,255,.08);text-align:left;vertical-align:top;white-space:nowrap}.abc-table th{font-size:8px;text-transform:uppercase;color:#7f95aa}.abc-cum-cell{display:grid;grid-template-columns:auto auto;gap:2px 7px;align-items:baseline}.abc-cum-cell span{color:#7f95aa;font-size:8px}.abc-cum-cell small{grid-column:1/-1;color:#8297aa;margin-top:3px}.abc-muted{color:#718598}.abc-foot{margin-top:14px}@media(max-width:760px){.abc-arms{grid-template-columns:1fr}.abc-title-row{flex-direction:column}.abc-table{min-width:760px}}`;
  document.head.appendChild(style);

  fetch("/data/investments/eurusd_abc_public_pl.json?v=" + Date.now(), {cache:"no-store"})
    .then(response => { if (!response.ok) throw new Error(String(response.status)); return response.json(); })
    .then(render)
    .catch(() => { root.innerHTML = `<div class="abc-lab"><b>A/B/C Research Lab</b><p class="abc-foot">Oczekiwanie na pierwszą publiczną projekcję danych live-shadow.</p></div>`; });
})();
