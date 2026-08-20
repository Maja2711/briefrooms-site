(() => {
  "use strict";
  const root = document.getElementById("eurusd-daily-root");
  if (!root) return;

  const esc = (value) => String(value ?? "")
    .replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;")
    .replaceAll('"',"&quot;").replaceAll("'","&#039;");

  const px = (value) => Number.isFinite(Number(value))
    ? Number(value).toLocaleString("pl-PL",{minimumFractionDigits:5,maximumFractionDigits:5})
    : "—";

  const render = (p) => {
    const direction = String(p.direction || "FLAT");
    const stage = String(p?.metadata?.rollout_stage || "shadow").toUpperCase();
    const components = p?.metadata?.components || {};
    const directional = direction === "LONG" || direction === "SHORT";
    const plan = directional
      ? `<div class="brfx-plan"><div><span>Wejście</span><b>${px(p.entry)}</b></div><div><span>Stop</span><b>${px(p.stop)}</b></div><div><span>Cel</span><b>${px(p.target)}</b></div></div>`
      : `<p class="brfx-muted">Brak kierunkowego setupu przy obecnych progach.</p>`;

    root.innerHTML = `<article class="brfx-card">
      <div class="brfx-head"><div><small>Daily Engine Output · ${esc(p.decision_mode || "WITHOUT")}</small><h3>EUR/USD Spot</h3></div><span class="brfx-stage">${esc(stage)}</span></div>
      <div class="brfx-signal"><strong>${esc(direction)}</strong><span>score ${Number(p.score || 0).toFixed(1)}/100 · confidence ${Math.round(Number(p.confidence || 0)*100)}%</span></div>
      ${plan}
      <details><summary>Metoda i Belief boundary</summary>
        <div class="brfx-details">
          <p>Trend: <b>${Number(components.trend || 0).toFixed(3)}</b> · USD environment: <b>${Number(components.broad_usd_environment || 0).toFixed(3)}</b> · US rates proxy: <b>${Number(components.us_rates_pressure_proxy || 0).toFixed(3)}</b>.</p>
          <p>Belief Core nie wpływa na decyzję v1. Output jest przygotowany pod późniejszą kalibrację WITHOUT/WITH.</p>
        </div>
      </details>
      <p class="brfx-foot">Horyzont: ${esc(p.horizon)} · ${esc(p.engine_version)} · obserwacja ${esc(p.timestamp)}</p>
    </article>`;
  };

  const style = document.createElement("style");
  style.textContent = `.brfx-card{padding:18px;border:1px solid rgba(255,191,63,.28);border-radius:18px;background:linear-gradient(135deg,rgba(255,191,63,.08),rgba(255,255,255,.025));color:#eef7ff}.brfx-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}.brfx-head small{display:block;color:#8fa3b8;font-size:9px;font-weight:900;text-transform:uppercase;letter-spacing:.06em}.brfx-head h3{margin:5px 0 0;font-size:26px;letter-spacing:-.03em}.brfx-stage{padding:6px 9px;border-radius:999px;background:rgba(255,191,63,.13);color:#ffd36f;font-size:10px;font-weight:950}.brfx-signal{display:flex;align-items:baseline;gap:12px;margin:18px 0}.brfx-signal strong{font-size:28px}.brfx-signal span,.brfx-muted,.brfx-foot,.brfx-details{color:#96aabe;font-size:11px;line-height:1.5}.brfx-plan{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:14px 0}.brfx-plan div{padding:10px;border:1px solid rgba(255,255,255,.09);border-radius:12px;background:rgba(0,0,0,.16)}.brfx-plan span{display:block;color:#7f95aa;font-size:9px;text-transform:uppercase}.brfx-plan b{display:block;margin-top:4px;font-size:16px}.brfx-card details{margin-top:14px}.brfx-card summary{cursor:pointer;color:#9ffff6;font-size:11px;font-weight:850}.brfx-foot{margin:14px 0 0}.brfx-error{padding:16px;border:1px solid rgba(255,77,109,.25);border-radius:14px;color:#ffafbd;background:rgba(255,77,109,.06)}@media(max-width:600px){.brfx-plan{grid-template-columns:1fr}.brfx-signal{align-items:flex-start;flex-direction:column;gap:3px}}`;
  document.head.appendChild(style);

  fetch("/data/investments/eurusd_daily_spot.json?v="+Date.now(), {cache:"no-store"})
    .then((response) => { if (!response.ok) throw new Error("feed"); return response.json(); })
    .then(render)
    .catch(() => { root.innerHTML = '<div class="brfx-error">Nie udało się wczytać stanu Daily EUR/USD Spot.</div>'; });
})();
