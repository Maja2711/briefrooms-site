import { createClient } from "npm:@supabase/supabase-js@2.57.4";

const ALLOWED_ORIGINS = new Set([
  "https://briefrooms.com",
  "https://www.briefrooms.com",
  "http://localhost:8000",
  "http://localhost:8080",
  "http://127.0.0.1:8000",
]);

export function corsHeaders(req: Request): Record<string, string> {
  const origin = req.headers.get("origin") || "";
  const allowed = ALLOWED_ORIGINS.has(origin) ? origin : "https://briefrooms.com";
  return {
    "Access-Control-Allow-Origin": allowed,
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-briefrooms-dispatch-secret, x-briefrooms-admin-secret",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Vary": "Origin",
    "Content-Type": "application/json; charset=utf-8",
  };
}

export function json(req: Request, body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: corsHeaders(req) });
}

export function preflight(req: Request): Response | null {
  if (req.method !== "OPTIONS") return null;
  return new Response(null, { status: 204, headers: corsHeaders(req) });
}

export function serviceClient() {
  const url = Deno.env.get("SUPABASE_URL");
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!url || !key) throw new Error("SUPABASE service credentials unavailable");
  return createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false } });
}

export function validPreferences(input: unknown) {
  const p = (input && typeof input === "object" ? input : {}) as Record<string, unknown>;
  const channels = (p.channels && typeof p.channels === "object" ? p.channels : {}) as Record<string, unknown>;
  const events = (p.events && typeof p.events === "object" ? p.events : {}) as Record<string, unknown>;
  return {
    channels: {
      daily: channels.daily !== false,
      weekly: channels.weekly !== false,
      stock: channels.stock !== false,
    },
    events: {
      open: events.open !== false,
      close: events.close !== false,
    },
  };
}

export function eventAllowed(preferences: any, engine: string, eventType: string): boolean {
  const p = validPreferences(preferences);
  const e = String(engine || "").toLowerCase();
  const t = String(eventType || "").toLowerCase();
  return Boolean((p.channels as any)[e] && (p.events as any)[t]);
}

export function safePathForEngine(engine: string): string {
  if (engine === "weekly") return "/pl/inwestycje/pozycje-tygodniowe.html";
  if (engine === "stock") return "/pl/inwestycje/stock-trading.html";
  return "/pl/inwestycje/daily-trading.html";
}
