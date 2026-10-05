#!/usr/bin/env bash
set -euo pipefail

: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"
PUSH_API="${PUSH_API:-https://briefrooms-trading-push.szczerbinski0577.workers.dev}"

watch_seconds=$((5 * 60 * 60 + 25 * 60))
handoff_seconds=30
started=$(date +%s)
deadline=$((started + watch_seconds))
next_repo_refresh=$started

sync_push_commit() {
  local sha="$1"
  bash scripts/sync_trading_push_commit.sh "$sha" daily
}

persist_triggered_exit() {
  for attempt in 1 2 3; do
    git fetch --quiet origin main
    git reset --hard origin/main

    python scripts/daily_eurusd_fast_lifecycle.py       --output data/investments/eurusd_daily_spot.json       --history data/investments/eurusd_daily_history.json       --apply

    python scripts/build_trading_notification_events.py

    git add --       data/investments/eurusd_daily_spot.json       data/investments/eurusd_daily_history.json       data/notifications/trading-state.json       data/notifications/trading-events.json

    if git diff --cached --quiet; then
      echo "Realtime trigger no longer applies on fresh main; canonical state already won."
      return 2
    fi

    python scripts/verify_no_retroactive_execution.py       --baseline-ref HEAD       --run-start "$NO_RETROACTIVE_RUN_STARTED_AT"

    git config user.name "github-actions[bot]"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    git commit -m "Close Daily EURUSD via realtime lifecycle"
    sha=$(git rev-parse HEAD)

    if git push origin HEAD:main; then
      sync_push_commit "$sha"
      echo "close_sha=$sha" >> "$GITHUB_OUTPUT"
      return 0
    fi

    echo "Realtime close push conflict on attempt $attempt; recomputing from fresh main."
    git fetch --quiet origin main || true
    git reset --hard origin/main || true
    sleep "$attempt"
  done
  return 1
}

probe_flags() {
  local path="$1"
  python - "$path" <<'PY'
import json, sys
from pathlib import Path
j=json.loads(Path(sys.argv[1]).read_text())
print("1" if j.get("position_open") else "0", "1" if j.get("exit_triggered") else "0")
PY
}

while [ "$(date +%s)" -lt "$deadline" ]; do
  now=$(date +%s)

  if [ "$now" -ge "$next_repo_refresh" ]; then
    git fetch --quiet origin main || true
    if git diff --quiet && git diff --cached --quiet; then
      git reset --hard origin/main >/dev/null
    fi
    next_repo_refresh=$((now + 30))
  fi

  if ! python scripts/daily_eurusd_fast_lifecycle.py     --output data/investments/eurusd_daily_spot.json     --history data/investments/eurusd_daily_history.json     --probe-json "$RUNNER_TEMP/eurusd-realtime-probe.json"; then
    echo "Realtime probe source error; retrying after 15 seconds."
    sleep 15
    continue
  fi

  read -r position_open exit_triggered <<<"$(probe_flags "$RUNNER_TEMP/eurusd-realtime-probe.json")"

  if [ "$position_open" != "1" ]; then
    echo "No persisted OPEN Daily EURUSD position; realtime watcher is not needed."
    echo "position_open=false" >> "$GITHUB_OUTPUT"
    exit 0
  fi

  if [ "$exit_triggered" = "1" ]; then
    echo "Realtime exit trigger observed; refreshing main and re-validating before write."
    set +e
    persist_triggered_exit
    rc=$?
    set -e
    if [ "$rc" -eq 0 ] || [ "$rc" -eq 2 ]; then
      echo "position_open=false" >> "$GITHUB_OUTPUT"
      exit 0
    fi
    exit "$rc"
  fi

  sleep 5
done

git fetch --quiet origin main || true
git reset --hard origin/main >/dev/null || true
python scripts/daily_eurusd_fast_lifecycle.py   --output data/investments/eurusd_daily_spot.json   --history data/investments/eurusd_daily_history.json   --probe-json "$RUNNER_TEMP/eurusd-realtime-final.json" || true

still_open=$(python - "$RUNNER_TEMP/eurusd-realtime-final.json" <<'PY'
import json, sys
from pathlib import Path
try:
    j=json.loads(Path(sys.argv[1]).read_text())
except Exception:
    j={}
print("1" if j.get("position_open") else "0")
PY
)

if [ "$still_open" = "1" ]; then
  curl --fail --silent --show-error     -X POST     -H "Authorization: Bearer $GH_TOKEN"     -H "Accept: application/vnd.github+json"     -H "X-GitHub-Api-Version: 2022-11-28"     "https://api.github.com/repos/$GITHUB_REPOSITORY/actions/workflows/daily-eurusd-realtime-lifecycle.yml/dispatches"     -d '{"ref":"main"}'
  echo "Queued successor realtime watcher."
fi

handoff_end=$(( $(date +%s) + handoff_seconds ))
while [ "$(date +%s)" -lt "$handoff_end" ] && [ "$still_open" = "1" ]; do
  if python scripts/daily_eurusd_fast_lifecycle.py     --output data/investments/eurusd_daily_spot.json     --history data/investments/eurusd_daily_history.json     --probe-json "$RUNNER_TEMP/eurusd-realtime-handoff.json"; then
    read -r _ triggered <<<"$(probe_flags "$RUNNER_TEMP/eurusd-realtime-handoff.json")"
    if [ "$triggered" = "1" ]; then
      persist_triggered_exit || true
      break
    fi
  fi
  sleep 5
done
