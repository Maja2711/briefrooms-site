#!/usr/bin/env python3
"""Make one minimal AI request and classify provider failures."""

from __future__ import annotations

import argparse
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
except ModuleNotFoundError:
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
    result = {
        "provider": runtime.provider,
        "endpoint": safe_endpoint(runtime.endpoint),
        "model": runtime.generation_model,
        **values,
    }
    if runtime.provider == "unavailable":
        result.update(
            {
                "required_secret": "GEMINI_API_KEY or OPENAI_API_KEY",
                "provider_note": "Gemini is preferred; OpenAI is optional fallback",
            }
        )
    return result


def _gemini_request(runtime: AiRuntime, post):
    url = f"{runtime.endpoint.rstrip('/')}/{runtime.generation_model}:generateContent"
    response = post(
        url,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-goog-api-key": runtime.api_key,
        },
        json={
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": 'Return only this JSON object: {"ok":true}'}],
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 32,
                "responseMimeType": "application/json",
            },
        },
        timeout=20,
    )
    return response, url


def _openai_compatible_request(runtime: AiRuntime, post):
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
            "max_tokens": 32,
        },
        timeout=20,
    )
    return response, runtime.endpoint


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
        if runtime.provider == "gemini":
            response, request_endpoint = _gemini_request(runtime, post)
        else:
            response, request_endpoint = _openai_compatible_request(runtime, post)
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
            safe_endpoint(request_endpoint),
            runtime.generation_model,
            status,
            error_class,
            permanent,
        )

    try:
        payload = response.json()
        if runtime.provider == "gemini":
            candidates = payload.get("candidates") or []
            parts = candidates[0]["content"]["parts"]
            content = "".join(str(part.get("text", "")) for part in parts)
        else:
            content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        raise PreflightError(
            runtime.provider,
            safe_endpoint(request_endpoint),
            runtime.generation_model,
            status,
            "invalid_provider_response",
            True,
        ) from exc
    if not str(content).strip():
        raise PreflightError(
            runtime.provider,
            safe_endpoint(request_endpoint),
            runtime.generation_model,
            status,
            "empty_provider_response",
            True,
        )
    return diagnostic(runtime, status="healthy", status_code=status)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-approved-cache",
        action="store_true",
        help="Continue in approved-cache-only mode when no AI provider is configured.",
    )
    args = parser.parse_args()
    runtime = get_ai_runtime()
    print(json.dumps(diagnostic(runtime, status="checking"), sort_keys=True))
    if not runtime.available and args.allow_approved_cache:
        print(
            json.dumps(
                diagnostic(
                    runtime,
                    status="approved_cache_only",
                    error_class="missing_provider_credentials",
                    permanent=True,
                ),
                sort_keys=True,
            )
        )
        return 0
    try:
        result = check_provider(runtime=runtime)
    except PreflightError as exc:
        result = diagnostic(
            runtime,
            status="failed" if exc.permanent else "degraded_source_only_allowed",
            status_code=exc.status_code,
            error_class=exc.error_class,
            permanent=exc.permanent,
        )
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        # A timeout, rate limit or temporary provider outage must not freeze the
        # source-linked news pages. The publication pipeline still validates any
        # visible AI comment strictly and can publish source-only cards.
        return 10 if exc.permanent else 0
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
