(() => {
  "use strict";

  const root = document.getElementById("gpw-daily-pick-root");
  if (!root) return;

  const DATA_URL = "/data/investments/gpw_daily_pick.json";
  const labels = {
    TRANSAKCJA: ["TRANSAKCJA", "trade"],
    BRAK_TRANSAKCJI: ["BRAK TRANSAKCJI", "no-trade"],
    AWARIA_DANYCH: ["AWARIA DANYCH", "error"]
  };

  const warsawDate = () => {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Europe/Warsaw",
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    }).formatToParts(new Date());
    const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${value.year}-${value.month}-${value.day}`;
  };

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const money = (value) => Number.isFinite(Number(value))
    ? `${Number(value).toLocaleString("pl-PL", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} PLN`
    : "—";

  const safeUrl = (value) => {
    try {
      const parsed = new URL(String(value), window.location.origin);
      return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "#";
    } catch (_) {
      return "#";
    }
  };

  const formatTimestamp = (value) => {
    try {
      return new Intl.DateTimeFormat("pl-PL", {
        timeZone: "Europe/Warsaw",
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit"
      }).format(new Date(value));
    } catch (_) {
      return "—";
    }
  };

  const setShell = (payload, stale = false) => {
    const decision = stale ? "AWARIA_DANYCH" : payload.decision;
    const [statusText, statusClass] = labels[decision] || labels.AWARIA_DANYCH;
    root.querySelector("[data-gpw-status]").className = `gpw-pick-status ${statusClass}`;
    root.querySelector("[data-gpw-status]").textContent = stale ? "DANE NIEAKTUALNE" : statusText;
    root.querySelector("[data-gpw-date]").textContent = payload.date || "—";
    root.querySelector("[data-gpw-generated]").textContent = `Aktualizacja: ${formatTimestamp(payload.generated_at)}`;
  };

  const renderEmpty = (payload, stale = false) => {
    setShell(payload, stale);
    const error = stale || payload.decision === "AWARIA_DANYCH";
    const reason = stale
      ? "Dzisiejszy zapis nie został jeszcze bezpiecznie opublikowany. Poprzedni sygnał nie jest pokazywany jako aktualny."
      : payload.reason;
    root.querySelector("[data-gpw-body]").innerHTML = `
      <div class="gpw-pick-empty ${error ? "error" : ""}">
        <strong>${error ? "Publikacja zatrzymana przez zabezpieczenia" : "Dziś bez wymuszonego wyboru"}</strong><br>
        ${escapeHtml(reason || "Brak kandydatury spełniającej pełny próg jakości.")}
      </div>`;
    root.querySelector("[data-gpw-metrics]").textContent = metricsText(payload.metrics);
  };

  const metricsText = (metrics = {}) => {
    const count = Number(metrics.resolved_trades || 0);
    if (!count) return "Historia: brak zakończonych paper trades";
    const winRate = metrics.win_rate == null ? "—" : `${Math.round(Number(metrics.win_rate) * 100)}%`;
    const averageR = metrics.average_r == null ? "—" : `${Number(metrics.average_r).toFixed(2)}R`;
    return `Historia: ${count} zakończonych · skuteczność ${winRate} · średnio ${averageR}`;
  };

  const renderTrade = (payload) => {
    setShell(payload, false);
    const pick = payload.selection;
    const risks = (pick.risk_factors || []).map((risk) => `<li>${escapeHtml(risk)}</li>`).join("");
    const sources = (pick.sources || []).map((source) => `
      <li><a href="${escapeHtml(safeUrl(source.url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(source.publisher)}</a>: ${escapeHtml(source.title)}</li>`).join("");
    root.querySelector("[data-gpw-body]").innerHTML = `
      <div class="gpw-pick-main">
        <section class="gpw-pick-symbol" aria-label="Wybrana spółka">
          <div class="gpw-pick-title"><strong>${escapeHtml(pick.ticker)}</strong><span>${escapeHtml(pick.name)}</span></div>
          <span class="gpw-pick-score"><b>${Number(pick.score).toFixed(1)}</b>/100</span>
        </section>
        <section class="gpw-pick-thesis">
          <h3>Teza na 1–2 sesje</h3>
          <p>${escapeHtml(pick.thesis)}</p>
          <p class="gpw-pick-why"><b>Dlaczego teraz:</b> ${escapeHtml(pick.why_now)}</p>
          ${risks ? `<ul class="gpw-pick-risks">${risks}</ul>` : ""}
        </section>
        <section class="gpw-pick-plan">
          <div class="gpw-pick-levels">
            <div><small>Strefa wejścia</small><b>${money(pick.entry_zone?.[0])}–${money(pick.entry_zone?.[1])}</b></div>
            <div class="stop"><small>Stop</small><b>${money(pick.stop)}</b></div>
            <div class="target"><small>Cel</small><b>${money(pick.target)}</b></div>
          </div>
          <p class="gpw-pick-activation">${escapeHtml(pick.activation)} Ważność planu: ${escapeHtml(pick.valid_until)}.</p>
          ${sources ? `<ul class="gpw-pick-sources">${sources}</ul>` : ""}
        </section>
      </div>`;
    root.querySelector("[data-gpw-metrics]").textContent = metricsText(payload.metrics);
  };

  const render = (payload) => {
    if (!payload || !labels[payload.decision]) throw new Error("Niepoprawny kontrakt danych");
    const today = warsawDate();
    const weekday = new Date(`${today}T12:00:00+02:00`).getDay();
    const stale = payload.date !== today && weekday >= 1 && weekday <= 5;
    if (payload.decision === "TRANSAKCJA" && !stale) renderTrade(payload);
    else renderEmpty(payload, stale);
  };

  const load = async () => {
    try {
      const response = await fetch(`${DATA_URL}?v=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch (error) {
      renderEmpty({
        date: warsawDate(),
        generated_at: new Date().toISOString(),
        decision: "AWARIA_DANYCH",
        reason: "Nie udało się pobrać zweryfikowanego zapisu. Nie pokazujemy poprzedniego sygnału jako aktualnego.",
        metrics: {}
      });
    }
  };

  load();
})();
