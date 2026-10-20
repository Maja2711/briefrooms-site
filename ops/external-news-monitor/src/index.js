import { assess } from "./health.js";
// Cloudflare Worker: independent GitHub Actions watchdog monitor.
// Deploy as a separate Worker; never schedule this through GitHub Actions.
const REPO = "Maja2711/briefrooms-site";
const WORKFLOWS = [
  { file: "news-publication-sla-watchdog.yml", label: "watchdog", graceMinutes: 90 },
  { file: "publish-news.yml", label: "publisher-schedule", graceMinutes: 90 },
];
async function inspect(env) {
  const results = {};
  for (const workflow of WORKFLOWS) {
    const url = `https://api.github.com/repos/${REPO}/actions/workflows/${workflow.file}/runs?per_page=30`;
    const response = await fetch(url, {headers:{
      "Accept":"application/vnd.github+json", "User-Agent":"briefrooms-external-watchdog",
      ...(env.GITHUB_READ_TOKEN ? {"Authorization":`Bearer ${env.GITHUB_READ_TOKEN}`} : {})
    }});
    if (!response.ok) throw new Error(`GitHub API ${workflow.label}: ${response.status}`);
    const data = await response.json();
    results[workflow.label] = assess(data.workflow_runs || [], Date.now(), workflow.graceMinutes);
  }
  return results;
}
export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil((async () => {
      try {
        const result = await inspect(env);
        const failures = Object.entries(result).filter(([,v]) => !v.healthy);
        if (failures.length) {
          console.error("NEWS_SCHEDULE_ALERT", JSON.stringify({checkedAt:new Date().toISOString(), result}));
          if (env.ALERT_WEBHOOK_URL) {
            const response = await fetch(env.ALERT_WEBHOOK_URL, {
              method:"POST",headers:{"content-type":"application/json"},
              body:JSON.stringify({source:"briefrooms-external-monitor", status:"alert", result})
            });
            if (!response.ok) throw new Error(`Alert webhook: ${response.status}`);
          }
        }
      } catch (error) {
        console.error("NEWS_MONITOR_ERROR", String(error));
        throw error;
      }
    })());
  }
};
