(() => {
  "use strict";

  const root = document.getElementById("us-daily-stock-root");
  if (!root) return;

  const DATA_URL = "/data/investments/us_daily_stock.json";
  const labels = {
    TRADE: ["TRADE", "trade"],
    NO_TRADE: ["NO TRADE", "no-trade"],
    DATA_ERROR: ["DATA CHECK", "pending"],
    PENDING: ["WAITING FOR US OPEN", "pending"]
  };

  const scoreLabels = {
    catalyst: "Catalyst",
    relative_momentum: "Relative momentum",
    volume_liquidity: "Volume & liquidity",
    market_context: "Market & sector",
    risk_reward: "Risk / reward",
    historical_expectancy: "Historical setup expectancy"
  };

  const nyDate = () => {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/New_York",
      year: "numeric", month: "2-digit", day: "2-digit"
    }).formatToParts(new Date());
    const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${value.year}-${value.month}-${value.day}`;
  };

  const isNyWeekday = () => ["Mon", "Tue", "Wed", "Thu", "Fri"].includes(
    new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short" }).format(new Date())
  );

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");

  const money = (value) => Number.isFinite(Number(value))
    ? Number(value).toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : "—";

  const safeUrl = (value) => {
    try {
      const parsed = new URL(String(value), window.location.origin);
      return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "#";
    } catch (_) { return "#"; }
  };

  const formatTimestamp = (value) => {
    try {
      return new Intl.DateTimeFormat("en-US", {
        timeZone: "America/New_York", month: "short", day: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short"
      }).format(new Date(value));
    } catch (_) { return "—"; }
  };

  const metricsText = (metrics = {}) => {
    const count = Number(metrics.resolved_trades || 0);
    if (!count) return "Track record: no resolved US Daily Stock trades yet";
    const win = metrics.win_rate == null ? "—" : `${Math.round(Number(metrics.win_rate) * 100)}%`;
    const avgR = metrics.average_r == null ? "—" : `${Number(metrics.average_r).toFixed(2)}R`;
    return `Track record: ${count} resolved · win rate ${win} · average ${avgR}`;
  };

  const setShell = (payload, stale = false) => {
    let decision = stale ? "DATA_ERROR" : payload.decision;
    let [text, statusClass] = labels[decision] || labels.DATA_ERROR;
    if (stale) text = "STALE DATA — AUTO RECOVERY";
    const status = root.querySelector("[data-us-status]");
    status.className = `gpw-pick-status ${statusClass}`;
    status.textContent = text;
    root.querySelector("[data-us-date]").textContent = payload.date || "—";
    root.querySelector("[data-us-generated]").textContent = `Updated: ${formatTimestamp(payload.generated_at)}`;
  };

  const renderDetails = (payload) => {
    const details = root.querySelector("[data-us-details]");
    const body = root.querySelector("[data-us-details-body]");
    const sections = [];
    const scores = payload.selection?.scores || {};
    const rows = Object.entries(scoreLabels)
      .filter(([key]) => Number.isFinite(Number(scores[key])))
      .map(([key, label]) => `<div><span>${label}</span><b>${Number(scores[key]).toFixed(1)}/100</b></div>`).join("");
    if (rows) sections.push(`<section><h3>Score breakdown</h3><div class="gpw-pick-detail-grid">${rows}</div></section>`);

    const quality = payload.data_quality || {};
    if (quality.status) {
      sections.push(`<section><h3>Pipeline status</h3><p>Data: <b>${escapeHtml(quality.status)}</b>${quality.complete_ratio != null ? ` · completeness ${(Number(quality.complete_ratio) * 100).toFixed(0)}%` : ""}${quality.ranked_candidates != null ? ` · ranked ${escapeHtml(quality.ranked_candidates)}` : ""}</p></section>`);
    }

    const metrics = payload.metrics || {};
    if (Number(metrics.resolved_trades || 0) > 0) {
      sections.push(`<section><h3>Resolved paper trades</h3><div class="gpw-pick-detail-grid">
        <div><span>Trades</span><b>${Number(metrics.resolved_trades)}</b></div>
        <div><span>Win rate</span><b>${metrics.win_rate == null ? "—" : `${Math.round(Number(metrics.win_rate) * 100)}%`}</b></div>
        <div><span>Average return</span><b>${metrics.average_return_percent == null ? "—" : `${Number(metrics.average_return_percent).toFixed(2)}%`}</b></div>
        <div><span>Average R</span><b>${metrics.average_r == null ? "—" : `${Number(metrics.average_r).toFixed(2)}R`}</b></div>
      </div></section>`);
    }

    details.hidden = sections.length === 0;
    if (!sections.length) { details.open = false; body.innerHTML = ""; return; }
    body.innerHTML = sections.join("");
  };

  const renderEmpty = (payload, stale = false) => {
    setShell(payload, stale);
    let message = payload.reason || "No US Daily Stock selection is available yet.";
    if (stale) message = "The US Daily Stock feed is stale; automated recovery is in progress.";
    root.querySelector("[data-us-body]").innerHTML = `<div class="gpw-pick-empty"><strong>${escapeHtml(message)}</strong></div>`;
    root.querySelector("[data-us-metrics]").textContent = metricsText(payload.metrics);
    renderDetails(payload);
  };

  const renderTrade = (payload) => {
    setShell(payload, false);
    const pick = payload.selection;
    const risks = (pick.risk_factors || []).map((risk) => `<li>${escapeHtml(risk)}</li>`).join("");
    const sources = (pick.sources || []).map((source) => `<li><a href="${escapeHtml(safeUrl(source.url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(source.publisher)}</a>: ${escapeHtml(source.title)}</li>`).join("");
    const conviction = String(pick.conviction || "").toUpperCase();
    root.querySelector("[data-us-body]").innerHTML = `
      <div class="gpw-pick-main">
        <section class="gpw-pick-symbol" aria-label="Selected US stock">
          <div class="gpw-pick-title"><strong>${escapeHtml(pick.ticker)}</strong><span>${escapeHtml(pick.name)}</span></div>
          <span class="gpw-pick-score"><b>${Number(pick.score).toFixed(1)}</b>/100</span>
        </section>
        <section class="gpw-pick-thesis">
          <h3>1–2 session thesis</h3>
          <p>${escapeHtml(pick.thesis)}</p>
          <p class="gpw-pick-why"><b>Why now:</b> ${escapeHtml(pick.why_now)}</p>
          <p class="gpw-pick-why"><b>Conviction:</b> ${escapeHtml(conviction)}${pick.score_target != null ? ` · target ${Number(pick.score_target).toFixed(0)}/100` : ""}</p>
          ${risks ? `<ul class="gpw-pick-risks">${risks}</ul>` : ""}
        </section>
        <section class="gpw-pick-plan">
          <div class="gpw-pick-levels">
            <div><small>Entry zone</small><b>${money(pick.entry_zone?.[0])}–${money(pick.entry_zone?.[1])}</b></div>
            <div class="stop"><small>Stop</small><b>${money(pick.stop)}</b></div>
            <div class="target"><small>Target</small><b>${money(pick.target)}</b></div>
          </div>
          <p class="gpw-pick-activation">${escapeHtml(pick.activation)} Valid through: ${escapeHtml(pick.valid_until)}.</p>
          ${pick.market_snapshot ? `<p class="gpw-pick-activation"><b>US session snapshot:</b> ${money(pick.reference_price)} at ${escapeHtml(formatTimestamp(pick.market_snapshot.observed_at))}</p>` : ""}
          ${sources ? `<ul class="gpw-pick-sources">${sources}</ul>` : ""}
        </section>
      </div>`;
    root.querySelector("[data-us-metrics]").textContent = metricsText(payload.metrics);
    renderDetails(payload);
  };

  const render = (payload) => {
    if (!payload || !labels[payload.decision]) throw new Error("Invalid US Daily Stock contract");
    const stale = payload.date !== nyDate() && isNyWeekday();
    if (payload.decision === "TRADE" && !stale) renderTrade(payload);
    else renderEmpty(payload, stale);
  };

  const load = async () => {
    try {
      const response = await fetch(`${DATA_URL}?v=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch (_) {
      renderEmpty({
        date: nyDate(), generated_at: new Date().toISOString(), decision: "DATA_ERROR", locked: false,
        reason: "The US Daily Stock feed could not be loaded.", metrics: {}, data_quality: { status: "frontend_load_error" }
      });
    }
  };

  load();
})();
