"""Gemini-first runtime adapter for the governed news publisher.

The adapter keeps the existing validation and publication contracts intact,
while translating the shared OpenAI-style transport into native Gemini API
requests. Gemini Free Tier is rate-limited per project, so all generation and
review calls share one bounded request clock.
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
    _last_gemini_request_at = 0.0

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
                    "GEMINI_REVIEW_MODEL", "gemini-3.5-flash-lite"
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

    def _pace_gemini_requests() -> None:
        global _last_gemini_request_at
        try:
            minimum_interval = max(
                0.0,
                float(os.getenv("GEMINI_MIN_INTERVAL_SECONDS", "7") or 7),
            )
        except ValueError:
            minimum_interval = 7.0
        elapsed = time.monotonic() - _last_gemini_request_at
        if minimum_interval and elapsed < minimum_interval:
            time.sleep(minimum_interval - elapsed)
        _last_gemini_request_at = time.monotonic()

    def _gemini_retry_delay(response, attempt: int) -> float:
        headers = getattr(response, "headers", {}) or {}
        retry_after = headers.get("Retry-After", "")
        try:
            return min(75.0, max(1.0, float(retry_after)))
        except (TypeError, ValueError):
            pass

        try:
            payload = response.json()
        except Exception:
            payload = {}
        details = (
            payload.get("error", {}).get("details", [])
            if isinstance(payload, dict)
            else []
        )
        for detail in details:
            if not isinstance(detail, dict):
                continue
            retry_delay = detail.get("retryDelay")
            if isinstance(retry_delay, str):
                match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)s", retry_delay.strip())
                if match:
                    return min(75.0, max(1.0, float(match.group(1))))
        return 65.0 if attempt == 0 else 75.0

    def _gemini_payload_shape(payload) -> str:
        if isinstance(payload, list):
            item_types = sorted({type(item).__name__ for item in payload})
            return f"list(len={len(payload)},item_types={','.join(item_types) or 'none'})"
        return type(payload).__name__

    def _normalize_gemini_json_payload(payload, messages):
        """Normalize only structurally valid Gemini JSON variants.

        Gemini occasionally returns the requested candidate collection as a
        top-level JSON array even when the prompt asks for
        {"candidates": [...]}. That array is normalized only for prompts that
        explicitly define a candidates collection. Other response shapes remain
        invalid so existing publication contracts stay strict.
        """
        if isinstance(payload, dict):
            return payload

        if isinstance(payload, str):
            nested = json.loads(payload)
            if nested == payload:
                raise ValueError(
                    "Gemini JSON string does not contain structured JSON"
                )
            return _normalize_gemini_json_payload(nested, messages)

        if isinstance(payload, list):
            prompt_text = "\n".join(
                str(message.get("content", ""))
                for message in (messages or [])
                if isinstance(message, dict)
            ).lower()
            expects_candidates = (
                '"candidates"' in prompt_text
                and (
                    "required_json_shape" in prompt_text
                    or "kandydat" in prompt_text
                    or "candidate" in prompt_text
                )
            )
            if expects_candidates and all(isinstance(item, dict) for item in payload):
                return {"candidates": payload}
            if len(payload) == 1 and isinstance(payload[0], dict):
                return payload[0]

        raise ValueError(
            "Gemini response is not a JSON object; "
            f"shape={_gemini_payload_shape(payload)}"
        )

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
            response = None
            try:
                _pace_gemini_requests()
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
                if status == 429 and attempt < 2:
                    time.sleep(_gemini_retry_delay(response, attempt))
                    continue
                if status in {500, 502, 503, 504} and attempt < 2:
                    time.sleep(float(5 * (2 ** attempt)))
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
                payload = _normalize_gemini_json_payload(json.loads(raw), messages)
                return payload
            except Exception as exc:
                last_error = exc
                permanent = status in _quality.PERMANENT_HTTP_STATUSES
                is_timeout = "timeout" in type(exc).__name__.lower()
                if permanent or attempt >= 2 or (is_timeout and attempt >= 1):
                    break

        if last_status == 429:
            raise _quality.AiRateLimitError(
                f"Gemini project quota remained exhausted after paced retries: {last_error}"
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
