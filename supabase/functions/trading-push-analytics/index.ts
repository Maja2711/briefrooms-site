import { json, preflight, serviceClient } from "../_shared/common.ts";

function authorized(req: Request): boolean {
  const expected = Deno.env.get("BRIEFROOMS_PUSH_ADMIN_SECRET") || "";
  return Boolean(expected && req.headers.get("x-briefrooms-admin-secret") === expected);
}

Deno.serve(async (req) => {
  const pf = preflight(req); if (pf) return pf;
  if (req.method !== "POST") return json(req, { error: "method_not_allowed" }, 405);
  if (!authorized(req)) return json(req, { error: "unauthorized" }, 401);
  try {
    const db = serviceClient();
    const { data: subs, error: se } = await db.from("trading_push_subscriptions")
      .select("id,device_id,preferences,is_active,created_at,updated_at");
    if (se) throw se;
    const { data: deliveries, error: de } = await db.from("trading_push_deliveries")
      .select("status,created_at");
    if (de) throw de;

    const active = (subs || []).filter((x) => x.is_active);
    const uniqueDevices = new Set(active.map((x) => x.device_id)).size;
    const channel = (name: string) => active.filter((x) => x.preferences?.channels?.[name] !== false).length;
    const event = (name: string) => active.filter((x) => x.preferences?.events?.[name] !== false).length;
    const now = Date.now();
    const since7d = now - 7 * 86400 * 1000;
    const new7d = (subs || []).filter((x) => new Date(x.created_at).getTime() >= since7d).length;
    const unsub7d = (subs || []).filter((x) => !x.is_active && new Date(x.updated_at).getTime() >= since7d).length;
    const count = (status: string) => (deliveries || []).filter((x) => x.status === status).length;
    const sent = count("SENT") + count("CLICKED");
    const clicked = count("CLICKED");
    return json(req, {
      active_subscriptions: active.length,
      active_devices: uniqueDevices,
      unique_users: null,
      channels: { daily: channel("daily"), weekly: channel("weekly"), stock: channel("stock") },
      events: { open: event("open"), close: event("close") },
      new_7d: new7d,
      unsubscribed_7d: unsub7d,
      sent,
      clicked,
      ctr_percent: sent ? Math.round((clicked / sent) * 10000) / 100 : 0,
      failed: count("FAILED"),
      expired: count("EXPIRED"),
    });
  } catch (error) {
    return json(req, { error: "analytics_failed", detail: String(error?.message || error) }, 500);
  }
});
