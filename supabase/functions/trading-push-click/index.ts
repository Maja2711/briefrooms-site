import { json, preflight, serviceClient } from "../_shared/common.ts";

Deno.serve(async (req) => {
  const pf = preflight(req); if (pf) return pf;
  if (req.method !== "POST") return json(req, { error: "method_not_allowed" }, 405);
  try {
    const body = await req.json();
    const id = String(body?.delivery_id || "");
    if (!/^[0-9a-f-]{36}$/i.test(id)) return json(req, { error: "invalid_delivery" }, 400);
    const db = serviceClient();
    const { error } = await db.from("trading_push_deliveries").update({
      status: "CLICKED",
      clicked_at: new Date().toISOString(),
    }).eq("id", id);
    if (error) throw error;
    return json(req, { ok: true });
  } catch (error) {
    return json(req, { error: "click_failed", detail: String(error?.message || error) }, 500);
  }
});
