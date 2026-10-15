const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
};

function json(value, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: JSON_HEADERS });
}

export async function dispatchWatchdog(env, reason = "cloudflare_scheduled_heartbeat") {
  const token = String(env.GITHUB_DISPATCH_TOKEN || "").trim();
  const repository = String(env.GITHUB_REPOSITORY || "Maja2711/briefrooms-site").trim();
  const workflow = String(env.WATCHDOG_WORKFLOW || "daily-eurusd-cycle-watchdog.yml").trim();
  const ref = String(env.WATCHDOG_REF || "main").trim();

  if (!token) {
    throw new Error("github_dispatch_token_missing");
  }
  if (!repository || !workflow || !ref) {
    throw new Error("heartbeat_configuration_invalid");
  }

  const response = await fetch(
    `https://api.github.com/repos/${repository}/actions/workflows/${encodeURIComponent(workflow)}/dispatches`,
    {
      method: "POST",
      headers: {
        "authorization": `Bearer ${token}`,
        "accept": "application/vnd.github+json",
        "content-type": "application/json",
        "user-agent": "BriefRooms-Daily-EURUSD-Heartbeat/1.0",
        "x-github-api-version": "2022-11-28",
      },
      body: JSON.stringify({ ref }),
    },
  );

  if (response.status !== 204) {
    const body = await response.text();
    throw new Error(`github_dispatch_failed_${response.status}:${body.slice(0, 200)}`);
  }

  return {
    ok: true,
    module_id: "TR-03",
    role: "heartbeat_only",
    reason,
    workflow,
    ref,
    dispatched_at: new Date().toISOString(),
    trading_authority: false,
    decision_authority: false,
    execution_authority: false,
  };
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(
      dispatchWatchdog(env).catch((error) => {
        console.error("DAILY_EURUSD_HEARTBEAT_FAILED", String(error?.message || error));
        throw error;
      }),
    );
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return json({
        ok: true,
        module_id: "TR-03",
        role: "heartbeat_only",
        cadence_minutes: 5,
        github_configured: Boolean(String(env.GITHUB_DISPATCH_TOKEN || "").trim()),
        watchdog_workflow: String(env.WATCHDOG_WORKFLOW || ""),
        watchdog_ref: String(env.WATCHDOG_REF || ""),
        trading_authority: false,
        decision_authority: false,
        execution_authority: false,
      });
    }
    return json({ ok: false, error: "not_found" }, 404);
  },
};
