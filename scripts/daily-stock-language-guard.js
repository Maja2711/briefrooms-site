(() => {
  "use strict";

  const lang = document.documentElement.lang === "en" ? "en" : "pl";
  const market = lang === "en" ? "gpw" : "us";
  const url = market === "gpw"
    ? "/data/investments/gpw_daily_pick.json"
    : "/data/investments/us_daily_stock.json";

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");

  const fallback = (selection) => {
    const name = String(selection?.name || selection?.ticker || selection?.symbol || (lang === "pl" ? "Wybrana spółka" : "The selected stock"));
    if (lang === "en") {
      return {
        thesis: `${name} is the active GPW Daily Trade selection after the highest validated ranking among eligible Polish-market candidates.`,
        why_now: "The selection combines relative momentum, market and sector context, liquidity, risk/reward, current-session confirmation and the strategy's historical evidence.",
        activation: "Enter only inside the stated entry zone and do not chase the price above its upper limit."
      };
    }
    return {
      thesis: `${name} jest aktywnym wyborem US Daily Stock po najwyższym zweryfikowanym rankingu wśród dopuszczonych kandydatów rynku USA.`,
      why_now: "Wybór łączy momentum relatywne, kontekst rynku i sektora, płynność, relację zysku do ryzyka, potwierdzenie bieżącej sesji oraz historyczne wyniki strategii dla rynku USA.",
      activation: "Wchodź wyłącznie w podanej strefie wejścia i nie goń ceny powyżej jej górnej granicy."
    };
  };

  const copy = (selection) => {
    const target = lang === "en" ? "en" : "pl";
    const localized = selection?.localized?.[target] || {};
    const fb = fallback(selection);
    return {
      thesis: String(localized.thesis || fb.thesis),
      why_now: String(localized.why_now || fb.why_now),
      activation: String(localized.activation || fb.activation)
    };
  };

  const apply = (payload) => {
    const selection = payload?.selection || {};
    if (!selection?.ticker && !selection?.symbol) return false;
    const card = document.querySelector(`.dsm-market-card[data-dsm-market="${market}"]`);
    if (!card) return false;
    const text = copy(selection);
    const thesis = card.querySelector(".dsm-thesis");
    const why = card.querySelector(".dsm-why");
    const activation = card.querySelector(".dsm-activation");
    if (thesis) thesis.innerHTML = `<b>${lang === "pl" ? "Teza" : "1–2 session thesis"}:</b> ${escapeHtml(text.thesis)}`;
    if (why) why.innerHTML = `<b>${lang === "pl" ? "Dlaczego teraz" : "Why now"}:</b> ${escapeHtml(text.why_now)}`;
    if (activation) {
      const valid = selection.valid_until
        ? ` ${lang === "pl" ? "Ważność planu" : "Plan valid through"}: ${escapeHtml(selection.valid_until)}.`
        : "";
      activation.innerHTML = `${escapeHtml(text.activation)}${valid}`;
    }
    card.querySelectorAll(".dsm-original-badge").forEach((node) => node.remove());
    return Boolean(thesis || why || activation);
  };

  const fetchPayload = async () => {
    const response = await fetch(`${url}?v=${Date.now()}`, {
      cache: "no-store",
      headers: { "Cache-Control": "no-cache" }
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  };

  const boot = async () => {
    let payload;
    try { payload = await fetchPayload(); } catch (_) { return; }
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      if (apply(payload) || attempts >= 80) window.clearInterval(timer);
    }, 250);
  };

  boot();
})();
