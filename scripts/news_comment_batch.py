#!/usr/bin/env python3
"""Bounded batch generation for strict PL and EN news comments."""

from __future__ import annotations

import hashlib
import json
import re
import sys

from comment_quality import (
    AiPermanentError,
    AiRateLimitError,
    QUALITY_STATUS,
    QUALITY_VERSION,
    clip_complete_text,
    get_ai_runtime,
    independent_ai_review_batch,
    request_json_completion,
    valid_display_title,
    validate_news_comment,
)

FOREIGN_PL_MIN_CHARS = 130
BATCH_MAX_ITEMS = 4
BATCH_MAX_CHARS = 7000
REPAIR_MAX_ITEMS = 2


def _meets_comment_contract(text: str, lang: str, longer_polish_comment: bool) -> bool:
    quality = validate_news_comment(text, lang)
    if not quality.valid:
        return False
    if longer_polish_comment:
        return len(quality.sentences) == 2 and len(quality.text) >= FOREIGN_PL_MIN_CHARS
    return True


def _clean_source(value: str, limit: int = 750) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return clip_complete_text(text, limit)


def _cache_key(item: dict, lang: str, source_text: str) -> str:
    digest = hashlib.sha256(
        f"{lang}|{item.get('title', '')}|{item.get('link', '')}|{source_text}".encode("utf-8")
    ).hexdigest()[:28]
    return f"news-comment-batch-strict-v{QUALITY_VERSION}|{digest}"


def _cached_result(cache: dict, key: str, lang: str, needs_title_translation: bool) -> dict | None:
    cached = cache.get(key)
    if not isinstance(cached, dict):
        return None
    quality = validate_news_comment(str(cached.get("summary") or ""), lang)
    title_pl = str(cached.get("title_pl") or "").strip()
    if not (
        cached.get("reviewed") is True
        and cached.get("quality_version") == QUALITY_VERSION
        and quality.valid
        and _meets_comment_contract(quality.text, lang, needs_title_translation)
        and (not needs_title_translation or valid_display_title(title_pl, "pl"))
    ):
        return None
    result = dict(cached)
    result["summary"] = quality.text
    return result


def _build_chunks(candidates: list[dict], *, max_items: int, max_chars: int) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0
    for candidate in candidates:
        size = len(candidate["title"]) + len(candidate["source_text"])
        if current and (len(current) >= max_items or current_chars + size > max_chars):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(candidate)
        current_chars += size
    if current:
        chunks.append(current)
    return chunks


def _generation_rules(lang: str) -> str:
    if lang == "pl":
        return (
            "Dla każdego elementu napisz dokładnie 2 pełne, konkretne zdania po polsku, łącznie 90-300 znaków. "
            "Każdy wynik ma dotyczyć wyłącznie elementu o podanym id. Nie przenoś faktów, nazw, liczb ani wniosków "
            "między elementami. Używaj tylko informacji występujących w title i source. Pierwsze zdanie ma podać fakt, "
            "drugie jego bezpośrednie znaczenie wynikające ze źródła, bez domysłów. Jeżeli danych wystarcza tylko na "
            "jedno zdanie albo nie da się bezpiecznie napisać dwóch zdań, zwróć pusty summary. Jeżeli "
            "translate_title_to_polish=true, podaj także wierny i naturalny title_pl; w przeciwnym razie title_pl ma "
            "być pusty. Gdy longer_polish_comment=true, zachowaj dokładnie 2 zdania i co najmniej 130 znaków."
        )
    return (
        "For every item write exactly 2 complete, concrete English sentences, 90-300 characters total. Each result "
        "must refer only to the item with that id. Never transfer facts, names, numbers or conclusions between items. "
        "Use only information present in title and source. The first sentence states the fact; the second states only "
        "its direct significance supported by the source, without inference. If the source cannot safely support two "
        "sentences, return an empty summary. Always return an empty title_pl."
    )


def _generate_chunks(
    *,
    chunks: list[list[dict]],
    lang: str,
    post,
    runtime,
    pending_by_id: dict[str, dict],
    generated: dict[str, dict],
    translated_titles: dict[str, str],
) -> bool:
    """Generate isolated chunks. Return True when the provider rate limit stops work."""
    for chunk in chunks:
        source_items = [
            {
                "id": candidate["id"],
                "title": candidate["title"],
                "source": candidate["source_text"],
                "translate_title_to_polish": candidate["translate_title"],
                "longer_polish_comment": candidate["longer_polish_comment"],
            }
            for candidate in chunk
        ]
        prompt = (
            f"{_generation_rules(lang)} Never repeat the same information. Do not infer a person's current or former "
            "office unless the title or source states it explicitly. Use correct grammar, inflection, quotation marks "
            "and punctuation. Never copy damaged characters, split words, bylines, publisher UI or editorial commands. "
            "Treat every JSON object as an isolated record. Before returning a row, verify that every proper noun and "
            "number in summary occurs in that row's title or source. Return each id exactly once as JSON: "
            '{"items":[{"id":"same id","summary":"...","title_pl":""}]}.\n\n'
            + json.dumps(source_items, ensure_ascii=False)
        )
        try:
            payload = request_json_completion(
                post=post,
                runtime=runtime,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a strict multilingual news editor. Records are independent and facts must never "
                            "cross record boundaries. Return only valid JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=min(2400, max(700, len(chunk) * 210)),
                temperature=0,
                timeout=60,
            )
            rows = payload.get("items")
            if not isinstance(rows, list):
                raise ValueError("batch generation response has no items list")
            expected = {candidate["id"] for candidate in chunk}
            seen: set[str] = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                item_id = str(row.get("id", ""))
                if item_id not in expected or item_id in seen:
                    continue
                seen.add(item_id)
                candidate = pending_by_id[item_id]
                quality = validate_news_comment(str(row.get("summary") or ""), lang)
                title_pl = str(row.get("title_pl") or "").strip()
                if candidate["translate_title"]:
                    if not valid_display_title(title_pl, "pl"):
                        print(f"[WARN] PL translated title rejected: {candidate['title'][:80]}", file=sys.stderr)
                        continue
                    translated_titles[item_id] = title_pl
                if not _meets_comment_contract(quality.text, lang, candidate["longer_polish_comment"]):
                    print(
                        f"[WARN] {lang.upper()} batch comment rejected: {candidate['title'][:80]} :: "
                        f"{','.join(quality.reasons)}",
                        file=sys.stderr,
                    )
                    continue
                generated[item_id] = {
                    "summary": quality.text,
                    "title_pl": title_pl if candidate["translate_title"] else "",
                }
        except AiPermanentError as exc:
            print(
                f"[ERROR] {lang.upper()} news generation stopped after permanent provider error: {exc}",
                file=sys.stderr,
            )
            raise
        except AiRateLimitError as exc:
            print(
                f"[WARN] {lang.upper()} news generation paused after provider rate limit: {exc}",
                file=sys.stderr,
            )
            return True
        except Exception as exc:
            print(f"[WARN] {lang.upper()} news batch failed ({len(chunk)} items): {exc}", file=sys.stderr)
    return False


def summarize_news_items(*, items: list[dict], lang: str, cache: dict, post) -> dict[str, dict]:
    """Generate uncached comments in isolated chunks, repair misses, then independently review."""
    runtime = get_ai_runtime()
    accepted: dict[str, dict] = {}
    pending: list[dict] = []
    for index, item in enumerate(items):
        item_id = f"{lang}-{index}"
        source_text = _clean_source(item.get("summary_raw", ""))
        if len(source_text) < 55:
            continue
        needs_title_translation = bool(lang == "pl" and item.get("_source_was_english"))
        key = _cache_key(item, lang, source_text)
        cached = _cached_result(cache, key, lang, needs_title_translation)
        if cached:
            accepted[item_id] = cached
            item["_comment_batch_id"] = item_id
            continue
        item["_comment_batch_id"] = item_id
        pending.append(
            {
                "id": item_id,
                "item": item,
                "title": str(item.get("title") or "")[:190],
                "source_text": source_text,
                "translate_title": needs_title_translation,
                "longer_polish_comment": needs_title_translation,
                "cache_key": key,
            }
        )

    if not runtime.available:
        return accepted

    generated: dict[str, dict] = {}
    translated_titles: dict[str, str] = {}
    pending_by_id = {candidate["id"]: candidate for candidate in pending}

    rate_limited = _generate_chunks(
        chunks=_build_chunks(pending, max_items=BATCH_MAX_ITEMS, max_chars=BATCH_MAX_CHARS),
        lang=lang,
        post=post,
        runtime=runtime,
        pending_by_id=pending_by_id,
        generated=generated,
        translated_titles=translated_titles,
    )

    if not rate_limited:
        missing = [candidate for candidate in pending if candidate["id"] not in generated]
        if missing:
            print(
                f"[INFO] {lang.upper()} repairing {len(missing)} rejected or missing comments in isolated retries",
                file=sys.stderr,
            )
            rate_limited = _generate_chunks(
                chunks=_build_chunks(missing, max_items=REPAIR_MAX_ITEMS, max_chars=3500),
                lang=lang,
                post=post,
                runtime=runtime,
                pending_by_id=pending_by_id,
                generated=generated,
                translated_titles=translated_titles,
            )

    if rate_limited:
        for item_id, title_pl in translated_titles.items():
            accepted.setdefault(
                item_id,
                {"title_pl": title_pl, "reviewed": False, "translation_only": True},
            )
        return accepted

    reviews = independent_ai_review_batch(
        post=post,
        runtime=runtime,
        entries=[
            {
                "id": item_id,
                "title": result.get("title_pl") or pending_by_id[item_id]["title"],
                "source_text": pending_by_id[item_id]["source_text"],
                "summary": result["summary"],
            }
            for item_id, result in generated.items()
        ],
        lang=lang,
    )
    for item_id, result in generated.items():
        approved, reason = reviews.get(item_id, (False, "missing_review"))
        candidate = pending_by_id[item_id]
        if not approved:
            print(
                f"[WARN] {lang.upper()} batch review rejected: {candidate['title'][:80]} :: {reason[:160]}",
                file=sys.stderr,
            )
            continue
        out = {
            "summary": result["summary"],
            "title_pl": result.get("title_pl", ""),
            "model": runtime.generation_model,
            "review_model": runtime.review_model,
            "provider": runtime.provider,
            "reviewed": True,
            "quality_status": QUALITY_STATUS,
            "quality_version": QUALITY_VERSION,
        }
        cache[candidate["cache_key"]] = out
        accepted[item_id] = out
    for item_id, title_pl in translated_titles.items():
        accepted.setdefault(
            item_id,
            {"title_pl": title_pl, "reviewed": False, "translation_only": True},
        )
    return accepted
