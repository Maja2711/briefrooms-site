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

    _PL_OUTLOOK_VERIFICATION_REGISTRY = [
        {"name": "GUS", "url": "https://stat.gov.pl/", "uses": ["official_indicator"]},
        {"name": "NBP", "url": "https://nbp.pl/", "uses": ["official_indicator", "official_decision", "market_indicator"]},
        {"name": "Gov.pl", "url": "https://www.gov.pl/", "uses": ["official_decision", "policy_implementation", "regulatory_milestone"]},
        {"name": "Sejm RP", "url": "https://www.sejm.gov.pl/", "uses": ["official_decision", "regulatory_milestone"]},
        {"name": "Dziennik Ustaw", "url": "https://dziennikustaw.gov.pl/", "uses": ["policy_implementation", "regulatory_milestone"]},
        {"name": "KNF", "url": "https://www.knf.gov.pl/", "uses": ["official_decision", "regulatory_milestone", "market_indicator"]},
        {"name": "UOKiK", "url": "https://uokik.gov.pl/", "uses": ["official_decision", "regulatory_milestone"]},
        {"name": "Eurostat", "url": "https://ec.europa.eu/eurostat/", "uses": ["official_indicator"]},
        {"name": "ECB", "url": "https://www.ecb.europa.eu/", "uses": ["official_indicator", "official_decision", "market_indicator"]},
        {"name": "European Commission", "url": "https://ec.europa.eu/", "uses": ["official_decision", "policy_implementation", "regulatory_milestone"]},
        {"name": "Council of the EU", "url": "https://www.consilium.europa.eu/", "uses": ["official_decision", "regulatory_milestone"]},
        {"name": "EUR-Lex", "url": "https://eur-lex.europa.eu/", "uses": ["policy_implementation", "regulatory_milestone"]},
        {"name": "Court of Justice of the European Union", "url": "https://curia.europa.eu/", "uses": ["official_decision"]},
        {"name": "EMA", "url": "https://www.ema.europa.eu/", "uses": ["official_decision", "clinical_endpoint", "regulatory_milestone"]},
        {"name": "ClinicalTrials.gov", "url": "https://clinicaltrials.gov/", "uses": ["clinical_endpoint"]},
        {"name": "WHO", "url": "https://www.who.int/", "uses": ["official_indicator", "public_health"]},
        {"name": "NASA", "url": "https://www.nasa.gov/", "uses": ["scientific_result"]},
        {"name": "ESA", "url": "https://www.esa.int/", "uses": ["scientific_result"]},
    ]
    _CONTENT_CATEGORIES_BY_AREA = {
        "economy": ["macro", "market_investment", "regulatory"],
        "geopolitics": ["geopolitics"],
        "health": ["public_health", "clinical_health"],
        "science": ["technology", "science_research"],
    }

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

    def _registry_for(*forecast_types: str) -> list[dict]:
        wanted = set(forecast_types)
        return [
            {"name": row["name"], "url": row["url"]}
            for row in _PL_OUTLOOK_VERIFICATION_REGISTRY
            if wanted.intersection(row.get("uses") or [])
        ]

    def _pl_candidate_opportunities(sources) -> list[dict]:
        opportunities: list[dict] = []
        for source in sources or []:
            if not isinstance(source, dict):
                continue
            source_id = source.get("id")
            source_area = str(source.get("area") or "").strip()
            if source_area not in _CONTENT_CATEGORIES_BY_AREA:
                continue
            compatible_categories = _CONTENT_CATEGORIES_BY_AREA[source_area]
            title = re.sub(r"\s+", " ", str(source.get("title") or "")).strip()
            summary = re.sub(r"\s+", " ", str(source.get("summary") or "")).strip()
            text = f"{title} {summary}".lower()
            if not title:
                continue

            scheduled = re.search(
                r"\b(referend\w*|głosowan\w*|wybor\w*|decyzj\w*|"
                r"negocjacj\w*|zatwierdz\w*|podpis\w*|wejd\w* w życie|"
                r"przystąpi\w*|przyjęci\w*|uchwal\w*)\b",
                text,
                re.IGNORECASE,
            )
            date_signal = re.search(
                r"\b(?:\d{1,2}\s+(?:stycznia|lutego|marca|kwietnia|maja|czerwca|"
                r"lipca|sierpnia|września|października|listopada|grudnia)|20\d{2})\b",
                text,
                re.IGNORECASE,
            )
            if scheduled and date_signal:
                opportunities.append({
                    "source_id": source_id,
                    "source_area": source_area,
                    "compatible_content_categories": compatible_categories,
                    "preferred_content_category": "regulatory" if source_area == "economy" else compatible_categories[0],
                    "kind": "scheduled_binary_or_regulatory_event",
                    "observed_evidence": (title + ". " + summary)[:700],
                    "allowed_forecast_types": ["official_decision", "policy_implementation", "regulatory_milestone"],
                    "contract_hint": {"baseline_value": 0, "threshold": 1, "unit": "zdarzenie binarne", "comparison_operator": ">="},
                    "verification_choices": _registry_for("official_decision", "policy_implementation", "regulatory_milestone"),
                    "instruction": "Na podstawie źródła oceń najbardziej prawdopodobny przyszły wynik. area musi być identyczne z source_area, a content_category musi pochodzić z compatible_content_categories.",
                })

            indicator_terms = re.search(
                r"\b(inflacj\w*|pkb|bezroboci\w*|sprzedaż\w*|rentownoś\w*|stop\w* procent\w*|produkcj\w*|wynagrodzeni\w*|obligacj\w*|deficyt\w*|eksport\w*|import\w*)\b",
                text,
                re.IGNORECASE,
            )
            numeric = re.findall(r"(?<!\d)(\d{1,3}(?:[\s.,]\d{1,3})?)\s*(%|proc\.|mld|mln|pb)?", text, re.IGNORECASE)
            if indicator_terms and numeric:
                observed_numbers = [(number + (" " + unit if unit else "")).strip() for number, unit in numeric[:4]]
                preferred = "macro" if source_area == "economy" and "macro" in compatible_categories else compatible_categories[0]
                opportunities.append({
                    "source_id": source_id,
                    "source_area": source_area,
                    "compatible_content_categories": compatible_categories,
                    "preferred_content_category": preferred,
                    "kind": "official_or_market_indicator_with_numeric_evidence",
                    "observed_evidence": (title + ". " + summary)[:700],
                    "observed_numbers": observed_numbers,
                    "allowed_forecast_types": ["official_indicator", "market_indicator"],
                    "verification_choices": _registry_for("official_indicator", "market_indicator"),
                    "instruction": "Użyj tylko liczby jednoznacznie opisanej w źródle jako baseline. area musi być identyczne z source_area, a content_category musi pochodzić z compatible_content_categories.",
                })

        deduped: list[dict] = []
        seen = set()
        for row in opportunities:
            key = (row.get("source_id"), row.get("kind"))
            if key not in seen:
                seen.add(key)
                deduped.append(row)
        return deduped[:12]

    def _augment_pl_outlook_candidate_messages(messages):
        augmented = []
        for message in messages or []:
            if not isinstance(message, dict):
                augmented.append(message)
                continue
            copy = dict(message)
            if str(copy.get("role", "")) != "user":
                augmented.append(copy)
                continue
            try:
                payload = json.loads(str(copy.get("content", "")))
            except Exception:
                augmented.append(copy)
                continue
            shape = payload.get("required_json_shape") if isinstance(payload, dict) else None
            is_pl_candidate_prompt = (
                isinstance(payload, dict)
                and payload.get("language") == "pl"
                and isinstance(shape, dict)
                and "candidates" in shape
                and isinstance(payload.get("sources"), list)
            )
            if not is_pl_candidate_prompt:
                augmented.append(copy)
                continue

            opportunities = _pl_candidate_opportunities(payload.get("sources"))
            payload["official_verification_registry"] = _PL_OUTLOOK_VERIFICATION_REGISTRY
            payload["candidate_opportunities"] = opportunities
            payload["dimension_scoring_guide"] = {
                "scale": "0-100; oceniaj faktycznie, nie pozostawiaj wartości przykładowych 0",
                "evidence_quality": "siła i bezpośredniość dowodów w cytowanych źródłach",
                "measurability": "czy rezultat ma jedną precyzyjną metrykę, próg i termin",
                "causal_strength": "czy źródła dają racjonalny mechanizm prowadzący do prognozowanego wyniku",
                "verifiability": "czy wynik da się jednoznacznie sprawdzić w podanym oficjalnym źródle",
                "novelty": "czy prognoza nie powtarza recent_titles_to_avoid",
                "speculation_risk": "ryzyko, że prognoza wykracza poza dostarczone dowody; niżej = lepiej",
            }
            try:
                example = payload["required_json_shape"]["candidates"][0]
                example.update({
                    "evidence_quality": 75,
                    "measurability": 90,
                    "causal_strength": 70,
                    "verifiability": 90,
                    "novelty": 70,
                    "speculation_risk": 30,
                })
            except Exception:
                pass
            payload["task"] = (
                "Zwróć od 1 do 10 kandydatów, ale tylko takich, które da się uczciwie rozstrzygnąć. "
                "Jeżeli candidate_opportunities nie jest puste, wybierz co najmniej jedną okazję. "
                "Dla niej skopiuj source_area dokładnie do area i wybierz content_category wyłącznie z compatible_content_categories. "
                "Dla decyzji użyj kontraktu binarnego baseline_value=0, threshold=1, unit='zdarzenie binarne'. "
                "Dla wskaźnika ciągłego baseline musi pochodzić dosłownie ze źródła. "
                "Wypełnij sześć pól jakości rzeczywistymi ocenami 0-100 według dimension_scoring_guide; nie kopiuj zer ani wartości przykładowych bez oceny."
            )
            rules = list(payload.get("hard_rules") or [])
            rules.extend([
                "verification_url wybierz dokładnie z official_verification_registry; nie twórz innego adresu",
                "data_source_for_verification musi odpowiadać nazwie wybranego wpisu official_verification_registry",
                "jeżeli korzystasz z candidate_opportunities, source_ids muszą zawierać wskazany source_id",
                "dla candidate_opportunities area musi równać się source_area, a content_category musi należeć do compatible_content_categories",
                "evidence_quality, measurability, causal_strength, verifiability, novelty i speculation_risk muszą być uczciwymi liczbami 0-100, nie placeholderami",
                "wysoka ocena nie jest celem sama w sobie; odrzuć słaby pomysł zamiast zawyżać punktację",
                "nie wolno prognozować liczby artykułów, komunikatów, publikacji ani zainteresowania tematem",
            ])
            payload["hard_rules"] = rules
            copy["content"] = json.dumps(payload, ensure_ascii=False)
            augmented.append(copy)
        return augmented

    def _pace_gemini_requests() -> None:
        global _last_gemini_request_at
        try:
            minimum_interval = max(0.0, float(os.getenv("GEMINI_MIN_INTERVAL_SECONDS", "7") or 7))
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
        details = payload.get("error", {}).get("details", []) if isinstance(payload, dict) else []
        for detail in details:
            if isinstance(detail, dict) and isinstance(detail.get("retryDelay"), str):
                match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)s", detail["retryDelay"].strip())
                if match:
                    return min(75.0, max(1.0, float(match.group(1))))
        return 65.0 if attempt == 0 else 75.0

    def _gemini_payload_shape(payload) -> str:
        if isinstance(payload, list):
            item_types = sorted({type(item).__name__ for item in payload})
            return f"list(len={len(payload)},item_types={','.join(item_types) or 'none'})"
        return type(payload).__name__

    def _normalize_gemini_json_payload(payload, messages):
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            nested = json.loads(payload)
            if nested == payload:
                raise ValueError("Gemini JSON string does not contain structured JSON")
            return _normalize_gemini_json_payload(nested, messages)
        if isinstance(payload, list):
            prompt_text = "\n".join(str(message.get("content", "")) for message in (messages or []) if isinstance(message, dict)).lower()
            expects_candidates = '"candidates"' in prompt_text and ("required_json_shape" in prompt_text or "kandydat" in prompt_text or "candidate" in prompt_text)
            if expects_candidates and all(isinstance(item, dict) for item in payload):
                return {"candidates": payload}
            if len(payload) == 1 and isinstance(payload[0], dict):
                return payload[0]
        raise ValueError("Gemini response is not a JSON object; " f"shape={_gemini_payload_shape(payload)}")

    def _request_json_completion(*, post, runtime, messages, max_tokens, temperature, review=False, timeout=40):
        if runtime.provider != "gemini":
            return _original_request_json_completion(
                post=post, runtime=runtime, messages=messages, max_tokens=max_tokens,
                temperature=temperature, review=review, timeout=timeout,
            )
        if not runtime.available:
            raise RuntimeError("AI provider is unavailable")

        effective_messages = _augment_pl_outlook_candidate_messages(messages)
        model = runtime.review_model if review else runtime.generation_model
        url = f"{runtime.endpoint}/{model}:generateContent"
        contents, system_instruction = _gemini_contents(effective_messages)
        body = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens, "responseMimeType": "application/json"}}
        if system_instruction:
            body["systemInstruction"] = system_instruction

        last_error = None
        last_status = None
        for attempt in range(3):
            status = None
            try:
                _pace_gemini_requests()
                response = post(url, headers={"Accept": "application/json", "Content-Type": "application/json", "x-goog-api-key": runtime.api_key}, json=body, timeout=timeout)
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
                    raise ValueError(f"Gemini response has no candidates: {json.dumps(data)[:600]}")
                parts = candidates[0].get("content", {}).get("parts", []) if isinstance(candidates[0], dict) else []
                raw = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict)).strip()
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S)
                return _normalize_gemini_json_payload(json.loads(raw), effective_messages)
            except Exception as exc:
                last_error = exc
                permanent = status in _quality.PERMANENT_HTTP_STATUSES
                is_timeout = "timeout" in type(exc).__name__.lower()
                if permanent or attempt >= 2 or (is_timeout and attempt >= 1):
                    break

        if last_status == 429:
            raise _quality.AiRateLimitError(f"Gemini project quota remained exhausted after paced retries: {last_error}") from last_error
        if last_status in _quality.PERMANENT_HTTP_STATUSES:
            raise _quality.AiPermanentError(last_status, f"Gemini returned permanent HTTP {last_status}; request was not retried: {last_error}") from last_error
        if last_status in _quality.TRANSIENT_HTTP_STATUSES:
            raise _quality.AiTransientError(f"Gemini remained unavailable after bounded retries (HTTP {last_status}): {last_error}") from last_error
        raise RuntimeError(f"Gemini request failed after retries: {last_error}") from last_error

    _quality.get_ai_runtime = _get_ai_runtime
    _quality.request_json_completion = _request_json_completion
