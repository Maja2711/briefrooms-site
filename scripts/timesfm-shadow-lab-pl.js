(() => {
  "use strict";
  const root = document.getElementById("timesfm-shadow-lab-pl-root");
  if (!root) return;

  const esc = value => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const hasNum = value => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
  const num = (value, digits=2) => hasNum(value)
    ? Number(value).toLocaleString("pl-PL", {minimumFractionDigits:digits, maximumFractionDigits:digits}) : "—";
  const price = value => hasNum(value) ? num(value, 5) : "—";
  const pct = value => hasNum(value) ? `${num(Number(value) * 100, 1)}%` : "—";
  const pips = value => hasNum(value) ? `${num(value, 1)} pips` : "—";
  const date = value => {
    if (!value) return "—";
    const d = new Date(value);
    return Number.isNaN(d.valueOf()) ? esc(value) : d.toLocaleString("pl-PL", {dateStyle:"short", timeStyle:"short"});
  };
  const directionClass = value => value === "UP" ? "tfm-up" : value === "DOWN" ? "tfm-down" : "tfm-flat";
  const directionLabel = value => ({UP:"W GÓRĘ",DOWN:"W DÓŁ",FLAT:"PŁASKO"}[value] || "—");
  const horizonLabel = value => ({"1h":"1h","4h":"4h","24h_trading_bars":"24h"}[value] || value);

  function forecastCard(label, row) {
    if (!row) return `<article class="tfm-card"><span>${horizonLabel(label)}</span><strong>—</strong><small>Brak prognozy</small></article>`;
    const q = row.quantiles || {};
    const result = row.status === "RESOLVED"
      ? `<small class="${row.direction_correct === true ? "tfm-ok" : row.direction_correct === false ? "tfm-bad" : ""}">wynik: ${price(row.actual_price)} · ${row.direction_correct === true ? "✓ kierunek" : row.direction_correct === false ? "✕ kierunek" : "—"}</small>`
      : `<small class="tfm-pending">wynik: oczekuje</small>`;
    return `<article class="tfm-card">
      <div class="tfm-card-head"><span>${horizonLabel(label)}</span><b class="${directionClass(row.predicted_direction)}">${directionLabel(row.predicted_direction)}</b></div>
      <strong>${price(row.forecast_price)}</strong>
      <small>zmiana: ${hasNum(row.predicted_return) ? `${Number(row.predicted_return) >= 0 ? "+" : ""}${num(Number(row.predicted_return) * 100, 3)}%` : "—"}</small>
      <small>q10–q90: ${price(q.q10)} – ${price(q.q90)}</small>
      ${result}
    </article>`;
  }

  function performanceRow(label, row) {
    row = row || {};
    return `<tr>
      <td><b>${horizonLabel(label)}</b></td>
      <td>${Number(row.resolved || 0)}</td>
      <td>${pct(row.direction_hit_rate)}</td>
      <td>${pips(row.mae_pips)}</td>
      <td>${pips(row.rmse_pips)}</td>
      <td>${pct(row.interval_80_coverage)}</td>
    </tr>`;
  }

  function render(payload) {
    const latest = payload.latest || {};
    const horizons = latest.horizons || {};
    const perf = payload.performance || {};
    const ledger = payload.ledger || {};
    const experiment = payload.experiment || {};

    if (!latest.available) {
      root.innerHTML = `<article class="tfm-lab tfm-empty">
        <div><span class="tfm-eyebrow">TIMESFM 2.5 · SHADOW</span><h3>TimesFM Shadow Forecaster</h3></div>
        <p>Czekamy na pierwszą publiczną prognozę z prospektywnego eksperymentu. Model nie wpływa na decyzje tradingowe.</p>
      </article>`;
      return;
    }

    root.innerHTML = `<article class="tfm-lab">
      <div class="tfm-title-row">
        <div><span class="tfm-eyebrow">TIMESFM 2.5 · SHADOW · RESEARCH ONLY</span><h3>TimesFM Shadow Forecaster</h3><p>Osobny forecaster szeregu czasowego. Prognoza jest zamrożona przed wynikiem i nie wpływa na A/B/C ani aktywny sygnał EUR/USD.</p></div>
        <span class="tfm-chip">ZERO AUTHORITY</span>
      </div>
      <div class="tfm-meta">
        <span>Reference: <b>${price(latest.origin_price)}</b></span>
        <span>Forecast: <b>${date(latest.forecast_at)}</b></span>
        <span>Bar origin: <b>${date(latest.origin_bar_at)}</b></span>
        <span>Context: <b>${Number(latest.context_points || 0)} × 30m</b></span>
      </div>
      <div class="tfm-grid">
        ${forecastCard("1h", horizons["1h"])}
        ${forecastCard("4h", horizons["4h"])}
        ${forecastCard("24h_trading_bars", horizons["24h_trading_bars"])}
      </div>
      <h4>Prospektywna skuteczność</h4>
      <div class="tfm-table-wrap"><table class="tfm-table"><thead><tr><th>Horyzont</th><th>Rozliczone</th><th>Hit rate kierunku</th><th>MAE</th><th>RMSE</th><th>Pokrycie q10–q90</th></tr></thead><tbody>
        ${performanceRow("1h", perf["1h"])}
        ${performanceRow("4h", perf["4h"])}
        ${performanceRow("24h_trading_bars", perf["24h_trading_bars"])}
      </tbody></table></div>
      <p class="tfm-foot">Model: ${esc(experiment.model_id || "google/timesfm-2.5-200m-pytorch")} · ${Number(ledger.forecasts || 0)} zamrożonych prognoz · ${Number(ledger.resolved_outcomes || 0)} rozliczonych wyników. Statystyki są badawcze; mała próba może być niestabilna.</p>
    </article>`;
  }

  fetch(`/data/investments/timesfm_shadow_public_pl.json?ts=${Date.now()}`, {cache:"no-store"})
    .then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then(render)
    .catch(() => {
      root.innerHTML = `<article class="tfm-lab tfm-empty"><div><span class="tfm-eyebrow">TIMESFM 2.5 · SHADOW</span><h3>TimesFM Shadow Forecaster</h3></div><p>Publiczna projekcja nie jest jeszcze dostępna. Eksperyment pozostaje aktywny w warstwie research.</p></article>`;
    });
})();
