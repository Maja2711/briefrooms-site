(() => {
  "use strict";

  const PREF_KEY = "brTradingNotificationsV1";
  const CURSOR_KEY = "brTradingNotificationsCursorV1";
  const CONFIG_URL = "/data/notifications/trading-notification-config.json";
  const EVENTS_URL = "/data/notifications/trading-events.json";
  const SW_URL = "/br-trading-sw.js";

  const lang = (document.documentElement.lang || "pl").toLowerCase().startsWith("pl") ? "pl" : "en";
  const t = lang === "pl" ? {
    title: "Powiadomienia tradingowe",
    intro: "Wybierz, z których części Trading Room chcesz otrzymywać alerty o otwarciu i zamknięciu pozycji.",
    enable: "Włącz powiadomienia tradingowe",
    disable: "Wyłącz na tym urządzeniu",
    test: "Wyślij test",
    daily: "Daily Trading",
    weekly: "Weekly Trading",
    stock: "Stock Trading",
    open: "Otwarcie pozycji",
    close: "Zamknięcie pozycji",
    saved: "Ustawienia zapisane.",
    allowed: "Powiadomienia są włączone na tym urządzeniu.",
    denied: "Przeglądarka zablokowała powiadomienia. Zmień zgodę w ustawieniach witryny.",
    unsupported: "Ta przeglądarka nie obsługuje powiadomień systemowych.",
    publicMode: "Dostęp: publiczny test BriefRooms.",
    foregroundNote: "MVP: alerty działają, gdy BriefRooms jest otwarte w przeglądarce. Pełny push przy zamkniętej stronie zostanie aktywowany po podłączeniu bezpiecznego backendu push.",
    testTitle: "BriefRooms · test",
    testBody: "Powiadomienia tradingowe działają na tym urządzeniu.",
    openLabel: "OTWARTO",
    closeLabel: "ZAMKNIĘTO",
  } : {
    title: "Trading notifications",
    intro: "Choose which Trading Room areas should alert you when a position opens or closes.",
    enable: "Enable trading notifications",
    disable: "Disable on this device",
    test: "Send test",
    daily: "Daily Trading",
    weekly: "Weekly Trading",
    stock: "Stock Trading",
    open: "Position opened",
    close: "Position closed",
    saved: "Settings saved.",
    allowed: "Notifications are enabled on this device.",
    denied: "Notifications are blocked by the browser. Change the site permission to enable them.",
    unsupported: "This browser does not support system notifications.",
    publicMode: "Access: public BriefRooms test.",
    foregroundNote: "MVP: alerts work while BriefRooms is open in the browser. Full push with the site closed will activate after a secure push backend is connected.",
    testTitle: "BriefRooms · test",
    testBody: "Trading notifications work on this device.",
    openLabel: "OPENED",
    closeLabel: "CLOSED",
  };

  const defaults = {
    enabled: false,
    channels: { daily: true, weekly: true, stock: true },
    events: { open: true, close: true },
  };

  function loadPrefs() {
    try {
      const parsed = JSON.parse(localStorage.getItem(PREF_KEY) || "null");
      return {
        ...defaults,
        ...(parsed || {}),
        channels: { ...defaults.channels, ...((parsed || {}).channels || {}) },
        events: { ...defaults.events, ...((parsed || {}).events || {}) },
      };
    } catch (_) {
      return structuredClone(defaults);
    }
  }

  function savePrefs(prefs) {
    localStorage.setItem(PREF_KEY, JSON.stringify(prefs));
  }

  async function loadConfig() {
    try {
      const response = await fetch(CONFIG_URL + "?v=" + Date.now(), { cache: "no-store" });
      if (!response.ok) throw new Error("config");
      return await response.json();
    } catch (_) {
      return { access_mode: "PUBLIC", poll_interval_seconds: 60, background_push: { enabled: false } };
    }
  }

  function engineName(engine) {
    return engine === "daily" ? t.daily : engine === "weekly" ? t.weekly : t.stock;
  }

  function notificationText(event) {
    const action = event.event_type === "OPEN" ? t.openLabel : t.closeLabel;
    const dir = event.direction ? " · " + event.direction : "";
    const entry = event.entry != null ? " @ " + event.entry : "";
    return action + " · " + (event.instrument || "") + dir + entry;
  }

  async function showNative(title, body, data) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    try {
      const registration = await navigator.serviceWorker?.getRegistration("/");
      if (registration) {
        await registration.showNotification(title, {
          body,
          icon: "/assets/favicon.svg",
          badge: "/assets/favicon.svg",
          tag: data?.event_id || undefined,
          data: { url: location.href, ...(data || {}) },
        });
        return;
      }
    } catch (_) {}
    new Notification(title, { body, icon: "/assets/favicon.svg", tag: data?.event_id || undefined });
  }

  function eventAllowed(event, prefs) {
    const channel = String(event.engine || "").toLowerCase();
    const type = String(event.event_type || "").toLowerCase();
    return !!prefs.enabled && !!prefs.channels[channel] && !!prefs.events[type];
  }

  async function pollEvents() {
    const prefs = loadPrefs();
    if (!prefs.enabled || Notification.permission !== "granted") return;
    try {
      const response = await fetch(EVENTS_URL + "?v=" + Date.now(), { cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      const events = Array.isArray(payload.events) ? payload.events : [];
      if (!events.length) return;

      const cursor = localStorage.getItem(CURSOR_KEY);
      if (!cursor) {
        localStorage.setItem(CURSOR_KEY, String(events[events.length - 1].event_id || ""));
        return;
      }
      const idx = events.findIndex((e) => String(e.event_id || "") === cursor);
      const unseen = idx >= 0 ? events.slice(idx + 1) : [];
      for (const event of unseen) {
        if (eventAllowed(event, prefs)) {
          await showNative("BriefRooms · " + engineName(event.engine), notificationText(event), event);
        }
      }
      if (events.length) localStorage.setItem(CURSOR_KEY, String(events[events.length - 1].event_id || ""));
    } catch (_) {}
  }

  function checkbox(name, label, checked) {
    return `<label class="brn-check"><input type="checkbox" data-brn="${name}" ${checked ? "checked" : ""}><span>${label}</span></label>`;
  }

  async function mount() {
    if (!("localStorage" in window)) return;
    const prefs = loadPrefs();
    const config = await loadConfig();

    const card = document.createElement("section");
    card.className = "brn-card";
    card.setAttribute("aria-label", t.title);
    card.innerHTML = `
      <div class="brn-head">
        <div><span class="brn-kicker">BriefRooms Alerts</span><h2>${t.title}</h2><p>${t.intro}</p></div>
        <button type="button" class="brn-primary" data-brn-action="enable">${prefs.enabled ? t.disable : t.enable}</button>
      </div>
      <div class="brn-grid">
        <fieldset><legend>Trading Room</legend>
          ${checkbox("channel.daily", t.daily, prefs.channels.daily)}
          ${checkbox("channel.weekly", t.weekly, prefs.channels.weekly)}
          ${checkbox("channel.stock", t.stock, prefs.channels.stock)}
        </fieldset>
        <fieldset><legend>${lang === "pl" ? "Zdarzenia" : "Events"}</legend>
          ${checkbox("event.open", t.open, prefs.events.open)}
          ${checkbox("event.close", t.close, prefs.events.close)}
        </fieldset>
      </div>
      <div class="brn-foot">
        <span data-brn-status>${config.access_mode === "PUBLIC" ? t.publicMode : ""}</span>
        <button type="button" class="brn-test" data-brn-action="test">${t.test}</button>
      </div>
      <p class="brn-note">${t.foregroundNote}</p>
    `;

    const target = document.querySelector(".switcher") || document.querySelector("main") || document.body;
    if (target.parentNode) target.parentNode.insertBefore(card, target.nextSibling);

    const status = card.querySelector("[data-brn-status]");
    const enableButton = card.querySelector('[data-brn-action="enable"]');

    card.addEventListener("change", (ev) => {
      const input = ev.target.closest("input[data-brn]");
      if (!input) return;
      const next = loadPrefs();
      const key = input.getAttribute("data-brn");
      const [group, item] = key.split(".");
      if (group === "channel") next.channels[item] = input.checked;
      if (group === "event") next.events[item] = input.checked;
      savePrefs(next);
      status.textContent = t.saved;
    });

    enableButton.addEventListener("click", async () => {
      const next = loadPrefs();
      if (next.enabled) {
        next.enabled = false;
        savePrefs(next);
        enableButton.textContent = t.enable;
        status.textContent = t.saved;
        return;
      }
      if (!("Notification" in window)) {
        status.textContent = t.unsupported;
        return;
      }
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        status.textContent = t.denied;
        return;
      }
      next.enabled = true;
      savePrefs(next);
      enableButton.textContent = t.disable;
      status.textContent = t.allowed;
      try { await navigator.serviceWorker.register(SW_URL, { scope: "/" }); } catch (_) {}
      await pollEvents();
    });

    card.querySelector('[data-brn-action="test"]').addEventListener("click", async () => {
      if (!("Notification" in window)) {
        status.textContent = t.unsupported;
        return;
      }
      let permission = Notification.permission;
      if (permission === "default") permission = await Notification.requestPermission();
      if (permission !== "granted") {
        status.textContent = t.denied;
        return;
      }
      try { await navigator.serviceWorker.register(SW_URL, { scope: "/" }); } catch (_) {}
      await showNative(t.testTitle, t.testBody, { event_id: "briefrooms-test" });
    });

    try { await navigator.serviceWorker.register(SW_URL, { scope: "/" }); } catch (_) {}
    await pollEvents();
    const interval = Math.max(30, Number(config.poll_interval_seconds || 60)) * 1000;
    window.setInterval(pollEvents, interval);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
