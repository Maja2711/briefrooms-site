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
    test: "Wyślij",
    cancel: "Anuluj",
    settingsTitle: "Ustawienia powiadomień",
    daily: "Daily Trading",
    weekly: "Weekly Trading",
    stock: "Stock Trading",
    open: "Otwarcie pozycji",
    close: "Zamknięcie pozycji",
    saved: "Ustawienia zapisane.",
    allowed: "Powiadomienia są włączone na tym urządzeniu.",
    denied: "Przeglądarka zablokowała powiadomienia. Zmień zgodę w ustawieniach witryny.",
    unsupported: "Ta przeglądarka nie obsługuje powiadomień systemowych.",
    pushUnsupported: "To urządzenie nie obsługuje Web Push w tym trybie. Na iPhonie otwórz BriefRooms jako aplikację z ekranu początkowego.",
    pushTestFailed: "Urządzenie zostało zapisane, ale test Web Push nie dotarł. Odnawiam subskrypcję przy kolejnym otwarciu.",
    foregroundNote: "Background Web Push może dostarczać alerty także po zamknięciu strony. Ustawienia są zapisywane dla tego urządzenia.",
    testTitle: "BriefRooms · test",
    testBody: "Powiadomienia tradingowe działają na tym urządzeniu.",
    openLabel: "OTWARTO",
    closeLabel: "ZAMKNIĘTO",
    checking: "Sprawdzam połączenie PUSH z tym urządzeniem…",
    pushReady: "PUSH aktywny na tym urządzeniu. Dostawca nie gwarantuje wyświetlenia przez system.",
    pushMissing: "Brak aktywnej subskrypcji PUSH na tym urządzeniu.",
    pushRepairing: "Odnawiam subskrypcję PUSH…",
    pushRepaired: "Subskrypcja PUSH odnowiona.",
    pushUnverified: "Nie udało się potwierdzić połączenia z serwerem PUSH.",
    pushDisabled: "Alerty wyłączone na tym urządzeniu.",
  } : {
    title: "Trading notifications",
    intro: "Choose which Trading Room areas should alert you when a position opens or closes.",
    enable: "Enable trading notifications",
    disable: "Disable on this device",
    test: "Send",
    cancel: "Cancel",
    settingsTitle: "Notification settings",
    daily: "Daily Trading",
    weekly: "Weekly Trading",
    stock: "Stock Trading",
    open: "Position opened",
    close: "Position closed",
    saved: "Settings saved.",
    allowed: "Notifications are enabled on this device.",
    denied: "Notifications are blocked by the browser. Change the site permission to enable them.",
    unsupported: "This browser does not support system notifications.",
    pushUnsupported: "This device does not support Web Push in this mode. On iPhone, open BriefRooms as a Home Screen web app.",
    pushTestFailed: "The device was saved, but the Web Push test failed. The subscription will be repaired on the next visit.",
    foregroundNote: "Background Web Push can deliver alerts even after the page is closed. Preferences are stored for this device.",
    testTitle: "BriefRooms · test",
    testBody: "Trading notifications work on this device.",
    openLabel: "OPENED",
    closeLabel: "CLOSED",
    checking: "Checking this device's push connection…",
    pushReady: "Push subscription active on this device; OS display is not guaranteed.",
    pushMissing: "No active push subscription on this device.",
    pushRepairing: "Reconnecting push subscription…",
    pushRepaired: "Push subscription restored.",
    pushUnverified: "Could not verify the push server connection.",
    pushDisabled: "Alerts are disabled on this device.",
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

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = atob(base64);
    return Uint8Array.from([...rawData].map((char) => char.charCodeAt(0)));
  }
  function sameBytes(a, b) {
    if (!a || !b || a.length !== b.length) return false;
    for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false;
    return true;
  }


  async function getRegistration() {
    if (!("serviceWorker" in navigator)) return null;
    await navigator.serviceWorker.register(SW_URL, { scope: "/" });
    return navigator.serviceWorker.ready;
  }

  async function backgroundSubscriptionStatus(config, subscription) {
    const push = config?.background_push || {};
    if (!push.enabled || !push.api_base || !subscription?.endpoint) return null;
    const response = await fetch(push.api_base.replace(/\/$/, "") + "/subscription-status", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ endpoint: subscription.endpoint }),
    });
    if (!response.ok) return null;
    return response.json().catch(() => null);
  }

  async function testBackgroundSubscription(config, subscription) {
    const push = config?.background_push || {};
    if (!push.enabled || !push.api_base) throw new Error("background_push_unavailable");
    if (!subscription?.endpoint) throw new Error("subscription_missing");
    const response = await fetch(push.api_base.replace(/\/$/, "") + "/test-subscription", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ endpoint: subscription.endpoint }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const error = new Error(payload.error || "test_push_failed");
      error.status = response.status;
      error.pushStatus = Number(payload.status || 0) || null;
      throw error;
    }
    return response.json().catch(() => ({ ok: true }));
  }

  async function syncBackgroundSubscription(config, prefs) {
    const push = config?.background_push || {};
    if (!push.enabled || !push.api_base || !prefs.enabled || Notification.permission !== "granted") return null;
    if (!("PushManager" in window)) throw new Error("push_manager_unavailable");
    const registration = await getRegistration();
    if (!registration) return null;

    const keyResponse = await fetch(push.api_base.replace(/\/$/, "") + "/vapid-public-key", { cache: "no-store" });
    if (!keyResponse.ok) throw new Error("vapid_key_unavailable");
    const keyPayload = await keyResponse.json();
    if (!keyPayload.publicKey) throw new Error("vapid_key_missing");

    const expectedKey = urlBase64ToUint8Array(keyPayload.publicKey);
    let subscription = await registration.pushManager.getSubscription();

    // A PushSubscription is cryptographically bound to the VAPID/applicationServerKey
    // used when it was created. If the backend key changed in the past, silently repair
    // the device instead of leaving it subscribed with an unusable endpoint.
    if (subscription?.options?.applicationServerKey) {
      const currentKey = new Uint8Array(subscription.options.applicationServerKey);
      if (!sameBytes(currentKey, expectedKey)) {
        try {
          await fetch(push.api_base.replace(/\/$/, "") + "/unsubscribe", {
            method: "DELETE",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ endpoint: subscription.endpoint }),
          });
        } catch (_) {}
        try { await subscription.unsubscribe(); } catch (_) {}
        subscription = null;
      }
    }

    // If the backend has already discarded this endpoint (for example after
    // a 404/410 from the push provider), do not re-register the same dead
    // browser subscription. Recreate it locally so the provider issues a
    // fresh endpoint for this device.
    if (subscription) {
      try {
        const serverStatus = await backgroundSubscriptionStatus(config, subscription);
        if (serverStatus && serverStatus.registered === false) {
          try { await subscription.unsubscribe(); } catch (_) {}
          subscription = null;
        }
      } catch (_) {}
    }

    if (!subscription) {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: expectedKey,
      });
    }

    const response = await fetch(push.api_base.replace(/\/$/, "") + "/subscribe", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        subscription: subscription.toJSON(),
        preferences: { channels: prefs.channels, events: prefs.events },
        language: lang,
      }),
    });
    if (!response.ok) throw new Error("push_subscribe_failed");
    const server = await response.json().catch(() => ({}));
    try {
      localStorage.setItem("brTradingPushDeviceV1", JSON.stringify({
        id: server.id || null,
        endpoint_hash_known: Boolean(server.id),
        synced_at: new Date().toISOString(),
      }));
    } catch (_) {}
    return subscription;
  }

  async function removeBackgroundSubscription(config) {
    const push = config?.background_push || {};
    const registration = await getRegistration();
    const subscription = registration ? await registration.pushManager.getSubscription() : null;
    if (!subscription) return;
    if (push.api_base) {
      try {
        await fetch(push.api_base.replace(/\/$/, "") + "/unsubscribe", {
          method: "DELETE",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ endpoint: subscription.endpoint }),
        });
      } catch (_) {}
    }
    try { await subscription.unsubscribe(); } catch (_) {}
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

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
    })[ch]);
  }

  async function fetchNotificationEvents(config) {
    const push = config?.background_push || {};
    if (push.enabled && push.api_base) {
      try {
        const response = await fetch(push.api_base.replace(/\/$/, "") + "/inbox?limit=50", { cache: "no-store" });
        if (response.ok) {
          const payload = await response.json();
          return Array.isArray(payload.events) ? payload.events : [];
        }
      } catch (_) {}
    }
    try {
      const response = await fetch(EVENTS_URL + "?v=" + Date.now(), { cache: "no-store" });
      if (!response.ok) return [];
      const payload = await response.json();
      return Array.isArray(payload.events) ? [...payload.events].reverse() : [];
    } catch (_) {
      return [];
    }
  }

  function notificationText(event) {
    const action = event.event_type === "OPEN" ? t.openLabel : t.closeLabel;
    const dir = event.direction ? " · " + event.direction : "";
    const entry = event.entry != null ? " @ " + event.entry : "";
    return action + " · " + (event.instrument || "") + dir + entry;
  }

  function logicalEventId(event) {
    const parts = [event?.engine, event?.event_type, event?.position_id];
    return parts.every(Boolean) ? parts.join("|") : String(event?.event_id || "");
  }

  // The foreground recovery path and background SW use the SAME IndexedDB
  // event identity so a missed background push is recovered when the app opens,
  // without two alerts for the same trade.
  function claimOnDevice(identity) {
    if (!identity || !window.indexedDB) return Promise.resolve(true);
    return new Promise((resolve) => {
      let finished = false;
      const finish = (ok) => { if (!finished) { finished = true; resolve(ok); } };
      try {
        const req = indexedDB.open("briefrooms-trading-push-seen-v1", 1);
        req.onupgradeneeded = () => req.result.createObjectStore("events", { keyPath: "id" });
        req.onerror = () => finish(true);
        req.onsuccess = () => {
          const db = req.result;
          let exists = false;
          try {
            const tx = db.transaction("events", "readwrite");
            const add = tx.objectStore("events").add({ id: identity, seen_at: Date.now() });
            add.onerror = (e) => {
              if (add.error?.name === "ConstraintError") {
                exists = true;
                e.preventDefault();
                e.stopPropagation();
              }
            };
            tx.oncomplete = () => { db.close(); finish(!exists); };
            tx.onabort = () => { db.close(); finish(true); };
            tx.onerror = () => { db.close(); finish(true); };
          } catch (_) { db.close(); finish(true); }
        };
      } catch (_) { finish(true); }
    });
  }

  function releaseOnDevice(identity) {
    if (!identity || !window.indexedDB) return Promise.resolve();
    return new Promise((resolve) => {
      try {
        const req = indexedDB.open("briefrooms-trading-push-seen-v1", 1);
        req.onerror = () => resolve();
        req.onsuccess = () => {
          const db = req.result;
          const tx = db.transaction("events", "readwrite");
          tx.objectStore("events").delete(identity);
          tx.oncomplete = tx.onabort = tx.onerror = () => { db.close(); resolve(); };
        };
      } catch (_) { resolve(); }
    });
  }

  async function showNative(title, body, data) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    const identity = logicalEventId(data);
    if (identity) {
      try {
        const prev = Number(localStorage.getItem("brTradingSeenEventV2:" + identity) || 0);
        if (prev && Date.now() - prev < 90 * 24 * 60 * 60 * 1000) return;
      } catch (_) {}
      if (!(await claimOnDevice(identity))) return;
    }
    try {
      const registration = await navigator.serviceWorker?.getRegistration("/");
      if (registration) {
        await registration.showNotification(title, {
          body,
          icon: "/assets/favicon.svg",
          badge: "/assets/favicon.svg",
          tag: identity || data?.event_id || undefined,
          renotify: false,
          data: { url: location.href, ...(data || {}) },
        });
      } else {
        new Notification(title, { body, icon: "/assets/favicon.svg", tag: identity || data?.event_id || undefined });
      }
      if (identity) {
        try { localStorage.setItem("brTradingSeenEventV2:" + identity, String(Date.now())); } catch (_) {}
      }
    } catch (_) {
      await releaseOnDevice(identity);
    }
  }

  function eventAllowed(event, prefs) {
    const channel = String(event.engine || "").toLowerCase();
    const type = String(event.event_type || "").toLowerCase();
    return !!prefs.enabled && !!prefs.channels[channel] && !!prefs.events[type];
  }

  async function pollEvents(config) {
    const prefs = loadPrefs();
    if (!prefs.enabled || Notification.permission !== "granted") return;
    try {
      const newestFirst = await fetchNotificationEvents(config);
      const events = [...newestFirst].reverse();
      if (!events.length) return;

      const cursor = localStorage.getItem(CURSOR_KEY);
      if (!cursor) {
        localStorage.setItem(CURSOR_KEY, String(events[events.length - 1].event_id || ""));
        return;
      }
      // Recover only recent events; a long-offline phone must never be
      // flooded by weeks of accumulated historical trading notifications.
      const now = Date.now();
      const recent = events.filter((event) => {
        const t = Date.parse(String(event.closed_at || event.opened_at || event.observed_at || ""));
        return Number.isFinite(t) && now >= t && now - t <= 2 * 60 * 60_000;
      });
      const candidates = [...new Map(recent.map((event) => [logicalEventId(event), event])).values()];
      for (const event of candidates) {
        if (eventAllowed(event, prefs)
          && !event.delivery_recovery && event.source !== "delivery_recovery"
          && !/-r[0-9]+$/i.test(String(event.event_id || ""))) {
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

    let lastVerifiedAt = 0;
    let recoveryRunning = null;
    let devicePushStatus = prefs.enabled ? t.checking : t.pushDisabled;

    async function verifyDevicePush({ repair = true, force = false } = {}) {
      const current = loadPrefs();
      if (!current.enabled) { devicePushStatus = t.pushDisabled; return; }
      if (Notification.permission !== "granted") { devicePushStatus = t.denied; return; }
      if (recoveryRunning) return recoveryRunning;
      if (!force && Date.now() - lastVerifiedAt < 5 * 60_000) return;
      recoveryRunning = (async () => {
        try {
          const registration = await getRegistration();
          const subscription = await registration?.pushManager?.getSubscription();
          if (!subscription && !repair) {
            devicePushStatus = t.pushMissing;
            return;
          }
          const serverStatus = subscription
            ? await backgroundSubscriptionStatus(config, subscription).catch(() => null) : null;
          if (subscription && serverStatus?.registered === true) {
            devicePushStatus = t.pushReady;
          } else if (repair) {
            devicePushStatus = t.pushRepairing;
            const restored = await syncBackgroundSubscription(config, current);
            if (!restored) throw new Error("subscription_missing");
            const verified = await backgroundSubscriptionStatus(config, restored);
            devicePushStatus = verified?.registered === true ? t.pushRepaired : t.pushUnverified;
          } else {
            devicePushStatus = serverStatus?.registered === false ? t.pushMissing : t.pushUnverified;
          }
        } catch (error) {
          devicePushStatus = String(error?.message || t.pushUnverified);
        } finally {
          lastVerifiedAt = Date.now();
          const el = modal.querySelector("[data-brn-device-status]");
          if (el) el.textContent = devicePushStatus;
        }
      })().finally(() => { recoveryRunning = null; });
      return recoveryRunning;
    }

    const launcher = document.createElement("div");
    launcher.className = "brn-launcher";
    launcher.innerHTML = `
      <button type="button" class="brn-open" data-brn-action="open">${prefs.enabled ? t.settingsTitle : t.enable}</button>
    `;

    const modal = document.createElement("div");
    modal.className = "brn-modal";
    modal.hidden = true;
    modal.innerHTML = `
      <div class="brn-backdrop" data-brn-action="close"></div>
      <section class="brn-dialog" role="dialog" aria-modal="true" aria-labelledby="brn-title">
        <button type="button" class="brn-close" data-brn-action="close" aria-label="${t.cancel}">×</button>
        <span class="brn-kicker">BriefRooms Alerts</span>
        <h2 id="brn-title">${t.settingsTitle}</h2>
        <p class="brn-intro">${t.intro}</p>
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
        <p class="brn-note">${t.foregroundNote}</p>
        <p class="brn-note" role="status" aria-live="polite" data-brn-device-status>${escapeHtml(devicePushStatus)}</p>
        <div class="brn-status" data-brn-status>${prefs.enabled ? t.allowed : ""}</div>
        <div class="brn-actions">
          ${prefs.enabled ? `<button type="button" class="brn-disable" data-brn-action="disable">${t.disable}</button>` : ""}
          <button type="button" class="brn-send" data-brn-action="send">${t.test}</button>
        </div>
      </section>
    `;

    const target = document.querySelector(".switcher") || document.querySelector("main") || document.body;
    if (target.parentNode) target.parentNode.insertBefore(launcher, target.nextSibling);
    document.body.appendChild(modal);

    const status = modal.querySelector("[data-brn-status]");
    const sendButton = modal.querySelector('[data-brn-action="send"]');
    const disableButton = modal.querySelector('[data-brn-action="disable"]');

    function openModal() {
      const current = loadPrefs();
      for (const [key, value] of Object.entries(current.channels)) {
        const input = modal.querySelector(`[data-brn="channel.${key}"]`);
        if (input) input.checked = value;
      }
      for (const [key, value] of Object.entries(current.events)) {
        const input = modal.querySelector(`[data-brn="event.${key}"]`);
        if (input) input.checked = value;
      }
      modal.hidden = false;
      document.documentElement.classList.add("brn-modal-open");
      modal.querySelector(".brn-dialog")?.focus?.();
      void verifyDevicePush({ repair: true, force: true });
    }

    function closeModal() {
      modal.hidden = true;
      document.documentElement.classList.remove("brn-modal-open");
    }

    launcher.querySelector('[data-brn-action="open"]').addEventListener("click", openModal);
    modal.querySelectorAll('[data-brn-action="close"]').forEach((el) => el.addEventListener("click", closeModal));
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && !modal.hidden) closeModal();
    });

    sendButton.addEventListener("click", async () => {
      const next = loadPrefs();
      for (const input of modal.querySelectorAll("input[data-brn]")) {
        const [group, item] = input.getAttribute("data-brn").split(".");
        if (group === "channel") next.channels[item] = input.checked;
        if (group === "event") next.events[item] = input.checked;
      }

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

      next.enabled = true;
      savePrefs(next);
      try {
        await getRegistration();
        const subscription = await syncBackgroundSubscription(config, next);
        if (!subscription) throw new Error("subscription_missing");
        await testBackgroundSubscription(config, subscription);
        status.textContent = t.allowed;
        lastVerifiedAt = 0;
        await verifyDevicePush({ repair: false, force: true });
        await pollEvents(config);
        closeModal();
      } catch (error) {
        const providerStatus = Number(error?.pushStatus || error?.status || 0);
        if (providerStatus === 404 || providerStatus === 410) {
          try {
            const registration = await getRegistration();
            const stale = registration ? await registration.pushManager.getSubscription() : null;
            if (stale) {
              try { await stale.unsubscribe(); } catch (_) {}
            }
            const repaired = await syncBackgroundSubscription(config, next);
            if (!repaired) throw new Error("subscription_repair_failed");
            await testBackgroundSubscription(config, repaired);
            status.textContent = t.allowed;
            await pollEvents(config);
            closeModal();
            return;
          } catch (_) {}
        }
        if (error?.message === "push_manager_unavailable") {
          status.textContent = t.pushUnsupported;
        } else {
          status.textContent = t.pushTestFailed;
        }
      }
    });

    if (disableButton) {
      disableButton.addEventListener("click", async () => {
        const next = loadPrefs();
        next.enabled = false;
        savePrefs(next);
        try { await removeBackgroundSubscription(config); } catch (_) {}
        status.textContent = t.saved;
        closeModal();
      });
    }

    await verifyDevicePush({ repair: true, force: true });
    await pollEvents(config);
    const interval = Math.max(30, Number(config.poll_interval_seconds || 60)) * 1000;
    window.setInterval(() => pollEvents(config), interval);
    // Installed desktop PWA can stay open for days. Revalidate its formerly
    // working endpoint on visibility/network return, without triggering a test
    // notification or re-sending already accepted trade alerts.
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) void verifyDevicePush({ repair: true });
    });
    window.addEventListener("online", () => void verifyDevicePush({ repair: true, force: true }));
    window.setInterval(() => void verifyDevicePush({ repair: true }), 15 * 60_000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
