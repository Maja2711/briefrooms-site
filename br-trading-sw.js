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
    renotify: false,
    data: { url: payload.url || "/pl/inwestycje/daily-trading.html", ...(payload.data || {}) },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

async function trackClick(data) {
  const clickApi = data?.click_api;
  const deliveryId = data?.delivery_id;
  if (!clickApi || !deliveryId || !String(clickApi).startsWith("https://")) return;
  try {
    await fetch(clickApi, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ delivery_id: deliveryId }),
      mode: "cors",
      credentials: "omit",
      keepalive: true,
    });
  } catch (_) {}
}

async function focusOrOpen(url) {
  const windows = await clients.matchAll({ type: "window", includeUncontrolled: true });
  for (const client of windows) {
    if ("navigate" in client) {
      try { await client.navigate(url); } catch (_) {}
    }
    if ("focus" in client) return client.focus();
  }
  return clients.openWindow ? clients.openWindow(url) : undefined;
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification?.data || {};
  const url = data.url || "/pl/inwestycje/daily-trading.html";
  event.waitUntil(Promise.all([trackClick(data), focusOrOpen(url)]));
});
