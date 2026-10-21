const BR_PWA_CACHE = "briefrooms-pwa-shell-v2";
const BR_PWA_ASSETS = [
  "/offline.html",
  "/manifest.webmanifest",
  "/assets/favicon.svg",
  "/assets/briefrooms-app-icon-192-v2.svg",
  "/assets/briefrooms-app-icon-512-v2.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(BR_PWA_CACHE);
    await cache.addAll(BR_PWA_ASSETS);
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys
      .filter((key) => key.startsWith("briefrooms-pwa-shell-") && key !== BR_PWA_CACHE)
      .map((key) => caches.delete(key)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET" || request.mode !== "navigate") return;
  event.respondWith((async () => {
    try {
      return await fetch(request);
    } catch (_) {
      const fallback = await caches.match("/offline.html");
      return fallback || Response.error();
    }
  })());
});

// ACK proves that this service worker displayed the notification, not merely
// that a push gateway accepted the payload. Retry transient ACK transport failures.
async function acknowledgeShownTradingNotification(data) {
  const ackUrl = data?.ack_url;
  const deliveryId = data?.delivery_id;
  const ackToken = data?.ack_token;
  if (!ackUrl || !deliveryId || !ackToken) return false;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await fetch(ackUrl, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ delivery_id: deliveryId, ack_token: ackToken }),
        cache: "no-store",
      });
      if (response.ok) return true;
      // Invalid or expired tokens cannot recover from retries.
      if ([400, 403, 404, 409].includes(response.status)) return false;
    } catch (_) {
      // Offline/browser network errors are retried while the SW stays alive.
    }
    if (attempt < 2) await new Promise((resolve) => setTimeout(resolve, (attempt + 1) * 750));
  }
  return false;
}

function tradingNotificationIdentity(payload) {
  const data = payload?.data || {};
  if (data.logical_event_key) return String(data.logical_event_key);
  if (data.engine && data.event_type && data.position_id) {
    return [data.engine, data.event_type, data.position_id].join("|");
  }
  return String(payload?.event_id || "");
}

// Persist the logical event key across service-worker restarts and multiple
// subscriptions on the same Samsung/browser. IndexedDB add() is atomic.
async function claimTradingNotification(identity) {
  if (!identity || !self.indexedDB) return true;
  return new Promise((resolve) => {
    let finished = false;
    const finish = (value) => { if (!finished) { finished = true; resolve(value); } };
    try {
      const opening = self.indexedDB.open("briefrooms-trading-push-seen-v1", 1);
      opening.onupgradeneeded = () => opening.result.createObjectStore("events", { keyPath: "id" });
      opening.onerror = () => finish(true);
      opening.onsuccess = () => {
        const db = opening.result;
        let alreadyClaimed = false;
        try {
          const tx = db.transaction("events", "readwrite");
          const write = tx.objectStore("events").add({ id: identity, seen_at: Date.now(), status: "PENDING" });
          write.onerror = (e) => {
            if (write.error?.name === "ConstraintError") {
              e.preventDefault();
              e.stopPropagation();
              alreadyClaimed = true;
            }
          };
          tx.oncomplete = () => { db.close(); finish(!alreadyClaimed); };
          tx.onabort = () => { db.close(); finish(true); };
          tx.onerror = () => { db.close(); finish(true); };
        } catch (_) { db.close(); finish(true); }
      };
    } catch (_) { finish(true); }
  });
}

// A claim only prevents concurrent display; it is not evidence that display
// succeeded. Legacy seen_at-only claims also cannot prove an ACK is safe.
async function shownTradingNotification(identity, markShown = false) {
  if (!identity || !self.indexedDB) return false;
  return new Promise((resolve) => {
    try {
      const opening = self.indexedDB.open("briefrooms-trading-push-seen-v1", 1);
      opening.onupgradeneeded = () => opening.result.createObjectStore("events", { keyPath: "id" });
      opening.onerror = () => resolve(false);
      opening.onsuccess = () => {
        const db = opening.result;
        try {
          const tx = db.transaction("events", markShown ? "readwrite" : "readonly");
          const store = tx.objectStore("events");
          let shown = false;
          if (markShown) {
            const now = Date.now();
            store.put({ id: identity, seen_at: now, shown_at: now, status: "SHOWN" });
            shown = true;
          } else {
            const read = store.get(identity);
            read.onsuccess = () => { shown = read.result?.status === "SHOWN" && Number.isFinite(read.result?.shown_at); };
          }
          tx.oncomplete = () => { db.close(); resolve(shown); };
          tx.onabort = tx.onerror = () => { db.close(); resolve(false); };
        } catch (_) { db.close(); resolve(false); }
      };
    } catch (_) { resolve(false); }
  });
}

async function releaseTradingNotification(identity) {
  if (!identity || !self.indexedDB) return;
  return new Promise((resolve) => {
    try {
      const opening = self.indexedDB.open("briefrooms-trading-push-seen-v1", 1);
      opening.onupgradeneeded = () => opening.result.createObjectStore("events", { keyPath: "id" });
      opening.onerror = () => resolve();
      opening.onsuccess = () => {
        const db = opening.result;
        const tx = db.transaction("events", "readwrite");
        tx.objectStore("events").delete(identity);
        tx.oncomplete = tx.onabort = tx.onerror = () => { db.close(); resolve(); };
      };
    } catch (_) { resolve(); }
  });
}

// A stale push must be labelled as such while preserving exact market timestamps.
function pushDisplayCopy(payload, displayedAtMs = Date.now()) {
  const body = String(payload?.body || "");
  const eventMs = Date.parse(String(payload?.event_at || payload?.sent_at || ""));
  if (!Number.isFinite(eventMs) || displayedAtMs - eventMs <= 5 * 60_000) return body;
  const minutes = Math.floor((displayedAtMs - eventMs) / 60000);
  const localized = String(payload?.language || "pl").toLowerCase().startsWith("pl");
  return body + (localized ? ` · OPÓŹNIONE ${minutes} min` : ` · DELAYED ${minutes} min`);
}

self.addEventListener("push", (event) => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch (_) {
    payload = { body: event.data ? event.data.text() : "" };
  }
  const title = payload.title || "BriefRooms Trading";
  const receivedAt = Date.now();
  const options = {
    body: pushDisplayCopy(payload, receivedAt),
    icon: "/assets/briefrooms-app-icon-192-v2.svg",
    badge: "/assets/favicon.svg",
    tag: tradingNotificationIdentity(payload) || payload.tag || undefined,
    renotify: false, // Never raise a second audible alert for the same trade.
    // Android timestamp is when shown, not when a queued server request was sent.
    timestamp: receivedAt,
    data: { url: payload.url || "/pl/inwestycje/daily-trading.html", event_id: payload.event_id || null, sent_at: payload.sent_at || null, event_at: payload.event_at || null, push_received_at: new Date(receivedAt).toISOString(), ...(payload.data || {}), ack_url: payload.ack_url || payload?.data?.ack_url || null, ack_token: payload.ack_token || payload?.data?.ack_token || null, delivery_id: payload.delivery_id || payload?.data?.delivery_id || null },
  };
  event.waitUntil((async () => {
    const identity = tradingNotificationIdentity(payload);
    let shown = false;
    const existing = identity && typeof self.registration.getNotifications === "function"
      ? await self.registration.getNotifications({ tag: identity }) : [];
    if (existing.length) {
      shown = true;
    } else if (!(await claimTradingNotification(identity))) {
      // A simultaneous foreground/push display may still fail. ACK only a
      // completed display, never another handler's in-flight IDB claim.
      shown = await shownTradingNotification(identity);
    } else {
      try {
        await self.registration.showNotification(title, options);
        await shownTradingNotification(identity, true);
        shown = true;
      } catch (error) {
        // The OS may have blocked the UI: do not ACK. Permit bounded retry.
        await releaseTradingNotification(identity);
        throw error;
      }
    }
    if (shown) await acknowledgeShownTradingNotification({
      ack_url: payload.ack_url || payload?.data?.ack_url,
      delivery_id: payload.delivery_id || payload?.data?.delivery_id,
      ack_token: payload.ack_token || payload?.data?.ack_token,
    });
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification?.data || {};
  const url = data.url || "/pl/inwestycje/daily-trading.html";
  event.waitUntil((async () => {
    await acknowledgeShownTradingNotification(data);
    try {
      if (data.analytics_url) await fetch(data.analytics_url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ event_id: data.event_id || null }),
      });
    } catch (_) {}
    const windows = await clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of windows) {
      if ("focus" in client) {
        await client.navigate(url);
        return client.focus();
      }
    }
    return clients.openWindow ? clients.openWindow(url) : undefined;
  })());
});
