import { assess } from "./health.js";
const REPO = "Maja2711/briefrooms-site";
const WORKFLOWS = [
  { file: "news-publication-sla-watchdog.yml", label: "watchdog", graceMinutes: 90 },
  { file: "publish-news.yml", label: "publisher-schedule", graceMinutes: 90 },
];
async function githubJson(env, path) {
  const response = await fetch(`https://api.github.com/repos/${REPO}/${path}`, {headers:{
    "Accept":"application/vnd.github+json", "User-Agent":"briefrooms-external-watchdog",
    ...(env.GITHUB_READ_TOKEN ? {"Authorization":`Bearer ${env.GITHUB_READ_TOKEN}`} : {})
  }});
  if (!response.ok) throw new Error(`github_api_${response.status}:${path}`);
  return response.json();
}
async function inspect(env) {
  const results = {};
  for (const workflow of WORKFLOWS) {
    const state = await githubJson(env, `actions/workflows/${workflow.file}`);
    if (state.state !== "active") {
      results[workflow.label] = {healthy:false,reason:"workflow_inactive",state:state.state};
      continue;
    }
    const data = await githubJson(env,`actions/workflows/${workflow.file}/runs?per_page=100&event=schedule`);
    results[workflow.label] = assess(data.workflow_runs || [],Date.now(),workflow.graceMinutes);
  }
  return results;
}
async function notify(env,payload) {
  if (!env.ALERT_WEBHOOK_URL) throw new Error("alert_webhook_not_configured");
  const response = await fetch(env.ALERT_WEBHOOK_URL,{
    method:"POST",headers:{"content-type":"application/json"},
    body:JSON.stringify(payload)
  });
  if (!response.ok) throw new Error(`alert_webhook_${response.status}`);
}
export default {
  async scheduled(event,env,ctx) {
    ctx.waitUntil((async()=>{
      let result, failures;
      try {
        result=await inspect(env);
        failures=Object.entries(result).filter(([,v])=>!v.healthy);
      } catch(error) {
        console.error("NEWS_MONITOR_ERROR",String(error));
        await notify(env,{source:"briefrooms-external-monitor",status:"monitor_error",error:String(error)});
        throw error;
      }
      // KV is required for alert deduplication; missing binding fails closed.
      if (!env.MONITOR_STATE) throw new Error("monitor_state_kv_not_configured");
      const fingerprint=failures.map(([name,v])=>`${name}:${v.reason}`).sort().join("|");
      const previous=await env.MONITOR_STATE.get("last_alert_fingerprint");
      if (fingerprint===previous) return;
      const payload={source:"briefrooms-external-monitor",status:failures.length?"alert":"recovered",
        checkedAt:new Date().toISOString(),result};
      // Never persist alert state before successful delivery.
      if (failures.length || previous) await notify(env,payload);
      await env.MONITOR_STATE.put("last_alert_fingerprint",fingerprint);
    })());
  }
};
