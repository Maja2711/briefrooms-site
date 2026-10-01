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
  const url = event.notification?.data?.url || "/pl/inwestycje/daily-trading.html";
  event.waitUntil((async () => {
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
