#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared fail-closed quality contract for PL and EN article comments."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

QUALITY_VERSION = 4
QUALITY_STATUS = f"passed_strict_v{QUALITY_VERSION}"
MIN_SENTENCES = 3
MAX_SENTENCES = 6
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
BYLINE_PL = re.compile(
    r"^(?:(?:[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż-]+|[A-ZĄĆĘŁŃÓŚŹŻ]\.)\s+){1,5}"
    r"[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż-]+\s*/+",
)
BYLINE_EN = re.compile(r"^(?:By\s+)?(?:[A-Z][a-z-]+\s+){1,4}[A-Z][a-z-]+\s*/+")

PL_ALLOWED_ONE = {"a", "i", "o", "u", "w", "z"}
PL_INVALID_TWO = {"cz"}


@dataclass(frozen=True)
class QualityResult:
    valid: bool
    text: str
    sentences: tuple[str, ...]
    reasons: tuple[str, ...]


def normalize_text(value: str) -> str:
    text = str(value or "")
    for bad, good in REPLACEMENTS.items():
        text = text.replace(bad, good)
    text = re.sub(r"\s+", " ", text).strip(" -–—·•/\t\n\r")
    return text


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
    api_key: str,
    model: str,
    title: str,
    source_text: str,
    summary: str,
    lang: str,
) -> tuple[bool, str]:
    """Run the mandatory second-pass review without importing an HTTP client."""
    if not api_key:
        return False, "missing_api_key"
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
    prompt = (
        f"{instruction}\nReturn only JSON {{\"approved\":true|false,\"reason\":\"short reason\"}}.\n\n"
        f"Title: {title}\n\nSource material:\n{source_text[:4500]}\n\nComment to review:\n{summary}"
    )
    try:
        response = post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are an independent, conservative publication quality reviewer."},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 120,
            },
            timeout=35,
        )
        response.raise_for_status()
        payload = json.loads(response.json()["choices"][0]["message"]["content"].strip())
        if payload.get("approved") is True:
            return True, str(payload.get("reason") or "approved")
        return False, str(payload.get("reason") or "rejected")
    except Exception as exc:
        return False, f"review_error:{type(exc).__name__}:{exc}"


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
