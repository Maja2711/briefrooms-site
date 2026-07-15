#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared fail-closed quality contract for PL and EN article comments."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import time

QUALITY_VERSION = 7
QUALITY_STATUS = f"passed_strict_v{QUALITY_VERSION}"
MIN_SENTENCES = 3
MAX_SENTENCES = 4
MIN_COMMENT_CHARS = 180
MAX_COMMENT_CHARS = 1600

REPLACEMENTS = {
    "Å": "ł", "Å": "Ł", "Å¼": "ż", "Å»": "Ż", "Åº": "ź", "Å¹": "Ź",
    "Å": "ś", "Åš": "Ś", "Å„": "ń", "Å": "ń", "Ã³": "ó", "Ã": "Ó",
    "Ä": "ę", "Ä": "Ę", "Ä…": "ą", "Ä„": "Ą", "Ä": "ć", "Ä": "Ć",
    "â": "–", "â": "—", "â": "„", "â": "”", "â": "“", "â": "’", "Â": "",
}

MOJIBAKE = re.compile(
    r"(?:Å[\x80-\x9f\u2010-\u203a]|Ä[\x80-\x9f\u2010-\u203a]|"
    r"Ã[\x80-\x9f\u00a0-\u00bf]|Â[\x80-\x9f\u00a0-\u00bf]|"
    r"â[\x80-\x9f\u2010-\u203a]|\ufffd|[\x80-\x9f])"
)
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
HTML_OR_URL = re.compile(r"<[^>]+>|https?://|www\.", re.I)
BAD_FRAGMENT = re.compile(
    r"fotonews|shutterstock|autor:|oprac\.|czytaj także|zobacz także|"
    r"skom(?:entuj|entował|entowała)|image source|image caption|photo credit|"
    r"homepage skip|accessibility help|cookie settings|sign up|subscribe|"
    r"pełne tło|źródłem wpisu|najważniejszy sygnał|the source is|full context|"
    r"main signal belongs|(?:^|\s)pap(?:\s|$)",
    re.I,
)

BAD_START_PL = re.compile(
    r"^(?:[.,;:!?%‰/\\)\]}]|zł\b|tys\.\b|mln\b|mld\b|proc\.\b|"
    r"dodał\b|dodała\b|dodali\b|zaznaczył\b|zaznaczyła\b|powiedział\b|"
    r"powiedziała\b|stwierdził\b|stwierdziła\b|ocenił\b|oceniła\b|"
    r"wskazał\b|wskazała\b|jak podkreślono\b|jak zaznaczono\b|"
    r"jak poinformowano\b|jak przekazano\b|placówka zaznaczyła\b)",
    re.I,
)
BAD_START_EN = re.compile(
    r"^(?:[.,;:!?%‰/\\)\]}]|usd\b|eur\b|gbp\b|and\b|or\b|but\b|"
    r"because\b|which\b|he added\b|she added\b|they added\b|"
    r"he said\b|she said\b|according to him\b|according to her\b)",
    re.I,
)

# Missing Polish characters are not mojibake and need their own hard rejection.
# These stems cover recurring publisher extraction failures, including the exact
# production regression reported on 2026-07-15.
BROKEN_PL = re.compile(
    r"\b(?:podkrel\w*|podkresl\w*|zuyw\w*|zuzyw\w*|iloci\w*|ilosc\w*|"
    r"zadeklarowaa|dopuci\w*|kosztw|mieszkanc\w*|spoleczenstw\w*|"
    r"prad(?:u|em|zie)?|zrod(?:lo|la|lem|lach)|wladz(?:a|e|om|ami)?|"
    r"mieszkańcw|rzeczywicie|spoeczeństw\w*|elazn\w*|decyzj\b|popary\w*|"
    r"politykw|spoeczn\w*|wygl da|zatrzyma si|wy cznie|w rod|poinformowa\b|"
    r"wyl dowaniu|znajdowa si|zaznaczy, e|zostaa|wysana|caej|zdjcie|ktrej|"
    r"wrci|stanw|moliwo\w*|przewodnicz cy|wyjani|zagraaj cym|dziaania|"
    r"dostpn\w*|okrelenie|wraenie|udowodnienie, e|probwki|zwierztach|"
    r"porwnawcze|wyleczony t metod|rz du|mwi|m wi|koz w|obowi zek|rozwi za|"
    r"dotycz cych|p ac|kilkadziesi t|ochron zdrowia|zosta y|zosta o|"
    r"przekaza a|podkre li|g os|w ród|niektrz|tak e|take nie)\b",
    re.I,
)
BROKEN_EN = re.compile(
    r"\b(?:don t|doesn t|didn t|isn t|aren t|wasn t|weren t|couldn t|"
    r"wouldn t|shouldn t|won t|can t|it s|they re|we re|you re)\b",
    re.I,
)
TRUNCATED_LOWERCASE_FRAGMENT = re.compile(
    r",\s*[a-z\u0105\u0107\u0119\u0142\u0144\u00f3\u015b\u017a\u017c]\.(?=\s+[A-Z\u0104\u0106\u0118\u0141\u0143\u00d3\u015a\u0179\u017b])"
)
MALFORMED_QUOTE_SPACING = re.compile(
    r"[.!?]\s+[\"'\u2019\u201d](?=\s|[A-Z\u0104\u0106\u0118\u0141\u0143\u00d3\u015a\u0179\u017b])"
)
BAD_PUNCTUATION_SPACING = re.compile(r"\s+[,.!?;:](?=\s|$)")
BAD_GRAMMAR_PL = re.compile(
    r"\bprzez\s+(?:telewizj\u0105|agencj\u0105|redakcj\u0105)\b",
    re.I,
)
BAD_TENSE_PL = re.compile(
    r"\b(?:w\u0142a\u015bnie|ju\u017c)\s+odby\u0142o\s+si\u0119\b[^.]{0,180}\bkt\u00f3re\s+okre\u015bli\b",
    re.I,
)
KNOWN_STATUS_CONFLICT_PL = re.compile(r"\bby\u0142y\s+prezydent\s+(?:Donald\s+)?Trump\b", re.I)
KNOWN_STATUS_CONFLICT_EN = re.compile(r"\bformer\s+president\s+(?:Donald\s+)?Trump\b", re.I)
BYLINE_PL = re.compile(
    r"^(?:(?:[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż-]+|[A-ZĄĆĘŁŃÓŚŹŻ]\.)\s+){1,5}"
    r"[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż-]+\s*/+",
)
BYLINE_EN = re.compile(r"^(?:By\s+)?(?:[A-Z][a-z-]+\s+){1,4}[A-Z][a-z-]+\s*/+")

PL_ALLOWED_ONE = {"a", "i", "o", "u", "w", "z"}
PL_INVALID_TWO = {"cz"}
DUPLICATE_STOPWORDS = {
    "and", "are", "for", "from", "has", "have", "that", "the", "their", "this", "was", "were", "with",
    "oraz", "jest", "ktory", "ktora", "ktore", "ktorych", "przez", "sie", "tego", "tym", "dla", "jako",
    "kt\u00f3ry", "kt\u00f3ra", "kt\u00f3re", "kt\u00f3rych", "si\u0119",
}


@dataclass(frozen=True)
class QualityResult:
    valid: bool
    text: str
    sentences: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class AiRuntime:
    provider: str
    api_key: str
    endpoint: str
    generation_model: str
    review_model: str

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.endpoint and self.generation_model and self.review_model)


def get_ai_runtime() -> AiRuntime:
    """Prefer a configured OpenAI key, then GitHub Models in Actions."""
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openai_key:
        generation_model = (
            os.getenv("NEWS_AI_MODEL")
            or os.getenv("BRIEFROOMS_AI_MODEL")
            or "gpt-4o"
        )
        review_model = os.getenv("NEWS_AI_REVIEW_MODEL") or "gpt-4.1"
        return AiRuntime(
            provider="openai",
            api_key=openai_key,
            endpoint=os.getenv("OPENAI_CHAT_ENDPOINT", "https://api.openai.com/v1/chat/completions"),
            generation_model=generation_model,
            review_model=review_model,
        )

    github_token = os.getenv("GITHUB_MODELS_TOKEN", "").strip()
    if github_token:
        return AiRuntime(
            provider="github-models",
            api_key=github_token,
            endpoint=os.getenv(
                "GITHUB_MODELS_ENDPOINT",
                "https://models.github.ai/inference/chat/completions",
            ),
            generation_model=os.getenv("GITHUB_MODELS_MODEL", "openai/gpt-4o"),
            review_model=os.getenv("GITHUB_MODELS_REVIEW_MODEL", "openai/gpt-4.1"),
        )

    return AiRuntime("unavailable", "", "", "", "")


def request_json_completion(
    *,
    post,
    runtime: AiRuntime,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    review: bool = False,
    timeout: int = 40,
) -> dict:
    """Call an OpenAI-compatible endpoint and parse one strict JSON response."""
    if not runtime.available:
        raise RuntimeError("AI provider is unavailable")

    model = runtime.review_model if review else runtime.generation_model
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = post(
                runtime.endpoint,
                headers={
                    "Accept": "application/vnd.github+json" if runtime.provider == "github-models" else "application/json",
                    "Authorization": f"Bearer {runtime.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=timeout,
            )
            status = int(getattr(response, "status_code", 200) or 200)
            if status in {429, 500, 502, 503, 504} and attempt < 3:
                retry_after = getattr(response, "headers", {}).get("Retry-After", "")
                try:
                    delay = min(20.0, max(1.0, float(retry_after)))
                except (TypeError, ValueError):
                    delay = float(2 ** attempt)
                time.sleep(delay)
                continue
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"].strip()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S)
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("AI response is not a JSON object")
            return payload
        except Exception as exc:
            last_error = exc
            is_timeout = "timeout" in type(exc).__name__.lower()
            if attempt >= 3 or (is_timeout and attempt >= 1):
                break
    raise RuntimeError(f"AI request failed after retries: {last_error}") from last_error


def normalize_text(value: str) -> str:
    text = str(value or "")
    for bad, good in REPLACEMENTS.items():
        text = text.replace(bad, good)
    text = re.sub(r"\s+", " ", text).strip(" -–—·•/\t\n\r")
    return text


def _abbreviation_at_end(value: str) -> bool:
    return bool(re.search(
        r"(?:\b(?:e\.g|i\.e|m\.in|dr|prof|np|itd|itp|proc|tys|mln|mld|nr|art|ust|pkt|godz|"
        r"mr|mrs|ms|vs|etc|p\.m|a\.m|r|m)\.|(?:\b[A-Z]\.){1,4})$",
        value,
        re.I,
    ))


def clip_complete_text(value: str, limit: int) -> str:
    """Clip source material only after a complete sentence, never mid-sentence."""
    text = normalize_text(value)
    if len(text) <= limit:
        return text

    prefix = text[:limit].rstrip()
    boundaries = list(re.finditer(r"[.!?](?:[\"'\u2019\u201d)\]]+)?(?=\s|$)", prefix))
    for boundary in reversed(boundaries):
        candidate = prefix[:boundary.end()].strip()
        if len(candidate) >= 55 and not _abbreviation_at_end(candidate):
            return candidate
    return ""


def decode_http_response(response) -> str:
    """Decode response bytes without trusting a misleading HTTP charset."""
    raw = bytes(getattr(response, "content", b"") or b"")
    if not raw:
        return str(getattr(response, "text", "") or "")

    candidates: list[str] = []
    head = raw[:4096]
    meta = re.search(br"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", head, re.I)
    if meta:
        candidates.append(meta.group(1).decode("ascii", errors="ignore"))
    candidates.append("utf-8")

    declared = str(getattr(response, "encoding", "") or "").strip()
    if declared:
        candidates.append(declared)
    try:
        apparent = str(getattr(response, "apparent_encoding", "") or "").strip()
        if apparent:
            candidates.append(apparent)
    except Exception:
        pass
    candidates.extend(("windows-1250", "iso-8859-2", "windows-1252"))

    decoded: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for position, encoding in enumerate(candidates):
        key = encoding.lower().replace("_", "-")
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            text = raw.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
        defects = (
            text.count("\ufffd") * 1000
            + len(MOJIBAKE.findall(text)) * 100
            + len(CONTROL.findall(text)) * 100
            + len(re.findall(r"(?:Ã.|Å.|Ä.|â€)", text)) * 50
        )
        decoded.append((defects, position, text))
    if decoded:
        return min(decoded, key=lambda item: (item[0], item[1]))[2]
    return raw.decode("utf-8", errors="replace")


def independent_ai_review(
    *,
    post,
    runtime: AiRuntime,
    title: str,
    source_text: str,
    summary: str,
    lang: str,
) -> tuple[bool, str]:
    """Run the mandatory second-pass review without importing an HTTP client."""
    if not runtime.available:
        return False, "missing_ai_provider"
    if lang == "pl":
        instruction = (
            "Oceń komentarz jako niezależny redaktor. Zatwierdź go tylko wtedy, gdy wszystkie zdania są poprawne "
            "po polsku, logiczne, kompletne i łatwe do zrozumienia, bez brakujących liter, porozcinanych wyrazów "
            "oraz fragmentów interfejsu wydawcy, a wszystkie fakty wynikają z materiału źródłowego. "
            "Jedna wada oznacza approved=false."
        )
    else:
        instruction = (
            "Review the comment as an independent editor. Approve it only if every sentence is grammatical, coherent, "
            "complete and easy to understand, with no missing characters, split words or publisher UI fragments, and "
            "every claim is supported by the source material. One defect means approved=false."
        )
    defect_rule = (
        "Reject any grammar or inflection error, clipped fragment, malformed quotation, punctuation defect, "
        "or repeated information. One defect means approved=false."
    )
    prompt = (
        f"{instruction} {defect_rule}\n"
        "Return only JSON {\"approved\":true|false,\"reason\":\"short reason\"}.\n\n"
        f"Title: {title}\n\nSource material:\n{clip_complete_text(source_text, 4500)}\n\nComment to review:\n{summary}"
    )
    try:
        payload = request_json_completion(
            post=post,
            runtime=runtime,
            messages=[
                {"role": "system", "content": "You are an independent, conservative publication quality reviewer."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=120,
            temperature=0,
            review=True,
            timeout=35,
        )
        if payload.get("approved") is True:
            return True, str(payload.get("reason") or "approved")
        return False, str(payload.get("reason") or "rejected")
    except Exception as exc:
        return False, f"review_error:{type(exc).__name__}:{exc}"


def independent_ai_review_batch(
    *,
    post,
    runtime: AiRuntime,
    entries: list[dict[str, str]],
    lang: str,
) -> dict[str, tuple[bool, str]]:
    """Review many generated comments in one separate-model request."""
    default_reason = "missing_review" if runtime.available else "missing_ai_provider"
    results = {
        str(entry.get("id", "")): (False, default_reason)
        for entry in entries
        if str(entry.get("id", ""))
    }
    if not runtime.available or not entries:
        return results

    if lang == "pl":
        instruction = (
            "Jesteś niezależnym, bardzo ostrożnym redaktorem. Dla każdego elementu zatwierdź komentarz tylko, "
            "gdy jest w pełni poprawny po polsku, logiczny, kompletny i łatwy do zrozumienia, nie ma brakujących "
            "liter ani porozcinanych wyrazów, a wszystkie fakty wynikają z materiału źródłowego."
        )
    else:
        instruction = (
            "You are an independent, conservative editor. Approve each comment only when it is fully grammatical, "
            "coherent, complete and easy to understand, contains no damaged or split words, and every claim is "
            "supported by the source material."
        )

    instruction += (
        " Reject any grammar or inflection error, clipped fragment, malformed quotation, punctuation defect, "
        "or repeated information. One defect means approved=false."
    )

    chunks: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    current_chars = 0
    for entry in entries:
        compact = {
            "id": str(entry.get("id", "")),
            "title": str(entry.get("title", ""))[:220],
            "source": clip_complete_text(str(entry.get("source_text", "")), 2600),
            "comment": str(entry.get("summary", ""))[:1600],
        }
        size = sum(len(value) for value in compact.values())
        if current and (len(current) >= 3 or current_chars + size > 7000):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(compact)
        current_chars += size
    if current:
        chunks.append(current)

    for chunk in chunks:
        expected_ids = {entry["id"] for entry in chunk if entry["id"]}
        prompt = (
            f"{instruction}\n"
            "Return only JSON in this exact shape: "
            '{"reviews":[{"id":"same id","approved":true,"reason":"short reason"}]}. '
            "Return exactly one review for every id. One defect means approved=false.\n\n"
            + json.dumps(chunk, ensure_ascii=False)
        )
        try:
            payload = request_json_completion(
                post=post,
                runtime=runtime,
                messages=[
                    {"role": "system", "content": "Review every item independently. Never repair text during review."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=min(3000, max(500, len(chunk) * 70)),
                temperature=0,
                review=True,
                timeout=50,
            )
            reviews = payload.get("reviews")
            if not isinstance(reviews, list):
                raise ValueError("batch review response has no reviews list")
            seen: set[str] = set()
            for review in reviews:
                if not isinstance(review, dict):
                    continue
                item_id = str(review.get("id", ""))
                if item_id not in expected_ids or item_id in seen:
                    continue
                seen.add(item_id)
                approved = review.get("approved") is True
                results[item_id] = (approved, str(review.get("reason") or ("approved" if approved else "rejected")))
        except Exception as exc:
            reason = f"batch_review_error:{type(exc).__name__}:{exc}"
            for item_id in expected_ids:
                results[item_id] = (False, reason)
    return results


def valid_display_title(value: str, lang: str) -> bool:
    text = normalize_text(value)
    if len(text) < 12 or len(text) > 190:
        return False
    if MOJIBAKE.search(text) or CONTROL.search(text) or HTML_OR_URL.search(text) or BAD_FRAGMENT.search(text):
        return False
    if lang == "pl" and BROKEN_PL.search(text):
        return False
    if lang == "en" and BROKEN_EN.search(text):
        return False
    return bool(re.search(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]", text))


def split_sentences(value: str) -> list[str]:
    text = normalize_text(value).replace("…", ".")
    marker = "\ue000"
    text = re.sub(
        r"\b(?:e\.g|i\.e|m\.in)\.",
        lambda match: match.group(0).replace(".", marker),
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\b(?:[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]\.){2,4}",
        lambda match: match.group(0).replace(".", marker),
        text,
    )
    text = re.sub(
        r"\b[A-ZĄĆĘŁŃÓŚŹŻ]\.",
        lambda match: match.group(0).replace(".", marker),
        text,
    )
    text = re.sub(
        r"\b(?:dr|prof|np|itd|itp|proc|tys|mln|mld|nr|art|ust|pkt|godz|św|"
        r"Mr|Mrs|Ms|Dr|Prof|vs|etc)\.",
        lambda match: match.group(0).replace(".", marker),
        text,
        flags=re.I,
    )
    text = re.sub(r"(?<=\d)\.(?=\d)", marker, text)
    text = re.sub(r"(?<=\d)\.(?=\s+[a-ząćęłńóśźż])", marker, text)
    parts = [normalize_text(part.replace(marker, ".")) for part in re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", text)]
    return [part for part in parts if part]


def _invalid_short_polish_tokens(text: str) -> list[str]:
    tokens = re.findall(r"(?<![\w@])[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{1,2}(?![\w.-])", text)
    invalid: list[str] = []
    for raw_token in tokens:
        if raw_token.isupper():
            continue
        token = raw_token.lower()
        if len(token) == 1 and token not in PL_ALLOWED_ONE:
            invalid.append(token)
        elif len(token) == 2 and token in PL_INVALID_TWO:
            invalid.append(token)
    return invalid


def _sentence_reason(sentence: str, lang: str) -> str:
    if len(sentence) < 24:
        return "sentence_too_short"
    if len(sentence) > 460:
        return "sentence_too_long"
    if sentence[-1] not in ".!?":
        return "missing_terminal_punctuation"
    if BAD_FRAGMENT.search(sentence) or HTML_OR_URL.search(sentence):
        return "publisher_or_ui_fragment"
    if re.search(r"(?<![A-ZĄĆĘŁŃÓŚŹŻ])[.!?][A-ZĄĆĘŁŃÓŚŹŻ]", sentence):
        return "missing_space_after_punctuation"
    if lang == "pl":
        if BYLINE_PL.search(sentence):
            return "byline"
        if BAD_START_PL.search(sentence):
            return "orphan_sentence_start"
        if BROKEN_PL.search(sentence):
            return "broken_polish_word"
        if len(_invalid_short_polish_tokens(sentence)) >= 2:
            return "orphan_polish_letters"
        if not re.match(r"^[A-ZĄĆĘŁŃÓŚŹŻ0-9„\"'’]", sentence):
            return "invalid_sentence_start"
    else:
        if BYLINE_EN.search(sentence):
            return "byline"
        if BAD_START_EN.search(sentence):
            return "orphan_sentence_start"
        if BROKEN_EN.search(sentence):
            return "broken_english_contraction"
        invalid_one = re.findall(r"(?<![\w@'’])[b-hj-z](?![\w.'’-])", sentence)
        if len(invalid_one) >= 2:
            return "orphan_english_letters"
        if not re.match(r"^[A-Z0-9\"'’]", sentence):
            return "invalid_sentence_start"
    return ""


def _meaning_tokens(sentence: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[^\W\d_]{3,}", sentence, flags=re.UNICODE)
        if token.lower() not in DUPLICATE_STOPWORDS
    }


def _has_near_duplicate_sentence(sentences: list[str]) -> bool:
    token_sets = [_meaning_tokens(sentence) for sentence in sentences]
    for index, left in enumerate(token_sets):
        for right in token_sets[index + 1:]:
            smaller = min(len(left), len(right))
            if smaller >= 6 and len(left & right) / smaller >= 0.55:
                return True
    return False


def validate_text(
    value: str,
    lang: str,
    *,
    min_sentences: int,
    max_sentences: int,
    min_chars: int,
    max_chars: int,
) -> QualityResult:
    if lang not in {"pl", "en"}:
        raise ValueError(f"Unsupported language: {lang}")

    text = normalize_text(value)
    reasons: list[str] = []
    if not text:
        reasons.append("empty")
    if CONTROL.search(text):
        reasons.append("control_character")
    if MOJIBAKE.search(text):
        reasons.append("mojibake")
    if TRUNCATED_LOWERCASE_FRAGMENT.search(text):
        reasons.append("truncated_lowercase_fragment")
    if MALFORMED_QUOTE_SPACING.search(text):
        reasons.append("malformed_quote_spacing")
    if BAD_PUNCTUATION_SPACING.search(text):
        reasons.append("bad_punctuation_spacing")
    if text.count('"') % 2:
        reasons.append("unbalanced_double_quote")
    if text.count("\u201e") != text.count("\u201d") and ("\u201e" in text or "\u201d" in text):
        reasons.append("unbalanced_polish_quote")
    if text.count("\u201c") != text.count("\u201d") and "\u201c" in text:
        reasons.append("unbalanced_english_quote")
    if lang == "pl" and BAD_GRAMMAR_PL.search(text):
        reasons.append("invalid_case_after_przez")
    if lang == "pl" and BAD_TENSE_PL.search(text):
        reasons.append("inconsistent_polish_tense")
    if lang == "pl" and KNOWN_STATUS_CONFLICT_PL.search(text):
        reasons.append("known_officeholder_status_conflict")
    if lang == "en" and KNOWN_STATUS_CONFLICT_EN.search(text):
        reasons.append("known_officeholder_status_conflict")
    if len(text) < min_chars:
        reasons.append("comment_too_short")
    if len(text) > max_chars:
        reasons.append("comment_too_long")

    sentences = split_sentences(text)
    if not min_sentences <= len(sentences) <= max_sentences:
        reasons.append("sentence_count")

    seen: set[str] = set()
    for sentence in sentences:
        reason = _sentence_reason(sentence, lang)
        if reason:
            reasons.append(reason)
        key = re.sub(r"\W+", "", sentence.lower())
        if key in seen:
            reasons.append("duplicate_sentence")
        seen.add(key)
    if _has_near_duplicate_sentence(sentences):
        reasons.append("near_duplicate_sentence")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return QualityResult(not unique_reasons, text, tuple(sentences), unique_reasons)


def validate_comment(value: str, lang: str) -> QualityResult:
    return validate_text(
        value,
        lang,
        min_sentences=MIN_SENTENCES,
        max_sentences=MAX_SENTENCES,
        min_chars=MIN_COMMENT_CHARS,
        max_chars=MAX_COMMENT_CHARS,
    )


def validate_news_comment(value: str, lang: str) -> QualityResult:
    return validate_text(
        value,
        lang,
        min_sentences=1,
        max_sentences=2,
        min_chars=55,
        max_chars=420,
    )
