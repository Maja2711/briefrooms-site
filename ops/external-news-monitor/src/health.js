export function assess(runs, now, graceMinutes) {
  const scheduled = runs.filter(r => r.event === "schedule" && r.run_started_at);
  scheduled.sort((a,b) => Date.parse(b.run_started_at)-Date.parse(a.run_started_at));
  const latest = scheduled[0];
  if (!latest) return {healthy:false, reason:"no_scheduled_runs"};
  const ageMinutes = (now-Date.parse(latest.run_started_at))/60000;
  return {healthy: ageMinutes <= graceMinutes && ageMinutes >= 0,
    reason: ageMinutes > graceMinutes ? "schedule_missing" : ageMinutes < 0 ? "future_timestamp" : "ok",
    ageMinutes, runId:latest.id, runUrl:latest.html_url,
    status:latest.status, conclusion:latest.conclusion};
}
