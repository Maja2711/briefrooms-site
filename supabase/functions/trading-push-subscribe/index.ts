import { json, preflight, serviceClient, validPreferences } from "../_shared/common.ts";

Deno.serve(async (req) => {
  const pf = preflight(req); if (pf) return pf;
  if (req.method !== "POST") return json(req, { error: "method_not_allowed" }, 405);
  try {
    const body = await req.json();
    const sub = body?.subscription;
    const endpoint = String(sub?.endpoint || "");
    const p256dh = String(sub?.keys?.p256dh || "");
    const auth = String(sub?.keys?.auth || "");
    const deviceId = String(body?.device_id || "");
    if (!endpoint.startsWith("https://") || !p256dh || !auth || deviceId.length < 12) {
      return json(req, { error: "invalid_subscription" }, 400);
    }
    const locale = String(body?.locale || "pl").slice(0, 8);
    const preferences = validPreferences(body?.preferences);
    const db = serviceClient();
    const { data, error } = await db.from("trading_push_subscriptions").upsert({
      endpoint,
      p256dh,
      auth,
      device_id: deviceId,
      locale,
      preferences,
      access_tier: "PUBLIC",
      is_active: true,
      last_seen_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }, { onConflict: "endpoint" }).select("id").single();
    if (error) throw error;
    return json(req, { ok: true, subscription_id: data.id });
  } catch (error) {
    return json(req, { error: "subscribe_failed", detail: String(error?.message || error) }, 500);
  }
});
