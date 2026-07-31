"""Runtime provider adapter loaded through PYTHONPATH in the news workflow.

Gemini is preferred when GEMINI_API_KEY is configured. Existing OpenAI
support remains an optional fallback. This module deliberately patches only
the shared AI transport used by the news publisher; validation, deduplication,
cache integrity and atomic publication remain unchanged.
"""

from __future__ import annotations

import json
import os
import re
import time

try:
    import comment_quality as _quality
except Exception:
    _quality = None


if _quality is not None:
    _original_get_ai_runtime = _quality.get_ai_runtime
    _original_request_json_completion = _quality.request_json_completion

    def _get_ai_runtime():
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        if gemini_key:
            return _quality.AiRuntime(
                provider="gemini",
                api_key=gemini_key,
                endpoint=os.getenv(
                    "GEMINI_API_ENDPOINT",
                    "https://generativelanguage.googleapis.com/v1beta/models",
                ).rstrip("/"),
                generation_model=os.getenv(
                    "GEMINI_GENERATION_MODEL", "gemini-3.5-flash-lite"
                ).strip(),
                review_model=os.getenv(
                    "GEMINI_REVIEW_MODEL", "gemini-3.5-flash"
                ).strip(),
            )
        return _original_get_ai_runtime()

    def _gemini_contents(messages: list[dict[str, str]]):
        system_parts: list[str] = []
        contents: list[dict] = []
        for message in messages:
            role = str(message.get("role", "user"))
            text = str(message.get("content", ""))
            if role == "system":
                if text:
                    system_parts.append(text)
                continue
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": text}]})
        if not contents:
            contents = [{"role": "user", "parts": [{"text": "Return valid JSON."}]}]
        system_instruction = None
        if system_parts:
            system_instruction = {"parts": [{"text": "\n\n".join(system_parts)}]}
        return contents, system_instruction

    def _request_json_completion(
        *,
        post,
        runtime,
        messages,
        max_tokens,
        temperature,
        review=False,
        timeout=40,
    ):
        if runtime.provider != "gemini":
            return _original_request_json_completion(
                post=post,
                runtime=runtime,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                review=review,
                timeout=timeout,
            )

        if not runtime.available:
            raise RuntimeError("AI provider is unavailable")

        model = runtime.review_model if review else runtime.generation_model
        url = f"{runtime.endpoint}/{model}:generateContent"
        contents, system_instruction = _gemini_contents(messages)
        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }
        if system_instruction:
            body["systemInstruction"] = system_instruction

        last_error = None
        last_status = None
        for attempt in range(3):
            status = None
            try:
                response = post(
                    url,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "x-goog-api-key": runtime.api_key,
                    },
                    json=body,
                    timeout=timeout,
                )
                status = int(getattr(response, "status_code", 200) or 200)
                last_status = status
                if status in _quality.TRANSIENT_HTTP_STATUSES and attempt < 2:
                    headers = getattr(response, "headers", {})
                    retry_after = headers.get("Retry-After", "")
                    try:
                        delay = max(1.0, float(retry_after))
                    except (TypeError, ValueError):
                        delay = float(5 * (2 ** attempt))
                    time.sleep(min(20.0, delay))
                    continue
                response.raise_for_status()
                data = response.json()
                candidates = data.get("candidates") or []
                if not candidates:
                    raise ValueError(
                        f"Gemini response has no candidates: {json.dumps(data)[:600]}"
                    )
                parts = (
                    candidates[0].get("content", {}).get("parts", [])
                    if isinstance(candidates[0], dict)
                    else []
                )
                raw = "".join(
                    str(part.get("text", ""))
                    for part in parts
                    if isinstance(part, dict)
                ).strip()
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S)
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("Gemini response is not a JSON object")
                return payload
            except Exception as exc:
                last_error = exc
                permanent = status in _quality.PERMANENT_HTTP_STATUSES
                is_timeout = "timeout" in type(exc).__name__.lower()
                if permanent or attempt >= 2 or (is_timeout and attempt >= 1):
                    break

        if last_status == 429:
            raise _quality.AiRateLimitError(
                f"Gemini rate limit remained active after bounded retries: {last_error}"
            ) from last_error
        if last_status in _quality.PERMANENT_HTTP_STATUSES:
            raise _quality.AiPermanentError(
                last_status,
                f"Gemini returned permanent HTTP {last_status}; request was not retried: {last_error}",
            ) from last_error
        if last_status in _quality.TRANSIENT_HTTP_STATUSES:
            raise _quality.AiTransientError(
                f"Gemini remained unavailable after bounded retries (HTTP {last_status}): {last_error}"
            ) from last_error
        raise RuntimeError(f"Gemini request failed after retries: {last_error}") from last_error

    _quality.get_ai_runtime = _get_ai_runtime
    _quality.request_json_completion = _request_json_completion
