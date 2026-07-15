#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Read source articles and create meaning-only 3-6 sentence BriefRooms summaries.

Rule: first try to read the article body from the source URL, then summarise only
what is present in that source material. Never pad with generic sentences about
category, source, or where to read more. The final comment must read like normal
language: no broken opening fragments, no leading currency symbols, no clipped
sentence starts, no orphan reporting verbs such as "Dodał, że".
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
from pathlib import Path

import requests

from comment_quality import (
    QUALITY_VERSION,
    clip_complete_text,
    decode_http_response,
    get_ai_runtime,
    independent_ai_review,
    independent_ai_review_batch,
    request_json_completion,
    validate_comment,
)

FILES = [(Path("pl/home_brief.json"), "pl"), (Path("en/home_brief.json"), "en")]
TIMEOUT = 12
USER_AGENT = "BriefRoomsBot/2.1 (+https://briefrooms.com)"
CACHE_PATH = Path(".cache/article_full_briefs.json")
MIN_ARTICLE_CHARS = 700
MAX_ARTICLE_CHARS = 6000
CACHE_VERSION = f"article-brief-v8-complete-source-review-strict-{QUALITY_VERSION}"

NOISE = re.compile(
    r"cookie|cookies|reklama|advertisement|subskryb|newsletter|zaloguj|privacy|rodo|"
    r"wyrażam zgodę|czytaj także|zobacz także|materiał partnera|all rights reserved|"
    r"sign up|subscribe|log in|register|terms of use|privacy policy|skip to content|"
    r"more menu|search bbc|image source|image caption",
    re.I,
)
BOILERPLATE = re.compile(
    r"briefrooms|pełnego tekstu źródłowego|publikacja źródłowa|otwórz pełny artykuł|"
    r"źródłem wpisu jest|najważniejszy sygnał.*kategorii|artykuł dotyczy tematu|"
    r"pełne tło i szczegóły|source publication|full source text|open the full article|"
    r"skom(entuj|entował|entowała|entowali|entowała)|powiedz|napisz|wyślij|fotonews|pap\b|"
    r"grzegorz krzyżewski|autor:|oprac\.|redakcja|czytaj także|zobacz także|"
    r"the source is|the main signal belongs to|this article is about|full context and supporting details",
    re.I,
)
BAD_START = re.compile(
    r"^(?:[.,;:!?%‰/\\)\]}]|zł\b|tys\.\b|mln\b|mld\b|proc\.\b|usd\b|eur\b|pln\b|"
    r"and\b|or\b|but\b|because\b|which\b|that\b|za\b|dla\b|oraz\b|a\b|i\b|"
    r"dodał\b|dodała\b|dodali\b|zaznaczył\b|zaznaczyła\b|podkreślił\b|podkreśliła\b|"
    r"powiedział\b|powiedziała\b|stwierdził\b|stwierdziła\b|ocenił\b|oceniła\b|wskazał\b|wskazała\b|"
    r"skomentuj\b|skomentował\b|skomentowała\b|powiedz\b|napisz\b)",
    re.I,
)
BAD_FRAGMENT = re.compile(
    r"\bm\.\s+[A-ZĄĆĘŁŃÓŚŹŻ]|[A-ZĄĆĘŁŃÓŚŹŻ]{3,}\s+[A-ZĄĆĘŁŃÓŚŹŻ]{3,}\s*/\s*FOTONEWS|"
    r"\bPAP\b|\bFOTONEWS\b|\bautor\b|\boprac\.\b|\bczytaj także\b|\bzobacz także\b",
    re.I,
)
GOOD_START = re.compile(r"^[A-ZĄĆĘŁŃÓŚŹŻ0-9\"„'’]")


def load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def clean(text: str, limit: int | None = None) -> str:
    text = html.unescape(str(text or ""))
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<noscript[\s\S]*?</noscript>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—·•/\t\n\r")
    if limit and len(text) > limit:
        return text[:limit].rsplit(" ", 1)[0].strip()
    return text


def logical_sentence(sentence: str) -> bool:
    sentence = clean(sentence)
    if len(sentence) < 45:
        return False
    if BAD_START.search(sentence) or BAD_FRAGMENT.search(sentence):
        return False
    if not GOOD_START.search(sentence):
        return False
    if NOISE.search(sentence) or BOILERPLATE.search(sentence):
        return False
    return True


def split_sentences(text: str) -> list[str]:
    text = clean(text).replace("…", ".")
    out = []
    for part in re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", text):
        sentence = clean(part)
        if sentence and sentence[-1] not in ".!?":
            sentence += "."
        if logical_sentence(sentence):
            out.append(sentence)
    return out


def unique(sentences: list[str]) -> list[str]:
    seen = set()
    out = []
    for sentence in sentences:
        key = re.sub(r"\W+", "", sentence.lower())[:110]
        if key and key not in seen:
            seen.add(key)
            out.append(sentence)
    return out


def extract_article_text(raw_html: str) -> str:
    raw_html = str(raw_html or "")[:500000]
    raw_html = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<noscript[\s\S]*?</noscript>", " ", raw_html, flags=re.I)
    blocks = []
    for pat in (r"<article[^>]*>([\s\S]*?)</article>", r"<main[^>]*>([\s\S]*?)</main>"):
        blocks.extend(re.findall(pat, raw_html, flags=re.I))
    if not blocks:
        blocks = [raw_html]
    paras = []
    for block in blocks[:3]:
        for p in re.findall(r"<p[^>]*>([\s\S]*?)</p>", block, flags=re.I)[:80]:
            t = clip_complete_text(clean(p), 1200)
            if len(t) >= 55 and not NOISE.search(t):
                paras.append(t)
            if len(paras) >= 18:
                break
        if len(paras) >= 18:
            break
    return clip_complete_text(clean(" ".join(paras)), MAX_ARTICLE_CHARS)


def fetch_article_text(url: str) -> tuple[str, str]:
    if not str(url or "").startswith("http"):
        return "", "invalid_url"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "pl,en;q=0.8"}, timeout=TIMEOUT, allow_redirects=True)
        if not r.ok:
            return "", f"http_{r.status_code}"
        text = extract_article_text(decode_http_response(r))
        if len(text) < MIN_ARTICLE_CHARS:
            return text, "article_text_too_short"
        return text, "article_read"
    except Exception as ex:
        return "", f"fetch_error:{type(ex).__name__}"


def ai_summarize(title: str, lang: str, article_text: str, link: str, cache: dict) -> str:
    runtime = get_ai_runtime()
    if not runtime.available or not article_text:
        return ""
    article_text = clip_complete_text(article_text, MAX_ARTICLE_CHARS)
    if not article_text:
        return ""
    key = hashlib.sha256(f"{CACHE_VERSION}|{lang}|{link}|{title}|{article_text[:1600]}".encode("utf-8")).hexdigest()[:48]
    cached = cache.get(key)
    if (
        isinstance(cached, dict)
        and cached.get("reviewed") is True
        and cached.get("quality_version") == QUALITY_VERSION
        and cached.get("summary")
    ):
        result = validate_comment(cached["summary"], lang)
        if result.valid:
            return result.text
    if lang == "pl":
        prompt = (
            "Przeczytaj tekst artykułu i zrób streszczenie do BriefRooms. "
            "Zwróć wyłącznie JSON {\"full_brief\":\"...\"}. "
            "Zasady: 3-4 pełne zdania; tylko sens i fakty z tekstu; prosto, logicznie i gramatycznie. "
            "Każde zdanie musi być samodzielne: czytelnik ma rozumieć kto/co zrobił bez znajomości poprzedniego zdania. "
            "Nie zaczynaj od: Dodał, Dodała, Zaznaczył, Powiedział, Skomentuj, symbolu, waluty, urwanego fragmentu ani środka zdania. "
            "Nie kopiuj podpisów, nazwisk autorów, FOTONEWS, PAP, poleceń redakcyjnych ani fragmentów UI. "
            "Tekst źródłowy może zawierać uszkodzone polskie znaki lub odstępy. Nie kopiuj takich uszkodzeń. "
            "Jeżeli sensu nie da się jednoznacznie odtworzyć, zwróć pusty full_brief. "
            "Zero ogólników o kategorii, źródle lub tym, gdzie czytać więcej; nie dopisuj faktów spoza artykułu.\n\n"
            f"Tytuł: {title}\nTekst artykułu:\n{article_text[:MAX_ARTICLE_CHARS]}"
        )
    else:
        prompt = (
            "Read the article text and write a BriefRooms summary. "
            "Return only JSON {\"full_brief\":\"...\"}. "
            "Rules: 3-4 complete sentences; only the meaning and facts from the text; simple, logical and grammatical. "
            "Every sentence must stand alone: the reader must understand who/what did something without relying on the previous sentence. "
            "Do not start with orphan reporting verbs, symbols, currencies, editorial commands or clipped fragments. "
            "Do not copy bylines, photo credits, wire labels, editorial commands or UI fragments. "
            "The source text may contain damaged characters or spacing. Never copy those defects. "
            "If the meaning cannot be reconstructed unambiguously, return an empty full_brief. "
            "No generic category/source/read-more filler; do not add unsupported facts.\n\n"
            f"Title: {title}\nArticle text:\n{article_text[:MAX_ARTICLE_CHARS]}"
        )
    prompt += (
        "\nStrict final requirement: write 3-4 sentences only. Every sentence must add a distinct concrete fact. "
        "Do not infer a person's current or former office unless the supplied source states it explicitly."
    )
    try:
        data = request_json_completion(
            post=requests.post,
            runtime=runtime,
            messages=[
                {"role": "system", "content": "You are a strict news editor. Summarise only the provided article text and return valid JSON."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=520,
            temperature=0,
            timeout=35,
        )
        summary = clip_complete_text(clean(str(data.get("full_brief", ""))), 1600)
        quality = validate_comment(summary, lang)
        if quality.valid and ai_review(title, lang, article_text, quality.text):
            cache[key] = {
                "summary": quality.text,
                "model": runtime.generation_model,
                "review_model": runtime.review_model,
                "provider": runtime.provider,
                "reviewed": True,
                "quality_version": QUALITY_VERSION,
            }
            return quality.text
        if not quality.valid:
            print(f"[WARN] deterministic comment rejection: {title[:80]} :: {','.join(quality.reasons)}", file=sys.stderr)
    except Exception as ex:
        print(f"[WARN] AI article summary failed: {title[:80]} :: {ex}", file=sys.stderr)
    return ""


def ai_review(title: str, lang: str, article_text: str, summary: str) -> bool:
    """Independent second model call; failure or uncertainty means no publication."""
    runtime = get_ai_runtime()
    approved, reason = independent_ai_review(
        post=requests.post,
        runtime=runtime,
        title=title,
        source_text=article_text,
        summary=summary,
        lang=lang,
    )
    if not approved:
        print(f"[WARN] AI review rejected: {title[:80]} :: {reason[:160]}", file=sys.stderr)
    return approved


def ai_summarize_batch(candidates: list[dict], lang: str, cache: dict) -> dict[str, str]:
    """Generate and independently review homepage comments in bounded batches."""
    runtime = get_ai_runtime()
    if not runtime.available:
        return {}

    accepted: dict[str, str] = {}
    pending: list[dict] = []
    for candidate in candidates:
        key = hashlib.sha256(
            f"{CACHE_VERSION}|{lang}|{candidate['link']}|{candidate['title']}|{candidate['article_text'][:1600]}".encode("utf-8")
        ).hexdigest()[:48]
        candidate["cache_key"] = key
        cached = cache.get(key)
        if isinstance(cached, dict) and cached.get("reviewed") is True and cached.get("quality_version") == QUALITY_VERSION:
            quality = validate_comment(str(cached.get("summary") or ""), lang)
            if quality.valid:
                accepted[candidate["id"]] = quality.text
                continue
        pending.append(candidate)

    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0
    for candidate in pending:
        size = len(candidate["title"]) + min(3200, len(candidate["article_text"]))
        if current and (len(current) >= 4 or current_chars + size > 12000):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(candidate)
        current_chars += size
    if current:
        chunks.append(current)

    generated: dict[str, str] = {}
    candidate_by_id = {candidate["id"]: candidate for candidate in pending}
    for chunk in chunks:
        source_items = [
            {
                "id": candidate["id"],
                "title": candidate["title"][:220],
                "article_text": clip_complete_text(candidate["article_text"], 3200),
            }
            for candidate in chunk
        ]
        if lang == "pl":
            rules = (
                "Dla każdego elementu napisz po polsku 3-4 pełne, proste i logiczne zdania, wyłącznie na "
                "podstawie podanego tekstu. Każde zdanie musi być samodzielne i poprawne. Nie kopiuj uszkodzonych "
                "znaków, porozcinanych wyrazów, podpisów, elementów interfejsu ani poleceń wydawcy."
            )
        else:
            rules = (
                "For every item write 3-4 complete, clear and logical English sentences using only the supplied "
                "article text. Every sentence must stand alone. Do not copy damaged characters, split words, "
                "bylines, publisher UI or editorial commands."
            )
        prompt = (
            f"{rules} Write 3-4 sentences only. Every sentence must add a new fact; do not repeat or paraphrase "
            "information already stated. Do not infer a person's current or former office unless the supplied "
            "source states it explicitly. "
            "Use correct grammar, inflection, quotation marks and punctuation. "
            "If an item cannot be summarized safely, return an empty full_brief. "
            "Return every id exactly once as JSON: "
            '{"items":[{"id":"same id","full_brief":"..."}]}.\n\n'
            + json.dumps(source_items, ensure_ascii=False)
        )
        try:
            payload = request_json_completion(
                post=requests.post,
                runtime=runtime,
                messages=[
                    {"role": "system", "content": "You are a strict multilingual news editor. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=min(4000, max(900, len(chunk) * 480)),
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
                quality = validate_comment(
                    clip_complete_text(clean(str(row.get("full_brief") or "")), 1600),
                    lang,
                )
                if quality.valid:
                    generated[item_id] = quality.text
                else:
                    print(f"[WARN] batch comment rejected: {candidate_by_id[item_id]['title'][:80]} :: {','.join(quality.reasons)}", file=sys.stderr)
        except Exception as exc:
            print(f"[WARN] AI article batch failed ({lang}, {len(chunk)} items): {exc}", file=sys.stderr)

    review_entries = [
        {
            "id": item_id,
            "title": candidate_by_id[item_id]["title"],
            "source_text": candidate_by_id[item_id]["article_text"],
            "summary": summary,
        }
        for item_id, summary in generated.items()
    ]
    reviews = independent_ai_review_batch(
        post=requests.post,
        runtime=runtime,
        entries=review_entries,
        lang=lang,
    )
    for item_id, summary in generated.items():
        approved, reason = reviews.get(item_id, (False, "missing_review"))
        if not approved:
            print(f"[WARN] batch AI review rejected: {candidate_by_id[item_id]['title'][:80]} :: {reason[:160]}", file=sys.stderr)
            continue
        candidate = candidate_by_id[item_id]
        accepted[item_id] = summary
        cache[candidate["cache_key"]] = {
            "summary": summary,
            "model": runtime.generation_model,
            "review_model": runtime.review_model,
            "provider": runtime.provider,
            "reviewed": True,
            "quality_version": QUALITY_VERSION,
        }
    return accepted


def process(path: Path, lang: str, cache: dict) -> bool:
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    candidates: list[dict] = []
    item_records: list[tuple[dict, str, str]] = []
    item_index = 0
    for section in ("latest", "radar"):
        for item in data.get(section, []) or []:
            link = item.get("link") or ""
            title = item.get("title") or ""
            article_text, status = fetch_article_text(link)
            item["article_read_status"] = status
            for key in ("comment_quality_status", "comment_quality_version", "comment_generation_status"):
                item.pop(key, None)
            if len(article_text) >= MIN_ARTICLE_CHARS:
                item_id = f"{lang}-{item_index}"
                item_index += 1
                candidates.append({
                    "id": item_id,
                    "title": title,
                    "link": link,
                    "article_text": article_text,
                })
                item_records.append((item, item_id, article_text))
            else:
                item.pop("full_brief", None)
                item["summary_basis"] = "rss_only_insufficient_article_text"
                item["comment_generation_status"] = "rejected_or_unavailable"
                item["article_text_chars"] = len(article_text)
            changed = True

    summaries = ai_summarize_batch(candidates, lang, cache)
    for item, item_id, article_text in item_records:
        summary = summaries.get(item_id, "")
        item["article_text_chars"] = len(article_text)
        if summary:
            item["full_brief"] = summary
            item["summary_basis"] = "article_text_ai_reviewed"
            item["comment_generation_status"] = "ai_review_approved"
        else:
            item.pop("full_brief", None)
            item["summary_basis"] = "article_text_ai_rejected"
            item["comment_generation_status"] = "rejected_or_unavailable"
    data["brief_methodology"] = {
        "pl": "Komentarz powstaje wyłącznie z przeczytanego tekstu artykułu. Musi przejść niezależny przegląd AI oraz pełną kontrolę językową; surowy tekst artykułu lub RSS nie może być komentarzem awaryjnym.",
        "en": "A comment is created only from the retrieved article text. It must pass an independent AI review and the complete language gate; raw article or RSS text is never a fallback comment.",
    }
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def main() -> None:
    cache = load_cache()
    changed = False
    for path, lang in FILES:
        changed = process(path, lang, cache) or changed
    save_cache(cache)
    print("Article reading summaries applied" if changed else "No article summaries changed")


if __name__ == "__main__":
    main()
