self.addEventListener("push", (event) => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch (_) {
    payload = { body: event.data ? event.data.text() : "" };
  }
  const title = payload.title || "BriefRooms Trading";
  const options = {
    body: payload.body || "",
    icon: "/assets/favicon.svg",
    badge: "/assets/favicon.svg",
    tag: payload.event_id || payload.tag || undefined,
    data: { url: payload.url || "/pl/inwestycje/daily-trading.html", ...(payload.data || {}) },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification?.data || {};
  const url = data.url || "/pl/inwestycje/daily-trading.html";
  event.waitUntil((async () => {
    try {
      await fetch("/api/trading-push/analytics/click", {
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
