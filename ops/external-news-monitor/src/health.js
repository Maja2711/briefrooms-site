export function assess(runs, now, graceMinutes) {
  if (!Number.isFinite(now) || !Number.isFinite(graceMinutes) || graceMinutes <= 0) throw new Error("invalid_monitor_configuration");
  const scheduled = runs.filter(r => r.event === "schedule" && Number.isFinite(Date.parse(r.run_started_at || "")));
  scheduled.sort((a,b) => Date.parse(b.run_started_at)-Date.parse(a.run_started_at));
  const latest = scheduled[0];
  if (!latest) return {healthy:false, reason:"no_scheduled_runs"};
  const ageMinutes = (now-Date.parse(latest.run_started_at))/60000;
  return {healthy: ageMinutes >= 0 && ageMinutes <= graceMinutes,
    reason: ageMinutes < 0 ? "future_timestamp" : ageMinutes > graceMinutes ? "schedule_missing" : "ok",
    ageMinutes, runId:latest.id, runUrl:latest.html_url,
    status:latest.status, conclusion:latest.conclusion};
}
