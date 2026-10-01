import webpush from "npm:web-push@3.6.7";
import { eventAllowed, json, preflight, safePathForEngine, serviceClient } from "../_shared/common.ts";

Deno.serve(async (req) => {
  const pf = preflight(req); if (pf) return pf;
  if (req.method !== "POST") return json(req, { error: "method_not_allowed" }, 405);

  try {
    const body = await req.json();
    const eventId = String(body?.event_id || "");
    if (!/^[0-9a-f]{24}$/i.test(eventId)) {
      return json(req, { error: "invalid_event_id" }, 400);
    }

    const canonicalUrl = "https://raw.githubusercontent.com/Maja2711/briefrooms-site/main/data/notifications/trading-events.json?v=" + Date.now();
    const canonicalResponse = await fetch(canonicalUrl, { headers: { "Accept": "application/json" } });
    if (!canonicalResponse.ok) return json(req, { error: "canonical_feed_unavailable" }, 503);
    const canonical = await canonicalResponse.json();
    const event = Array.isArray(canonical?.events)
      ? canonical.events.find((row: any) => String(row?.event_id || "") === eventId)
      : null;
    if (!event) return json(req, { error: "event_not_in_canonical_feed" }, 404);

    const engine = String(event?.engine || "").toLowerCase();
    const eventType = String(event?.event_type || "").toUpperCase();
    if (!["daily","weekly","stock"].includes(engine) || !["OPEN","CLOSE"].includes(eventType)) {
      return json(req, { error: "invalid_canonical_event" }, 400);
    }

    const publicKey = Deno.env.get("BRIEFROOMS_VAPID_PUBLIC_KEY") || "";
    const privateKey = Deno.env.get("BRIEFROOMS_VAPID_PRIVATE_KEY") || "";
    const subject = Deno.env.get("BRIEFROOMS_VAPID_SUBJECT") || "mailto:admin@briefrooms.com";
    if (!publicKey || !privateKey) return json(req, { error: "vapid_not_configured" }, 503);
    webpush.setVapidDetails(subject, publicKey, privateKey);

    const db = serviceClient();
    const { data: subscriptions, error } = await db.from("trading_push_subscriptions")
      .select("id,endpoint,p256dh,auth,preferences,locale,access_tier")
      .eq("is_active", true);
    if (error) throw error;

    let sent = 0, skipped = 0, expired = 0, failed = 0;
    for (const sub of subscriptions || []) {
      if (!eventAllowed(sub.preferences, engine, eventType)) { skipped++; continue; }

      const { data: delivery, error: deliveryError } = await db.from("trading_push_deliveries")
        .upsert({
          event_id: eventId,
          subscription_id: sub.id,
          engine,
          event_type: eventType,
          status: "PENDING",
        }, { onConflict: "event_id,subscription_id", ignoreDuplicates: false })
        .select("id,status")
        .single();
      if (deliveryError) { failed++; continue; }
      if (delivery.status === "SENT" || delivery.status === "CLICKED") { skipped++; continue; }

      const pl = String(sub.locale || "pl").toLowerCase().startsWith("pl");
      const action = eventType === "OPEN" ? (pl ? "OTWARTO" : "OPENED") : (pl ? "ZAMKNIĘTO" : "CLOSED");
      const title = "BriefRooms · " + (engine === "daily" ? "Daily Trading" : engine === "weekly" ? "Weekly Trading" : "Stock Trading");
      const dir = event?.direction ? " · " + event.direction : "";
      const entry = event?.entry != null ? " @ " + event.entry : "";
      const body = action + " · " + String(event?.instrument || "") + dir + entry;
      const clickApi = (Deno.env.get("SUPABASE_URL") || "") + "/functions/v1/trading-push-click";
      const payload = JSON.stringify({
        title,
        body,
        event_id: eventId,
        url: "https://briefrooms.com" + safePathForEngine(engine),
        data: { engine, event_type: eventType, delivery_id: delivery.id, click_api: clickApi },
      });

      try {
        const response = await webpush.sendNotification({
          endpoint: sub.endpoint,
          keys: { p256dh: sub.p256dh, auth: sub.auth },
        }, payload, { TTL: 3600, urgency: "high" });
        sent++;
        await db.from("trading_push_deliveries").update({
          status: "SENT",
          http_status: response?.statusCode || 201,
          sent_at: new Date().toISOString(),
        }).eq("id", delivery.id);
      } catch (pushError) {
        const statusCode = Number(pushError?.statusCode || 0) || null;
        if (statusCode === 404 || statusCode === 410) {
          expired++;
          await db.from("trading_push_subscriptions").update({
            is_active: false,
            updated_at: new Date().toISOString(),
          }).eq("id", sub.id);
          await db.from("trading_push_deliveries").update({
            status: "EXPIRED", http_status: statusCode, error_code: "push_subscription_expired",
          }).eq("id", delivery.id);
        } else {
          failed++;
          await db.from("trading_push_deliveries").update({
            status: "FAILED", http_status: statusCode, error_code: String(pushError?.body || pushError?.message || "push_failed").slice(0, 500),
          }).eq("id", delivery.id);
        }
      }
    }
    return json(req, { ok: true, event_id: eventId, sent, skipped, expired, failed });
  } catch (error) {
    return json(req, { error: "dispatch_failed", detail: String(error?.message || error) }, 500);
  }
});
