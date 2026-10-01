import { json, preflight, serviceClient } from "../_shared/common.ts";

Deno.serve(async (req) => {
  const pf = preflight(req); if (pf) return pf;
  if (req.method !== "POST") return json(req, { error: "method_not_allowed" }, 405);
  try {
    const body = await req.json();
    const endpoint = String(body?.endpoint || "");
    const deviceId = String(body?.device_id || "");
    if (!endpoint && !deviceId) return json(req, { error: "missing_identity" }, 400);
    const db = serviceClient();
    let q = db.from("trading_push_subscriptions").update({
      is_active: false,
      updated_at: new Date().toISOString(),
    });
    q = endpoint ? q.eq("endpoint", endpoint) : q.eq("device_id", deviceId);
    const { error } = await q;
    if (error) throw error;
    return json(req, { ok: true });
  } catch (error) {
    return json(req, { error: "unsubscribe_failed", detail: String(error?.message || error) }, 500);
  }
});
