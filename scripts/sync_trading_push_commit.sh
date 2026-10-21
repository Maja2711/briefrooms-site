#!/usr/bin/env bash
set -euo pipefail

sha="${1:-}"
engines_csv="${2:-}"
push_api="${PUSH_API:-https://briefrooms-trading-push.szczerbinski0577.workers.dev}"
push_api="${push_api%/}"
headers=(-H "content-type: application/json")
if [[ -n "${GH_TOKEN:-}" ]]; then
  # The ephemeral Actions credential is request-scoped: only our fixed Worker
  # may receive it, and the Worker uses it solely for verified GitHub reads.
  if [[ "$push_api" != "https://briefrooms-trading-push.szczerbinski0577.workers.dev" ]]; then
    echo "Refusing authenticated trading push to an untrusted Worker URL." >&2
    exit 2
  fi
  headers+=(-H "Authorization: Bearer ${GH_TOKEN}")
fi

if [[ ! "$sha" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "Invalid commit SHA for trading push sync: $sha" >&2
  exit 2
fi
if [[ -z "$engines_csv" ]]; then
  echo "Trading push sync requires at least one engine." >&2
  exit 2
fi

payload=$(python - "$sha" "$engines_csv" <<'PY'
import json, sys
sha=sys.argv[1].lower()
engines=[x.strip().lower() for x in sys.argv[2].split(",") if x.strip()]
allowed={"daily","weekly","stock"}
if not engines or any(x not in allowed for x in engines):
    raise SystemExit("invalid trading push engine list")
print(json.dumps({"sha":sha,"engines":engines}, separators=(",",":")))
PY
)

response=$(mktemp "${RUNNER_TEMP:-/tmp}/trading-push-commit-sync.XXXXXX")
trap 'rm -f -- "$response"' EXIT
for attempt in 1 2 3 4; do
  code=$(curl --silent --show-error \
    --connect-timeout 5 \
    --max-time 25 \
    -o "$response" \
    -w "%{http_code}" \
    "${headers[@]}" \
    --data "$payload" \
    "$push_api/sync-trading" || true)

  if [[ "$code" == "200" || "$code" == "202" ]] && python - "$response" "$sha" "$engines_csv" "$code" <<'PY'
import json, sys
from datetime import datetime
from pathlib import Path
try:
    j=json.loads(Path(sys.argv[1]).read_text())
    d=j.get("dispatch") or {}
    requested={x.strip().lower() for x in sys.argv[3].split(",") if x.strip()}
    actual=j.get("engines")
    identity=(str(j.get("sha") or "").lower() == sys.argv[2].lower()
              and isinstance(actual,list) and requested.issubset(set(actual)))
    failed=int(d.get("failed") or 0)
    pending=int(d.get("pending") or 0)
    terminal=j.get("ok") is True and failed == 0 and pending == 0 and sys.argv[4] == "200"
    retry_at=j.get("retry_scheduled_at")
    scheduled=False
    if isinstance(retry_at,str) and retry_at:
        scheduled=datetime.fromisoformat(retry_at.replace("Z","+00:00")).tzinfo is not None
    durable=j.get("durable_accepted") is True and ((failed == 0 and pending == 0) or scheduled)
    ok=identity and (terminal or durable)
    print("TRADING_PUSH_COMMIT_SYNC", json.dumps({
        "sha":j.get("sha"), "engines":actual, "status":j.get("status"),
        "http_status":sys.argv[4], "durable_accepted":j.get("durable_accepted") is True,
        "retry_scheduled_at":retry_at, "sent":d.get("sent",0),
        "failed":failed,"pending":pending,"receipt_valid":ok,
    },separators=(",",":")))
except (OSError,ValueError,TypeError,KeyError,AttributeError):
    ok=False
    print("TRADING_PUSH_COMMIT_SYNC invalid or malformed receipt",file=sys.stderr)
raise SystemExit(0 if ok else 1)
PY
  then
    exit 0
  fi

  # Never print the raw body: an upstream error may reflect credentials.
  # Only stable diagnostic codes produced by the Worker are safe to expose.
  reason=$(python - "$response" <<'PY'
import json, re, sys
from pathlib import Path
allowed={"GITHUB_TIMEOUT", "GITHUB_FETCH_FAILED", "GITHUB_JSON_INVALID",
         "GITHUB_TOKEN_TARGET_NOT_ALLOWED", "GITHUB_REDIRECT_REJECTED", "INVALID_GITHUB_AUTHORIZATION",
         "GITHUB_AUTH_REQUIRED", "INVALID_COMMIT_SHA",
         "GITHUB_MAIN_HEAD_MISSING", "GITHUB_TARGET_COMMIT_MISMATCH",
         "COMMIT_NOT_RECENT_MAIN_ANCESTOR", "COMMIT_SYNC_ENGINES_REQUIRED",
         "INVALID_COMMIT_SYNC_ENGINE", "COMMIT_FILE_TIMEOUT",
         "COMMIT_FILE_FETCH_FAILED", "COMMIT_FILE_JSON_INVALID",
         "IMMUTABLE_EVENT_CONFLICT", "SYNC_INTERNAL_ERROR", "GITHUB_HTTP_ERROR",
         "COMMIT_FILE_HTTP_ERROR", "WES_OUTBOX_EVENT_ID_MISMATCH",
         "WEEKLY_COMMIT_DIRECTORY_INVALID", "FALLBACK_REQUESTED_STATE_NOT_CURRENT",
         "FALLBACK_REQUESTED_OUTBOX_NOT_CURRENT"}
try:
    code=json.loads(Path(sys.argv[1]).read_text()).get("error_code")
    safe=isinstance(code,str) and (code in allowed or re.fullmatch(r"(?:GITHUB|COMMIT_FILE)_HTTP_[1-5][0-9]{2}",code))
    print(code if safe else "UNCLASSIFIED_RESPONSE")
except (OSError,ValueError,TypeError,AttributeError):
    print("INVALID_RESPONSE")
PY
)
  echo "Trading push handoff attempt $attempt failed: HTTP ${code:-unavailable} reason=$reason" >&2
  if [[ "$attempt" -lt 4 ]]; then sleep "$attempt"; fi
done

echo "Commit-bound trading push failed for $sha [$engines_csv]" >&2
exit 1
