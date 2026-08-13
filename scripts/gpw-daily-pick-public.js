(() => {
  "use strict";

  const root = document.getElementById("gpw-daily-pick-root");
  if (!root) return;

  const DATA_URL = "/data/investments/gpw_daily_pick.json";
  const labels = {
    TRANSAKCJA: ["TRANSAKCJA", "trade"],
    BRAK_TRANSAKCJI: ["BRAK DZISIAJ WYBORU", "no-trade"],
    AWARIA_DANYCH: ["ANALIZA DANYCH — TRWA", "pending"]
  };

  const scoreLabels = {
    catalyst: "Katalizator",
    relative_momentum: "Momentum relatywne",
    volume_liquidity: "Wolumen i płynność",
    market_context: "Rynek i sektor",
    risk_reward: "Relacja zysku do ryzyka",
    historical_expectancy: "Dotychczasowa skuteczność układu"
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

  const isWarsawWeekday = () => ["Mon", "Tue", "Wed", "Thu", "Fri"].includes(
    new Intl.DateTimeFormat("en-US", { timeZone: "Europe/Warsaw", weekday: "short" }).format(new Date())
  );

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
    let [statusText, statusClass] = labels[decision] || labels.AWARIA_DANYCH;
    if (stale) {
      statusText = "DANE NIEAKTUALNE — TRWA NAPRAWA";
      statusClass = "pending";
    } else if (payload.decision === "AWARIA_DANYCH" && payload.locked) {
      statusText = "BRAK POTWIERDZONEGO SYGNAŁU";
      statusClass = "no-trade";
    }
    root.querySelector("[data-gpw-status]").className = `gpw-pick-status ${statusClass}`;
    root.querySelector("[data-gpw-status]").textContent = statusText;
    root.querySelector("[data-gpw-date]").textContent = payload.date || "—";
    root.querySelector("[data-gpw-generated]").textContent = `Aktualizacja: ${formatTimestamp(payload.generated_at)}`;
  };

  const renderEmpty = (payload, stale = false) => {
    setShell(payload, stale);
    let message = "Brak dzisiaj wyboru";
    if (stale) message = "Trwa automatyczna naprawa porannego wyboru";
    else if (payload.decision === "AWARIA_DANYCH" && payload.locked) {
      message = "Brak dzisiaj wyboru — sygnał nie został potwierdzony przed 08:30";
    }
    root.querySelector("[data-gpw-body]").innerHTML = `
      <div class="gpw-pick-empty"><strong>${escapeHtml(message)}</strong></div>`;
    root.querySelector("[data-gpw-metrics]").textContent = metricsText(payload.metrics);
    renderDetails(payload);
  };

  const metricsText = (metrics = {}) => {
    const count = Number(metrics.resolved_trades || 0);
    if (!count) return "Historia: brak zakończonych transakcji dziennych";
    const winRate = metrics.win_rate == null ? "—" : `${Math.round(Number(metrics.win_rate) * 100)}%`;
    const averageR = metrics.average_r == null ? "—" : `${Number(metrics.average_r).toFixed(2)}R`;
    return `Historia: ${count} zakończonych · skuteczność ${winRate} · średnio ${averageR}`;
  };

  const renderDetails = (payload) => {
    const details = root.querySelector("[data-gpw-details]");
    const body = root.querySelector("[data-gpw-details-body]");
    const sections = [];
    const scores = payload.selection?.scores || {};
    const scoreRows = Object.entries(scoreLabels)
      .filter(([key]) => Number.isFinite(Number(scores[key])))
      .map(([key, label]) => `<div><span>${label}</span><b>${Number(scores[key]).toFixed(1)}/100</b></div>`)
      .join("");
    if (scoreRows) {
      sections.push(`<section><h3>Skład oceny</h3><div class="gpw-pick-detail-grid">${scoreRows}</div></section>`);
    }

    const metrics = payload.metrics || {};
    const resolved = Number(metrics.resolved_trades || 0);
    if (resolved > 0) {
      const winRate = metrics.win_rate == null ? "—" : `${Math.round(Number(metrics.win_rate) * 100)}%`;
      const averageReturn = metrics.average_return_percent == null ? "—" : `${Number(metrics.average_return_percent).toFixed(2)}%`;
      const averageR = metrics.average_r == null ? "—" : `${Number(metrics.average_r).toFixed(2)}R`;
      sections.push(`<section><h3>Wyniki zakończonych transakcji</h3><div class="gpw-pick-detail-grid">
        <div><span>Liczba</span><b>${resolved}</b></div>
        <div><span>Skuteczność</span><b>${winRate}</b></div>
        <div><span>Średni wynik</span><b>${averageReturn}</b></div>
        <div><span>Średnio względem ryzyka</span><b>${averageR}</b></div>
      </div></section>`);
    }

    const learning = payload.learning || {};
    if (learning.method) {
      const learned = Number(learning.resolved_trades || 0);
      const minimum = Number(learning.minimum_sample || 0);
      const state = learning.adaptation_active
        ? `Aktywna · próba ${learned}`
        : `Zbieranie próby · ${learned}/${minimum || "—"}`;
      const last = learning.last_lesson?.lesson
        ? `<p><b>Ostatni wniosek:</b> ${escapeHtml(learning.last_lesson.lesson)}</p>`
        : "<p>Brak jeszcze rozliczonej transakcji do nauki.</p>";
      sections.push(`<section><h3>Pętla uczenia</h3><p>${escapeHtml(state)}. Wagi strategii pozostają zamrożone; historia może tylko korygować 10% składnik skuteczności układu.</p>${last}</section>`);
    }

    const outcome = payload.outcome || {};
    if (outcome.status === "RESOLVED") {
      const result = outcome.activated
        ? `${Number(outcome.return_percent || 0).toFixed(2)}%`
        : "Nie aktywowano";
      sections.push(`<section><h3>Rozliczenie ostatniego wyboru</h3><p>${escapeHtml(result)}</p></section>`);
    }

    details.hidden = sections.length === 0;
    if (!sections.length) {
      details.open = false;
      body.innerHTML = "";
      return;
    }
    body.innerHTML = sections.join("");
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
    renderDetails(payload);
  };

  const render = (payload) => {
    if (!payload || !labels[payload.decision]) throw new Error("Niepoprawny kontrakt danych");
    const today = warsawDate();
    const stale = payload.date !== today && isWarsawWeekday();
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
        locked: false,
        reason: "Brak dzisiaj wyboru.",
        metrics: {}
      });
    }
  };

  load();
})();
