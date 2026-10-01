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

self.addEventListener("push", (event) => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch (_) {
    payload = { body: event.data ? event.data.text() : "" };
  }
  const title = payload.title || "BriefRooms Trading";
  const options = {
    body: payload.body || "",
    icon: "/assets/briefrooms-app-icon-192-v2.svg",
    badge: "/assets/favicon.svg",
    tag: payload.event_id || payload.tag || undefined,
    data: { url: payload.url || "/pl/inwestycje/daily-trading.html", event_id: payload.event_id || null, ...(payload.data || {}) },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification?.data || {};
  const url = data.url || "/pl/inwestycje/daily-trading.html";
  event.waitUntil((async () => {
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
