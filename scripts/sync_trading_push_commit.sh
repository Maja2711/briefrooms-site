#!/usr/bin/env bash
set -euo pipefail

sha="${1:-}"
engines_csv="${2:-}"
push_api="${PUSH_API:-https://briefrooms-trading-push.szczerbinski0577.workers.dev}"

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

response="${RUNNER_TEMP:-/tmp}/trading-push-commit-sync.json"
for attempt in 1 2 3 4; do
  code=$(curl --silent --show-error \
    -o "$response" \
    -w "%{http_code}" \
    -H "content-type: application/json" \
    --data "$payload" \
    "$push_api/sync-trading" || true)

  cat "$response" || true
  if [[ "$code" == "200" ]] && python - "$response" <<'PY'
import json, sys
from pathlib import Path
j=json.loads(Path(sys.argv[1]).read_text())
d=j.get("dispatch") or {}
ok=(
    j.get("ok") is True
    and int(d.get("failed") or 0) == 0
    and int(d.get("pending") or 0) == 0
)
print("TRADING_PUSH_COMMIT_SYNC", j)
raise SystemExit(0 if ok else 1)
PY
  then
    exit 0
  fi

  sleep "$attempt"
done

echo "Commit-bound trading push failed for $sha [$engines_csv]" >&2
exit 1
