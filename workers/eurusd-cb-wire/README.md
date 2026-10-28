# Dedicated EURUSD CB wire cache — prepared, NOT DEPLOYED

This optional service has no trading, quote-fetch, Belief, portfolio or execution
API. No endpoint, account ID, token, deployment workflow or running 60-second
service is claimed. Existing shared Workers and other instruments are untouched.

`scripts/eurusd_cb_poll_service.py` reuses the canonical Python collector and
normalizer, including the official calendars. It targets one non-overlapping poll
every 60 seconds, bounds HTTP work, backs off on service errors, responds to
SIGINT/SIGTERM and keeps the original append-only local wire journal. Polling and
source publication latency have to be measured after deployment; this is not a
live speech stream or an SLA. Google News remains opt-in under the same source
terms gate as the canonical collector.

The optional publisher requires `EURUSD_CB_WIRE_PUBLISH_URL` and
`EURUSD_CB_WIRE_TOKEN` in the service environment. The URL must be the dedicated
EURUSD Worker HTTPS `/v1/wire` endpoint; authenticated redirects are forbidden.
Provision a new namespace and secret deliberately. No runtime activation occurs
from this PR. `workers_dev=false` prevents accidental public Worker exposure in
the supplied prepared configuration; operators must separately select and verify
the dedicated routing before enabling a publisher. Do not reuse a shared trading
Worker, its namespace or its token.

The Worker accepts authenticated GET/POST only at `/v1/wire`. It atomically
rejects older/equal-conflicting publication times, keeps immutable receipt/body
chunks, bounds payloads at 3 MB and returns stale last-known bytes with explicit
transport freshness. It computes SHA-256 of the exact received bytes; it does not
reimplement Python canonical JSON float serialization. Python consumers must
still validate the canonical wire schema, source provenance, as-of times and
snapshot hash. Transport authentication is not source verification. A paper or
broker fill cannot be inferred from a cached publication.

Storage uses a dedicated SQLite Durable Object binding `EURUSD_CB_WIRE` and
content-addressed chunks; no R2 or shared binding is required. Chunking remains
below the conservative 128 KiB per-value ceiling. Reviewed official API references:
[transactional storage](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/)
and [platform limits](https://developers.cloudflare.com/durable-objects/platform/limits/).

Deterministic tests:

```text
python -m unittest tests.test_eurusd_cb_poll_service
node --test tests/test_eurusd_cb_wire_worker.js
```

These tests mock storage and network. They do not certify a deployed Worker,
Cloudflare account permissions, actual scheduling latency or trading behavior.
