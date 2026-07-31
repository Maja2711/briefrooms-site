#!/usr/bin/env python3
"""Make one minimal AI request and classify provider failures."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import requests

try:
    from comment_quality import (
        PERMANENT_HTTP_STATUSES,
        TRANSIENT_HTTP_STATUSES,
        AiRuntime,
        get_ai_runtime,
        provider_headers,
    )
except ModuleNotFoundError:  # Imported as scripts.check_ai_provider in tests.
    from scripts.comment_quality import (
        PERMANENT_HTTP_STATUSES,
        TRANSIENT_HTTP_STATUSES,
        AiRuntime,
        get_ai_runtime,
        provider_headers,
    )


@dataclass(frozen=True)
class PreflightError(RuntimeError):
    provider: str
    endpoint: str
    model: str
    status_code: int | None
    error_class: str
    permanent: bool

    def __str__(self) -> str:
        status = f" HTTP {self.status_code}" if self.status_code is not None else ""
        return f"{self.provider}{status}: {self.error_class}"


def safe_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def diagnostic(runtime: AiRuntime, **values) -> dict:
    return {
        "provider": runtime.provider,
        "endpoint": safe_endpoint(runtime.endpoint),
        "model": runtime.generation_model,
        **values,
    }


def check_provider(*, runtime: AiRuntime | None = None, post=None) -> dict:
    runtime = runtime or get_ai_runtime()
    post = post or requests.post
    if not runtime.available:
        raise PreflightError(
            runtime.provider,
            safe_endpoint(runtime.endpoint),
            runtime.generation_model,
            None,
            "missing_provider_credentials",
            True,
        )

    try:
        response = post(
            runtime.endpoint,
            headers=provider_headers(runtime),
            json={
                "model": runtime.generation_model,
                "messages": [
                    {
                        "role": "user",
                        "content": 'Return only this JSON object: {"ok":true}',
                    }
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 12,
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise PreflightError(
            runtime.provider,
            safe_endpoint(runtime.endpoint),
            runtime.generation_model,
            None,
            type(exc).__name__,
            False,
        ) from exc

    status = int(getattr(response, "status_code", 0) or 0)
    if status < 200 or status >= 300:
        permanent = status in PERMANENT_HTTP_STATUSES
        error_class = (
            "permanent_http_error"
            if permanent
            else "transient_http_error"
            if status in TRANSIENT_HTTP_STATUSES or status >= 500
            else "unexpected_http_error"
        )
        raise PreflightError(
            runtime.provider,
            safe_endpoint(runtime.endpoint),
            runtime.generation_model,
            status,
            error_class,
            permanent,
        )

    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise PreflightError(
            runtime.provider,
            safe_endpoint(runtime.endpoint),
            runtime.generation_model,
            status,
            "invalid_provider_response",
            True,
        ) from exc
    if not str(content).strip():
        raise PreflightError(
            runtime.provider,
            safe_endpoint(runtime.endpoint),
            runtime.generation_model,
            status,
            "empty_provider_response",
            True,
        )
    return diagnostic(runtime, status="healthy", status_code=status)


def main() -> int:
    runtime = get_ai_runtime()
    print(json.dumps(diagnostic(runtime, status="checking"), sort_keys=True))
    try:
        result = check_provider(runtime=runtime)
    except PreflightError as exc:
        result = diagnostic(
            runtime,
            status="failed",
            status_code=exc.status_code,
            error_class=exc.error_class,
            permanent=exc.permanent,
        )
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        return 10 if exc.permanent else 11
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
