(() => {
  "use strict";

  const lang = document.documentElement.lang === "en" ? "en" : "pl";
  const roots = [
    document.getElementById("gpw-daily-pick-root"),
    document.getElementById("gpw-pol-daily-root")
  ].filter(Boolean);
  if (!roots.length) return;

  const T = lang === "pl" ? {
    title: "Pełny ranking kandydatów · P0.3",
    subtitle: "Całe uniwersum GPW przed warstwą news/event, Opening Confirmation i EV.",
    rank: "#",
    ticker: "Walor",
    score: "Quant",
    momentum: "Rel. momentum",
    ret5: "5D",
    ret20: "20D",
    liquidity: "Płynność",
    risk: "Ryzyko",
    status: "Status",
    ranked: "ranking",
    screened: "screening",
    data: "dane",
    coverage: "Pokrycie świeżych danych",
    expected: "Sesja bazowa",
    note: "Ranking jest etapem ilościowym. Finalny wybór może się zmienić po katalizatorach, P0.1 Opening Confirmation, P0.2 EV i recenzji bezpieczeństwa."
  } : {
    title: "Full candidate ranking · P0.3",
    subtitle: "Entire GPW universe before news/event, Opening Confirmation and EV layers.",
    rank: "#",
    ticker: "Ticker",
    score: "Quant",
    momentum: "Rel. momentum",
    ret5: "5D",
    ret20: "20D",
    liquidity: "Liquidity",
    risk: "Risk",
    status: "Status",
    ranked: "ranked",
    screened: "screened",
    data: "data",
    coverage: "Fresh-data coverage",
    expected: "Base session",
    note: "This is the quantitative stage. The final pick may change after catalysts, P0.1 Opening Confirmation, P0.2 EV and the safety review."
  };

  const pct = (value, digits = 1) => Number.isFinite(Number(value))
    ? `${(Number(value) * 100).toFixed(digits)}%`
    : "—";
  const num = (value, digits = 1) => Number.isFinite(Number(value))
    ? Number(value).toFixed(digits)
    : "—";
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (m) => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"
  }[m]));

  const statusLabel = (row) => {
    if (row.status === "RANKED") return T.ranked;
    if (row.status === "SCREENED_OUT") return T.screened;
    return T.data;
  };

  const render = (payload) => {
    const rows = Array.isArray(payload?.rows) ? payload.rows : [];
    if (!rows.length) return;

    const body = rows.map((row) => {
      const scores = row.scores || {};
      const returns = row.returns || {};
      const cls = row.status === "RANKED" ? "ranked" : row.status === "SCREENED_OUT" ? "screened" : "data";
      return `<tr class="gpw-rank-${cls}">
        <td>${row.rank ?? "—"}</td>
        <td><strong>${esc(row.ticker || row.symbol)}</strong><small>${esc(row.name || "")}</small></td>
        <td>${num(row.quant_pre_score, 1)}</td>
        <td>${num(scores.relative_momentum, 1)}</td>
        <td>${pct(returns["5d"], 1)}</td>
        <td>${pct(returns["20d"], 1)}</td>
        <td>${num(scores.volume_liquidity, 1)}</td>
        <td>${pct(row.risk_percent, 1)}</td>
        <td><span class="gpw-rank-status ${cls}">${statusLabel(row)}</span>${row.reason ? `<small class="gpw-rank-reason">${esc(row.reason)}</small>` : ""}</td>
      </tr>`;
    }).join("");

    const html = `<details class="gpw-full-ranking" data-gpw-full-ranking>
      <summary><span><strong>${T.title}</strong><small>${T.subtitle}</small></span><span class="gpw-ranking-meta">${T.coverage}: <b>${pct(payload?.data_quality?.complete_ratio, 0)}</b> · ${T.expected}: <b>${esc(payload?.expected_session || "—")}</b></span></summary>
      <div class="gpw-ranking-scroll">
        <table><thead><tr><th>${T.rank}</th><th>${T.ticker}</th><th>${T.score}</th><th>${T.momentum}</th><th>${T.ret5}</th><th>${T.ret20}</th><th>${T.liquidity}</th><th>${T.risk}</th><th>${T.status}</th></tr></thead><tbody>${body}</tbody></table>
      </div>
      <p class="gpw-ranking-note">${T.note}</p>
    </details>`;

    const place = () => {
      for (const root of roots) {
        if (root.querySelector("[data-gpw-full-ranking]")) continue;
        const card = root.querySelector('[data-dsm-market="gpw"]') || root.querySelector(".dsm-market-card");
        if (card) card.insertAdjacentHTML("afterend", html);
        else root.insertAdjacentHTML("beforeend", html);
      }
    };
    place();
    for (const root of roots) {
      if (root.querySelector("[data-gpw-full-ranking]")) continue;
      const observer = new MutationObserver(() => {
        place();
        if (root.querySelector("[data-gpw-full-ranking]")) observer.disconnect();
      });
      observer.observe(root, {childList:true, subtree:true});
      window.setTimeout(() => observer.disconnect(), 10000);
    }
  };

  fetch(`/data/investments/gpw_daily_candidate_ranking.json?v=${Date.now()}`, {
    cache: "no-store",
    headers: {"Cache-Control":"no-cache"}
  })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then(render)
    .catch(() => {});
})();
