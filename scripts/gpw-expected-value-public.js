(() => {
  "use strict";

  const lang = document.documentElement.lang === "en" ? "en" : "pl";
  const roots = [
    document.getElementById("gpw-daily-pick-root"),
    document.getElementById("gpw-pol-daily-root")
  ].filter(Boolean);
  if (!roots.length) return;

  const T = lang === "pl" ? {
    title: "Empiryczne EV · P0.2",
    rr: "Dynamiczne R:R",
    ev: "EV netto",
    conservative: "EV po karze za niepewność",
    tp: "P(TP przed SL)",
    sl: "P(SL przed TP)",
    time: "P(wyjścia czasowego)",
    sample: "Efektywna próba",
    analogues: "analogi",
    note: "Target wybiera model walk-forward na podstawie podobnych historycznych setupów. To empiryczna estymacja, nie skalibrowane prawdopodobieństwo sukcesu."
  } : {
    title: "Empirical EV · P0.2",
    rr: "Dynamic R:R",
    ev: "Net EV",
    conservative: "Uncertainty-adjusted EV",
    tp: "P(TP before SL)",
    sl: "P(SL before TP)",
    time: "P(time exit)",
    sample: "Effective sample",
    analogues: "analogues",
    note: "The target is selected by a walk-forward model using similar historical setups. This is an empirical estimate, not a calibrated probability of success."
  };

  const pct = (value) => Number.isFinite(Number(value))
    ? `${(Number(value) * 100).toFixed(1)}%`
    : "—";
  const r = (value) => Number.isFinite(Number(value))
    ? `${Number(value) >= 0 ? "+" : ""}${Number(value).toFixed(2)}R`
    : "—";
  const num = (value, digits = 2) => Number.isFinite(Number(value))
    ? Number(value).toFixed(digits)
    : "—";

  const render = (payload) => {
    const selection = payload?.selection || {};
    const model = selection?.expected_value_model || {};
    if (payload?.decision !== "TRANSAKCJA" || model?.status !== "ready") return;

    const html = `<section class="dsm-ev-panel" data-gpw-ev-panel>
      <div class="dsm-ev-head"><strong>${T.title}</strong><span>${T.rr}: <b>${num(selection.reward_risk, 2)}R</b></span></div>
      <div class="dsm-ev-grid">
        <div><small>${T.ev}</small><b>${r(model.expected_net_r)}</b></div>
        <div><small>${T.conservative}</small><b>${r(model.conservative_ev_r)}</b></div>
        <div><small>${T.tp}</small><b>${pct(model.tp_before_sl_probability)}</b></div>
        <div><small>${T.sl}</small><b>${pct(model.sl_before_tp_probability)}</b></div>
        <div><small>${T.time}</small><b>${pct(model.time_exit_probability)}</b></div>
        <div><small>${T.sample}</small><b>${num(model.effective_sample_size, 1)} · ${Number(model.analogue_count || 0)} ${T.analogues}</b></div>
      </div>
      <p class="dsm-ev-note">${T.note}</p>
    </section>`;

    const place = () => {
      for (const root of roots) {
        const card = root.querySelector('[data-dsm-market="gpw"]') || root.querySelector(".dsm-market-card");
        if (!card || card.querySelector("[data-gpw-ev-panel]")) continue;
        const levels = card.querySelector(".dsm-levels");
        if (levels) levels.insertAdjacentHTML("afterend", html);
        else card.insertAdjacentHTML("beforeend", html);
      }
    };

    place();
    for (const root of roots) {
      const observer = new MutationObserver(() => {
        place();
        if (root.querySelector("[data-gpw-ev-panel]")) observer.disconnect();
      });
      if (!root.querySelector("[data-gpw-ev-panel]")) {
        observer.observe(root, { childList: true, subtree: true });
        window.setTimeout(() => observer.disconnect(), 10000);
      }
    }
  };

  fetch(`/data/investments/gpw_daily_pick.json?v=${Date.now()}`, {
    cache: "no-store",
    headers: { "Cache-Control": "no-cache" }
  })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then(render)
    .catch(() => {});
})();
