(() => {
  "use strict";
  const root = document.getElementById("eurusd-abc-lab-pl-root");
  if (!root) return;

  const hasNum = value => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
  const esc = value => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const num = (value, digits=2) => hasNum(value)
    ? Number(value).toLocaleString("pl-PL", {minimumFractionDigits:digits, maximumFractionDigits:digits}) : "—";
  const pct = value => hasNum(value) ? `${num(Number(value) * 100, 1)}%` : "—";
  const bps = value => hasNum(value) ? `${Number(value) > 0 ? "+" : ""}${num(value, 2)} bp` : "—";
  const price = value => hasNum(value) ? num(value, 5) : "—";
  const mins = value => hasNum(value) ? `${num(value, 0)} min` : "—";
  const date = value => {
    if (!value) return "—";
    const d = new Date(value);
    return Number.isNaN(d.valueOf()) ? esc(value) : d.toLocaleString("pl-PL", {dateStyle:"short", timeStyle:"short"});
  };

  const HELP = {
    score: ["Score", "Skala 0–100 używana do decyzji. Dla A i C: ≥60 LONG, ≤40 SHORT, środek to FLAT. B od PR25 ma osobną kalibrację swojego surowego Belief supportu, bo jego rozkład jest dużo ciaśniejszy niż techniki."],
    confidence: ["Confidence", "Siła decyzji wewnątrz danego modelu. To NIE jest skalibrowane prawdopodobieństwo zysku."],
    reference: ["Reference", "Cena EUR/USD zamrożona w chwili capture. Od niej liczymy wyniki forward i wirtualne Entry. To nie jest executable bid/ask."],
    forward: ["Wynik forward", "Ruch od reference do ceny dokładnie po 30m, 1h, 2h, 4h lub 24h. To test kierunku w punkcie czasu, niezależny od TP/SL."],
    hitrate: ["Hit rate", "Odsetek dojrzałych sygnałów LONG/SHORT, które miały właściwy znak na danym horyzoncie. FLAT nie jest trafieniem ani pudłem."],
    meansignal: ["Średni wynik sygnału", "Średni podpisany wynik w bp tylko dla sygnałów LONG/SHORT. Dodatni = przeciętnie zgodny kierunek, ujemny = przeciętnie przeciwny."],
    decisionrate: ["Sygnały / dojrzałe", "Ile razy silnik wydał LONG/SHORT wobec liczby capture, dla których dany horyzont już dojrzał. Pokazuje aktywność modelu."],
    entry: ["Entry", "Cena reference z chwili wygenerowania wirtualnego LONG/SHORT. Dla FLAT nie ma Entry, dlatego pokazujemy —, nie 0,00000."],
    sl: ["SL", "Stop Loss wirtualnej pozycji. Risk = max(1,35 × ATR26 z 30m, 0,27% ceny)."],
    tp: ["TP", "Take Profit wirtualnej pozycji, ustawiony 1,8R od Entry."],
    exit: ["Exit", "Cena faktycznego wirtualnego zamknięcia: TP, SL albo cena przy TIME EXIT 24h."],
    mfe: ["MFE", "Maximum Favorable Excursion: największy ruch na korzyść pozycji przed jej zamknięciem, w bp."],
    mae: ["MAE", "Maximum Adverse Excursion: największy ruch przeciw pozycji przed jej zamknięciem, w bp."],
    firsttouch: ["First touch", "Który poziom został osiągnięty pierwszy: TP albo SL, oraz po ilu minutach. TP i SL w tej samej świecy 1m = AMBIGUOUS."],
    realized: ["Wynik", "Zrealizowany wynik wirtualnej pozycji w bp po TP, SL lub 24h. Dla pozycji otwartej jeszcze go nie ma."],
    openclosed: ["Otwarte / zamknięte", "Liczba prospektywnych wirtualnych pozycji nadal monitorowanych oraz już zakończonych."],
    tpsl: ["TP / SL / 24h", "Liczba pozycji zamkniętych kolejno przez Take Profit, Stop Loss i wyjście czasowe po 24h."],
    winrate: ["Win rate", "Odsetek zamkniętych wirtualnych pozycji z dodatnim wynikiem. Jeśli nie ma żadnej zamkniętej pozycji, statystyka jest —, a nie 0%."],
    meanresult: ["Średni wynik", "Średni zrealizowany wynik w bp wyłącznie z zamkniętych wirtualnych pozycji. Brak zamkniętych = —."],
    meantime: ["Średni czas", "Średni czas od wygenerowania sygnału do pierwszego TP/SL. TIME EXIT bez wcześniejszego touch nie jest udawanym first-touch."],
    history: ["Historia", "Każdy capture jest zachowany. Najnowszy może jeszcze mieć OCZEKUJE, ale niżej widzisz starsze capture i ich już rozliczone 30m/1h/2h/4h/24h."],
  };
  const help = (label, key) => `<button type="button" class="abc-help-trigger" data-help="${esc(key)}">${esc(label)} <span>ⓘ</span></button>`;

  const directionClass = value => value === "LONG" ? "abc-long" : value === "SHORT" ? "abc-short" : "abc-flat";
  const outcomeMark = value => value === true ? "✓" : value === false ? "✕" : "—";
  const touchLabel = value => ({TAKE_PROFIT:"TP",STOP_LOSS:"SL",TIME_EXIT_24H:"TIME 24H",AMBIGUOUS_SAME_1M_BAR:"TP+SL / 1m"}[value] || (value ? esc(value) : "—"));
  const tradeStatus = row => {
    if (!row) return "—";
    if (row.status === "OPEN") return "W TRAKCIE";
    if (row.status === "NO_TRADE") return "BRAK POZYCJI";
    if (row.status === "AMBIGUOUS") return "NIEJEDNOZNACZNE";
    if (row.status === "NOT_TRACKED_PRE_V13") return "PRZED PR24";
    if (row.status === "UNAVAILABLE") return "NIEDOSTĘPNE";
    if (row.status === "CLOSED") return row.exit_reason ? `ZAMKNIĘTA · ${touchLabel(row.exit_reason)}` : "ZAMKNIĘTA";
    return esc(row.status || "—");
  };

  function currentArm(arm) {
    const calibration = arm.arm_id === "B" && hasNum(arm.raw_score)
      ? `<small class="abc-calibration">surowy Belief: ${num(arm.raw_score,1)} → score decyzyjny ${num(arm.score,1)}</small>` : "";
    return `<article class="abc-arm">
      <div class="abc-arm-head"><span>${esc(arm.arm_id)} · ${esc(arm.label_pl)}</span><b class="${directionClass(arm.direction)}">SYGNAŁ ${esc(arm.direction)}</b></div>
      <div class="abc-score">${arm.available ? `${num(arm.score,1)}<small>/100</small>` : "NIEDOSTĘPNY"}</div>
      <p>${help("confidence", "confidence")}: <b>${arm.available ? pct(arm.confidence) : "—"}</b></p>${calibration}
    </article>`;
  }

  function latestOutcomeCell(row) {
    if (!row || !row.available) return `<span class="abc-muted">—</span>`;
    const cls = hasNum(row.signed_return_bps) && Number(row.signed_return_bps) >= 0 ? "abc-positive" : "abc-negative";
    return `<b class="${cls}">${bps(row.signed_return_bps)}</b> <span>${outcomeMark(row.directional_correct)}</span>`;
  }

  function cumulativeCell(row) {
    if (!row) return "—";
    return `<div class="abc-cum-cell">
      <b>${row.hit_rate == null ? "—" : pct(row.hit_rate)}</b><span>hit rate</span>
      <b>${bps(row.mean_signed_return_bps_signal_only)}</b><span>śr. sygnał</span>
      <small>${Number(row.signals || 0)} sygnałów / ${Number(row.matured_captures || 0)} dojrzałych</small>
    </div>`;
  }

  function tradeRow(armId, row) {
    const signal = row?.direction || "UNAVAILABLE";
    const terminal = row?.status === "CLOSED" || row?.status === "AMBIGUOUS";
    const resultClass = hasNum(row?.realized_bps) && Number(row.realized_bps) >= 0 ? "abc-positive" : "abc-negative";
    const firstTouch = row?.first_touch ? `${touchLabel(row.first_touch)}${hasNum(row.minutes_to_first_touch) ? ` · ${mins(row.minutes_to_first_touch)}` : ""}` : "—";
    return `<tr>
      <td><b>${esc(armId)}</b></td><td><b class="${directionClass(signal)}">${esc(signal)}</b></td>
      <td>${price(row?.entry_price)}</td><td>${price(row?.stop_price)}</td><td>${price(row?.target_price)}</td>
      <td>${price(row?.exit_price)}</td><td><b>${tradeStatus(row)}</b></td>
      <td>${terminal ? bps(row?.mfe_bps) : "—"}</td><td>${terminal ? bps(row?.mae_bps) : "—"}</td>
      <td>${firstTouch}</td><td>${hasNum(row?.realized_bps) ? `<b class="${resultClass}">${bps(row.realized_bps)}</b>` : "—"}</td>
    </tr>`;
  }

  function tradeCumulativeRow(armId, row) {
    row = row || {};
    const closed = Number(row.closed_trades || 0);
    return `<tr>
      <td><b>${esc(armId)}</b></td><td>${Number(row.signals || 0)}</td>
      <td>${Number(row.open_trades || 0)} / ${closed}</td>
      <td>${Number(row.take_profit || 0)} / ${Number(row.stop_loss || 0)} / ${Number(row.time_exit_24h || 0)}</td>
      <td>${Number(row.ambiguous_same_1m_bar || 0)}</td>
      <td>${closed ? pct(row.win_rate) : `<span class="abc-muted">— · brak zamkniętych</span>`}</td>
      <td>${closed ? bps(row.mean_realized_bps) : "—"}</td>
      <td>${closed ? `${bps(row.mean_mfe_bps)} / ${bps(row.mean_mae_bps)}` : "—"}</td>
      <td>${hasNum(row.mean_minutes_to_first_touch) ? mins(row.mean_minutes_to_first_touch) : "—"}</td>
    </tr>`;
  }

  function compactArm(arm) {
    if (!arm) return "—";
    return `<b class="${directionClass(arm.direction)}">${esc(arm.direction || "—")}</b>${hasNum(arm.score) ? ` <small>${num(arm.score,1)}</small>` : ""}`;
  }

  function forwardHistoryCell(row) {
    if (!row || row.status !== "RESOLVED") return `<span class="abc-muted">OCZEKUJE</span>`;
    const marks = ["A","B","C"].map(k => `${k}${outcomeMark(row.arms?.[k]?.directional_correct)}`).join(" ");
    const cls = hasNum(row.raw_return_bps) && Number(row.raw_return_bps) >= 0 ? "abc-positive" : "abc-negative";
    return `<b class="${cls}">${bps(row.raw_return_bps)}</b><small>${marks}</small>`;
  }

  function historyRow(row) {
    return `<tr>
      <td>${date(row.signal_generated_at)}</td><td>${price(row.reference_price)}</td>
      <td>${compactArm(row.arms?.A)}</td><td>${compactArm(row.arms?.B)}</td><td>${compactArm(row.arms?.C)}</td>
      ${["30m","60m","120m","240m","1440m"].map(k => `<td class="abc-forward-history">${forwardHistoryCell(row.horizons?.[k])}</td>`).join("")}
    </tr>`;
  }

  function virtualHistoryRows(history) {
    const rows = [];
    for (const capture of history || []) {
      const vt = capture.virtual_trade || {};
      for (const armId of ["A","B","C"]) {
        const row = vt.arms?.[armId];
        if (!row || !row.tracked) continue;
        const cls = hasNum(row.realized_bps) && Number(row.realized_bps) >= 0 ? "abc-positive" : "abc-negative";
        rows.push(`<tr>
          <td>${date(capture.signal_generated_at)}</td><td><b>${armId}</b></td><td><b class="${directionClass(row.direction)}">${esc(row.direction)}</b></td>
          <td>${price(row.entry_price)}</td><td>${price(row.exit_price)}</td><td>${tradeStatus(row)}</td>
          <td>${hasNum(row.realized_bps) ? `<b class="${cls}">${bps(row.realized_bps)}</b>` : "—"}</td>
          <td>${hasNum(row.mfe_bps) ? bps(row.mfe_bps) : "—"}</td><td>${hasNum(row.mae_bps) ? bps(row.mae_bps) : "—"}</td>
          <td>${row.first_touch ? `${touchLabel(row.first_touch)}${hasNum(row.minutes_to_first_touch) ? ` · ${mins(row.minutes_to_first_touch)}` : ""}` : "—"}</td>
        </tr>`);
      }
    }
    return rows.join("") || `<tr><td colspan="10" class="abc-muted">Brak prospektywnych wirtualnych pozycji w historii.</td></tr>`;
  }

  function render(payload) {
    const latest = payload.latest || {};
    const arms = latest.arms || {};
    const horizons = latest.horizons || {};
    const comparison = payload.comparison || {};
    const virtualTrade = latest.virtual_trade || {};
    const tradeArms = virtualTrade.arms || {};
    const tradeComparison = payload.trade_comparison?.arms || {};
    const history = Array.isArray(payload.history) ? payload.history : [];
    const ordered = ["30m","60m","120m","240m","1440m"];

    root.innerHTML = `<article class="abc-lab">
      <div class="abc-title-row"><div><span class="abc-eyebrow">PR25 · EUR/USD</span><h3>A/B/C Research Lab</h3><p>Porównujemy jakość kierunku i realną ścieżkę wirtualnej pozycji. Najnowszy capture jest na górze, pełna historia niżej.</p></div><span class="abc-chip">LIVE SHADOW</span></div>
      <div class="abc-boundary">Research Lab nie wykonuje transakcji. Aktywny Daily EUR/USD ma od PR25 własny, niezależnie przeliczany techniczny fallback A.</div>
      <div id="abc-help-box" class="abc-help-box" hidden><button type="button" class="abc-help-close" aria-label="Zamknij">×</button><b></b><p></p></div>
      <div class="abc-capture-meta"><span>Sygnał wygenerowany: <b>${date(latest.signal_generated_at)}</b></span><span>Obserwacja rynku: <b>${date(latest.market_observed_at)}</b></span><span>${help("Reference", "reference")}: <b>${price(latest.reference_price)}</b></span></div>
      <div class="abc-arms">${["A","B","C"].map(key => currentArm(arms[key] || {arm_id:key,label_pl:key,direction:"UNAVAILABLE",available:false})).join("")}</div>

      <div class="abc-explain"><b>1. Wynik punktowy</b><span>${help("Co mierzymy?", "forward")} Cena dokładnie po 30m / 1h / 2h / 4h / 24h; wcześniejszy TP/SL nie zmienia tego testu.</span></div>
      <h4>Bieżący capture — wyniki forward</h4>
      <div class="abc-table-wrap"><table class="abc-table"><thead><tr><th>Horyzont</th><th>Status</th><th>Ruch EUR/USD</th><th>A · Techniczny</th><th>B · Belief</th><th>C · Hybrydowy</th></tr></thead><tbody>
      ${ordered.map(key => { const row=horizons[key]||{}; return `<tr><td><b>${esc(row.label || key)}</b></td><td>${row.status === "RESOLVED" ? "ROZLICZONY" : "OCZEKUJE"}</td><td>${row.status === "RESOLVED" ? bps(row.raw_return_bps) : "—"}</td><td>${latestOutcomeCell(row.arms?.A)}</td><td>${latestOutcomeCell(row.arms?.B)}</td><td>${latestOutcomeCell(row.arms?.C)}</td></tr>`; }).join("")}
      </tbody></table></div>

      <h4>Narastające porównanie kierunku</h4>
      <div class="abc-stat-help"><span>${help("Hit rate", "hitrate")}</span><span>${help("Śr. sygnał", "meansignal")}</span><span>${help("Sygnały / dojrzałe", "decisionrate")}</span></div>
      <div class="abc-table-wrap"><table class="abc-table abc-cumulative"><thead><tr><th>Horyzont</th><th>A · Techniczny</th><th>B · Belief</th><th>C · Hybrydowy</th></tr></thead><tbody>
      ${ordered.map(key => { const row=comparison[key]||{}; return `<tr><td><b>${esc(row.label || key)}</b></td><td>${cumulativeCell(row.A)}</td><td>${cumulativeCell(row.B)}</td><td>${cumulativeCell(row.C)}</td></tr>`; }).join("")}
      </tbody></table></div>

      <div class="abc-explain abc-trade-explain"><b>2. Wirtualna pozycja</b><span>LONG/SHORT dostaje Entry/SL/TP i jest monitorowany na 1m do TP, SL albo 24h.</span></div>
      <h4>Wirtualna ścieżka pozycji — TP / SL / MFE / MAE</h4>
      <div class="abc-risk"><span>Risk: max(1,35 × ATR26 30m, 0,27%)</span><span>TP: 1,8R</span><span>Max: 24h</span><span>Monitoring: 1m</span></div>
      <div class="abc-table-wrap"><table class="abc-table abc-trade-table"><thead><tr>
        <th>Silnik</th><th>Sygnał</th><th>${help("Entry","entry")}</th><th>${help("SL","sl")}</th><th>${help("TP","tp")}</th><th>${help("Exit","exit")}</th><th>Status</th><th>${help("MFE","mfe")}</th><th>${help("MAE","mae")}</th><th>${help("First touch","firsttouch")}</th><th>${help("Wynik","realized")}</th>
      </tr></thead><tbody>${["A","B","C"].map(key => tradeRow(key, tradeArms[key] || {})).join("")}</tbody></table></div>
      <p class="abc-method-note">Dla otwartych pozycji wynik, Exit i finalne MFE/MAE pozostają „—”. Nie zamieniamy braku danych na zero.</p>

      <h4>Narastające wyniki wirtualnych pozycji</h4>
      <div class="abc-table-wrap"><table class="abc-table abc-trade-cumulative"><thead><tr>
        <th>Silnik</th><th>Sygnały</th><th>${help("Otwarte / zamknięte","openclosed")}</th><th>${help("TP / SL / 24h","tpsl")}</th><th>Amb.</th><th>${help("Win rate","winrate")}</th><th>${help("Śr. wynik","meanresult")}</th><th>${help("Śr. MFE / MAE","mfe")}</th><th>${help("Śr. czas","meantime")}</th>
      </tr></thead><tbody>${["A","B","C"].map(key => tradeCumulativeRow(key, tradeComparison[key])).join("")}</tbody></table></div>

      <details class="abc-history-details" open><summary>3. ${help("Historia sygnałów i wyników forward", "history")} · ${history.length} ostatnich capture</summary>
        <div class="abc-table-wrap"><table class="abc-table abc-history-table"><thead><tr><th>Czas</th><th>Reference</th><th>A</th><th>B</th><th>C</th><th>30m</th><th>1h</th><th>2h</th><th>4h</th><th>24h</th></tr></thead><tbody>
        ${history.map(historyRow).join("") || `<tr><td colspan="10" class="abc-muted">Historia pojawi się po kolejnej publikacji PR25.</td></tr>`}
        </tbody></table></div>
      </details>

      <details class="abc-history-details" open><summary>4. Historia wirtualnych pozycji — ceny otwarcia i zamknięcia</summary>
        <div class="abc-table-wrap"><table class="abc-table abc-history-table"><thead><tr><th>Czas</th><th>Silnik</th><th>Sygnał</th><th>Entry</th><th>Exit</th><th>Status</th><th>Wynik</th><th>MFE</th><th>MAE</th><th>First touch</th></tr></thead><tbody>
        ${virtualHistoryRows(history)}
        </tbody></table></div>
      </details>

      <p class="abc-foot">Próba: <b>${Number(payload.sample?.captures || 0)}</b> capture · ${esc(payload.engine_version || "")}</p>
    </article>`;
  }

  root.addEventListener("click", event => {
    const close = event.target.closest?.(".abc-help-close");
    if (close) { const box=root.querySelector("#abc-help-box"); if (box) box.hidden=true; return; }
    const trigger = event.target.closest?.("[data-help]");
    if (!trigger) return;
    event.preventDefault(); event.stopPropagation();
    const item = HELP[trigger.dataset.help];
    const box = root.querySelector("#abc-help-box");
    if (!item || !box) return;
    box.querySelector("b").textContent = item[0];
    box.querySelector("p").textContent = item[1];
    box.hidden = false;
    box.scrollIntoView({behavior:"smooth", block:"nearest"});
  });

  const style = document.createElement("style");
  style.textContent = `
    .abc-help-trigger{appearance:none;border:0;background:transparent;color:inherit;font:inherit;font-weight:inherit;padding:0;cursor:pointer;text-decoration:underline;text-decoration-style:dotted;text-underline-offset:3px}.abc-help-trigger span{color:#72d3ff;font-size:.9em}.abc-help-box{position:relative;margin:12px 0;padding:14px 42px 14px 15px;border:1px solid rgba(114,211,255,.38);border-radius:12px;background:#102538;color:#eaf6ff}.abc-help-box b{color:#7dd7ff}.abc-help-box p{margin:6px 0 0;color:#bad0e1;line-height:1.55}.abc-help-close{position:absolute;right:10px;top:8px;border:0;background:transparent;color:#fff;font-size:22px;cursor:pointer}.abc-stat-help{display:flex;flex-wrap:wrap;gap:14px;margin:0 0 8px;color:#9db6ca;font-size:11px}.abc-calibration{display:block;margin-top:5px;color:#caa8ff}.abc-history-details{margin-top:24px;border-top:1px solid rgba(255,255,255,.1);padding-top:12px}.abc-history-details summary{font-weight:900;color:#f3f8ff;cursor:pointer;font-size:14px}.abc-forward-history small{display:block;margin-top:3px;color:#91a8bb;white-space:nowrap}.abc-history-table td{vertical-align:top;white-space:nowrap}.abc-muted{color:#859caf!important}
  `;
  document.head.appendChild(style);

  fetch("/data/investments/eurusd_abc_public_pl.json?v=" + Date.now(), {cache:"no-store"})
    .then(response => { if (!response.ok) throw new Error(String(response.status)); return response.json(); })
    .then(render)
    .catch(() => { root.innerHTML = `<article class="abc-lab"><h3>A/B/C Research Lab</h3><p class="abc-foot">Nie udało się wczytać publicznej projekcji A/B/C.</p></article>`; });
})();
