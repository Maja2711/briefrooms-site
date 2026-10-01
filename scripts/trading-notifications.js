(() => {
  "use strict";

  const PREF_KEY = "brTradingNotificationsV1";
  const CURSOR_KEY = "brTradingNotificationsCursorV1";
  const DEVICE_KEY = "brTradingNotificationsDeviceV1";
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
    backgroundAllowed: "Background Push jest aktywny — alert może przyjść także przy zamkniętej stronie BriefRooms.",
    denied: "Przeglądarka zablokowała powiadomienia. Zmień zgodę w ustawieniach witryny.",
    unsupported: "Ta przeglądarka nie obsługuje powiadomień systemowych.",
    backendError: "Powiadomienia lokalne działają, ale rejestracja Background Push nie powiodła się.",
    publicMode: "Dostęp: publiczny test BriefRooms.",
    foregroundNote: "Tryb awaryjny: gdy backend Background Push jest niedostępny, otwarta strona nadal sprawdza nowe zdarzenia.",
    backgroundNote: "Background Web Push: aktywny. Nie musisz trzymać otwartej strony BriefRooms.",
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
    backgroundAllowed: "Background Push is active — alerts can arrive even when BriefRooms is closed.",
    denied: "Notifications are blocked by the browser. Change the site permission to enable them.",
    unsupported: "This browser does not support system notifications.",
    backendError: "Local notifications work, but Background Push registration failed.",
    publicMode: "Access: public BriefRooms test.",
    foregroundNote: "Fallback mode: if Background Push is unavailable, an open BriefRooms page still checks for new events.",
    backgroundNote: "Background Web Push: active. You do not need to keep BriefRooms open.",
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

  function deviceId() {
    let id = localStorage.getItem(DEVICE_KEY);
    if (!id) {
      id = (crypto.randomUUID ? crypto.randomUUID() : "br-" + Date.now() + "-" + Math.random().toString(16).slice(2));
      localStorage.setItem(DEVICE_KEY, id);
    }
    return id;
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

  function backgroundReady(config) {
    const bg = config?.background_push || {};
    return bg.enabled === true && typeof bg.api_base === "string" && bg.api_base.startsWith("https://") &&
      typeof bg.public_vapid_key === "string" && bg.public_vapid_key.length > 40;
  }

  function base64UrlToUint8Array(base64String) {
    const padding = "=".repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = atob(base64);
    return Uint8Array.from([...rawData].map((char) => char.charCodeAt(0)));
  }

  async function serviceWorkerRegistration() {
    if (!("serviceWorker" in navigator)) throw new Error("service_worker_unsupported");
    return navigator.serviceWorker.register(SW_URL, { scope: "/" });
  }

  async function backendPost(config, functionName, payload) {
    const base = String(config.background_push.api_base || "").replace(/\/$/, "");
    const response = await fetch(base + "/" + functionName, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      mode: "cors",
      credentials: "omit",
    });
    if (!response.ok) throw new Error(functionName + ":" + response.status);
    return response.json();
  }

  async function syncBackgroundSubscription(config, prefs) {
    if (!backgroundReady(config) || !prefs.enabled || Notification.permission !== "granted") return false;
    const registration = await serviceWorkerRegistration();
    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: base64UrlToUint8Array(config.background_push.public_vapid_key),
      });
    }
    await backendPost(config, "trading-push-subscribe", {
      subscription: subscription.toJSON(),
      device_id: deviceId(),
      locale: lang,
      preferences: { channels: prefs.channels, events: prefs.events },
    });
    return true;
  }

  async function disableBackgroundSubscription(config) {
    if (!("serviceWorker" in navigator)) return;
    const registration = await navigator.serviceWorker.getRegistration("/");
    const subscription = await registration?.pushManager?.getSubscription();
    try {
      if (backgroundReady(config)) {
        await backendPost(config, "trading-push-unsubscribe", {
          endpoint: subscription?.endpoint || null,
          device_id: deviceId(),
        });
      }
    } catch (_) {}
    try { if (subscription) await subscription.unsubscribe(); } catch (_) {}
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

  async function pollEvents(config) {
    if (backgroundReady(config)) return;
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
      localStorage.setItem(CURSOR_KEY, String(events[events.length - 1].event_id || ""));
    } catch (_) {}
  }

  function checkbox(name, label, checked) {
    return `<label class="brn-check"><input type="checkbox" data-brn="${name}" ${checked ? "checked" : ""}><span>${label}</span></label>`;
  }

  async function mount() {
    if (!("localStorage" in window)) return;
    const config = await loadConfig();
    const prefs = loadPrefs();

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
      <p class="brn-note">${backgroundReady(config) ? t.backgroundNote : t.foregroundNote}</p>
    `;

    const target = document.querySelector(".switcher") || document.querySelector("main") || document.body;
    if (target.parentNode) target.parentNode.insertBefore(card, target.nextSibling);

    const status = card.querySelector("[data-brn-status]");
    const enableButton = card.querySelector('[data-brn-action="enable"]');

    card.addEventListener("change", async (ev) => {
      const input = ev.target.closest("input[data-brn]");
      if (!input) return;
      const next = loadPrefs();
      const key = input.getAttribute("data-brn");
      const [group, item] = key.split(".");
      if (group === "channel") next.channels[item] = input.checked;
      if (group === "event") next.events[item] = input.checked;
      savePrefs(next);
      status.textContent = t.saved;
      if (next.enabled && backgroundReady(config)) {
        try {
          await syncBackgroundSubscription(config, next);
          status.textContent = t.backgroundAllowed;
        } catch (_) {
          status.textContent = t.backendError;
        }
      }
    });

    enableButton.addEventListener("click", async () => {
      const next = loadPrefs();
      if (next.enabled) {
        next.enabled = false;
        savePrefs(next);
        await disableBackgroundSubscription(config);
        enableButton.textContent = t.enable;
        status.textContent = t.saved;
        return;
      }
      if (!("Notification" in window) || !("serviceWorker" in navigator)) {
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
      try {
        await serviceWorkerRegistration();
        if (backgroundReady(config)) {
          await syncBackgroundSubscription(config, next);
          status.textContent = t.backgroundAllowed;
        } else {
          status.textContent = t.allowed;
          await pollEvents(config);
        }
      } catch (_) {
        status.textContent = backgroundReady(config) ? t.backendError : t.allowed;
      }
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
      try { await serviceWorkerRegistration(); } catch (_) {}
      await showNative(t.testTitle, t.testBody, { event_id: "briefrooms-test" });
    });

    try {
      await serviceWorkerRegistration();
      if (prefs.enabled && Notification.permission === "granted" && backgroundReady(config)) {
        const ok = await syncBackgroundSubscription(config, prefs);
        if (ok) status.textContent = t.backgroundAllowed;
      }
    } catch (_) {}

    await pollEvents(config);
    if (!backgroundReady(config)) {
      const interval = Math.max(30, Number(config.poll_interval_seconds || 60)) * 1000;
      window.setInterval(() => pollEvents(config), interval);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
