#!/usr/bin/env python3
"""Optional dedicated EURUSD CB poller. Prepared code, NOT a deployed service.

Uses the exact canonical Python collector. A 60-second target is best-effort
polling, never live speech streaming or a measured end-to-end SLA. No trading
workflow, order, position, price history or direction authority is present.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import eurusd_central_bank_wire as wire

ROOT = Path(__file__).resolve().parents[1]
MAX_PUBLISH_BYTES = 3_000_000


class DeadlineClient:
    """Bound each request and stop starting HTTP work beyond the cycle budget."""
    def __init__(self, budget_seconds=45, monotonic=time.monotonic):
        self.monotonic = monotonic
        self.deadline = monotonic() + budget_seconds

    def get(self, url, **kwargs):
        remaining = self.deadline - self.monotonic()
        if remaining <= 1:
            raise TimeoutError('EURUSD CB collection budget exceeded')
        return wire.HttpClient(timeout=min(8, remaining / 3), retries=1).get(url, **kwargs)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise ValueError('authenticated publisher redirects are forbidden')


def validate_publish_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443)
            or parts.path != '/v1/wire' or parts.query or parts.fragment
            or not re.fullmatch(r'eurusd-cb-wire(?:-[a-z0-9-]+)?\.[a-z0-9-]+\.workers\.dev', parts.hostname or '')):
        raise ValueError('publishing requires the dedicated EURUSD CB HTTPS Worker endpoint')
    return url


def publish_snapshot(snapshot: dict, url: str, token: str, *, opener=None) -> dict:
    """Authenticated raw-byte delivery; never follow a redirect with the token."""
    validate_publish_url(url)
    if not token or len(token) < 32:
        raise ValueError('dedicated Worker token must contain at least 32 characters')
    if not wire.validate_snapshot(snapshot):
        raise ValueError('cannot publish invalid canonical snapshot')
    body = (json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
    if len(body) > MAX_PUBLISH_BYTES:
        raise ValueError('wire exceeds dedicated cache payload limit')
    expected = hashlib.sha256(body).hexdigest()
    request = urllib.request.Request(url, data=body, method='POST', headers={
        'Authorization': f'Bearer {token}', 'Content-Type': 'application/json',
        'X-Body-SHA256': expected, 'User-Agent': 'BriefRooms-EURUSD-CB-Poller/1.0'})
    transport = opener or urllib.request.build_opener(_NoRedirect())
    with transport.open(request, timeout=10) as response:
        if response.status not in (200, 201):
            raise ValueError('dedicated cache rejected publication')
        result = json.loads(response.read(4097))
    if result.get('body_sha256') != expected:
        raise ValueError('dedicated cache raw-body receipt mismatch')
    return {'published': True, 'body_sha256': expected, 'received_at': result.get('received_at')}


def run_once(output: str | Path, *, client=None, clock=None, include_secondary=False,
             publish_url=None, token=None, publisher=publish_snapshot) -> dict[str, Any]:
    clock = clock or (lambda: datetime.now(timezone.utc))
    started = wire.dt(clock())
    if started is None:
        raise ValueError('aware service clock required')
    client = client or DeadlineClient()
    previous = wire.load_wire(output, now=started)
    calendar, coverage = wire.collect_calendars(now=started, previous=previous, client=client)
    checked = max(started, wire.dt(clock()), wire.dt(coverage['checked_at']))
    snapshot = wire.collect(now=checked, previous=previous, calendar_events=calendar,
        calendar_coverage=coverage, client=client, include_secondary=include_secondary)
    saved = wire.write_snapshot(output, snapshot)
    delivered = {'published': False, 'reason': 'DEDICATED_WORKER_NOT_CONFIGURED'}
    if publish_url:
        delivered = publisher(snapshot, publish_url, token or '')
    return {'status': snapshot['overall_status'], 'saved': saved,
        'snapshot_sha256': snapshot['integrity']['snapshot_sha256'],
        'generated_at': snapshot['generated_at'], 'target_poll_seconds': 60,
        'latency_claim': 'BEST_EFFORT_POLLING_NOT_LIVE_SPEECH', **delivered}


def run_service(output: str | Path, *, stop: threading.Event, interval_seconds=60,
                once=run_once, emit=print, monotonic=time.monotonic, **kwargs) -> None:
    if not 60 <= interval_seconds <= 3600:
        raise ValueError('poll interval must be between 60 and 3600 seconds')
    failures = 0
    while not stop.is_set():
        started = monotonic()
        try:
            result = once(output, **kwargs)
            emit(json.dumps(result, sort_keys=True))
            failures = 0
        except Exception as error:
            failures += 1
            # Log types only. HTTP errors may contain URLs or authorization data.
            emit(json.dumps({'status': 'ERROR', 'error_type': type(error).__name__,
                'reason': 'RECOVERY_REQUIRED', 'consecutive_failures': failures}))
        delay = min(interval_seconds * 2 ** min(failures, 4), 900)
        if stop.wait(max(1, delay - (monotonic() - started))):
            break


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/investments/eurusd_central_bank_wire.json')
    parser.add_argument('--interval-seconds', type=int, default=60)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--secondary', action='store_true', help='Only after separate public RSS terms approval')
    args = parser.parse_args()
    options = {'include_secondary': args.secondary,
        'publish_url': os.environ.get('EURUSD_CB_WIRE_PUBLISH_URL'),
        'token': os.environ.get('EURUSD_CB_WIRE_TOKEN')}
    if options['publish_url']:
        validate_publish_url(options['publish_url'])
    if args.once:
        print(json.dumps(run_once(args.output, **options), sort_keys=True))
        return 0
    stop = threading.Event()
    for value in (signal.SIGINT, signal.SIGTERM):
        signal.signal(value, lambda *_: stop.set())
    run_service(args.output, stop=stop, interval_seconds=args.interval_seconds, **options)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
