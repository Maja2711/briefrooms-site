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

FILES = [(Path("pl/home_brief.json"), "pl"), (Path("en/home_brief.json"), "en")]
TIMEOUT = 12
USER_AGENT = "BriefRoomsBot/2.1 (+https://briefrooms.com)"
AI_MODEL = os.getenv("NEWS_AI_MODEL") or os.getenv("BRIEFROOMS_AI_MODEL") or "gpt-4o-mini"
CACHE_PATH = Path(".cache/article_full_briefs.json")
MIN_ARTICLE_CHARS = 700
MAX_ARTICLE_CHARS = 6000

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
            t = clean(p, 1200)
            if len(t) >= 55 and not NOISE.search(t):
                paras.append(t)
            if len(paras) >= 18:
                break
        if len(paras) >= 18:
            break
    return clean(" ".join(paras), MAX_ARTICLE_CHARS)


def fetch_article_text(url: str) -> tuple[str, str]:
    if not str(url or "").startswith("http"):
        return "", "invalid_url"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "pl,en;q=0.8"}, timeout=TIMEOUT, allow_redirects=True)
        if not r.ok:
            return "", f"http_{r.status_code}"
        text = extract_article_text(r.text)
        if len(text) < MIN_ARTICLE_CHARS:
            return text, "article_text_too_short"
        return text, "article_read"
    except Exception as ex:
        return "", f"fetch_error:{type(ex).__name__}"


def ai_summarize(title: str, lang: str, article_text: str, link: str, cache: dict) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not article_text:
        return ""
    key = hashlib.sha256(f"article-brief-v3-coherence|{lang}|{link}|{title}|{article_text[:1200]}".encode("utf-8")).hexdigest()[:48]
    cached = cache.get(key)
    if isinstance(cached, dict) and cached.get("summary"):
        return cached["summary"]
    if lang == "pl":
        prompt = (
            "Przeczytaj tekst artykułu i zrób streszczenie do BriefRooms. "
            "Zwróć wyłącznie JSON {\"full_brief\":\"...\"}. "
            "Zasady: 3-6 zdań; tylko sens i fakty z tekstu; prosto, logicznie i gramatycznie. "
            "Każde zdanie musi być samodzielne: czytelnik ma rozumieć kto/co zrobił bez znajomości poprzedniego zdania. "
            "Nie zaczynaj od: Dodał, Dodała, Zaznaczył, Powiedział, Skomentuj, symbolu, waluty, urwanego fragmentu ani środka zdania. "
            "Nie kopiuj podpisów, nazwisk autorów, FOTONEWS, PAP, poleceń redakcyjnych ani fragmentów UI. "
            "Zero ogólników o kategorii, źródle lub tym, gdzie czytać więcej; nie dopisuj faktów spoza artykułu.\n\n"
            f"Tytuł: {title}\nTekst artykułu:\n{article_text[:MAX_ARTICLE_CHARS]}"
        )
    else:
        prompt = (
            "Read the article text and write a BriefRooms summary. "
            "Return only JSON {\"full_brief\":\"...\"}. "
            "Rules: 3-6 sentences; only the meaning and facts from the text; simple, logical and grammatical. "
            "Every sentence must stand alone: the reader must understand who/what did something without relying on the previous sentence. "
            "Do not start with orphan reporting verbs, symbols, currencies, editorial commands or clipped fragments. "
            "Do not copy bylines, photo credits, wire labels, editorial commands or UI fragments. "
            "No generic category/source/read-more filler; do not add unsupported facts.\n\n"
            f"Title: {title}\nArticle text:\n{article_text[:MAX_ARTICLE_CHARS]}"
        )
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": AI_MODEL, "messages": [{"role": "system", "content": "You are a strict news editor. Summarise only what is in the provided article text and return valid JSON."}, {"role": "user", "content": prompt}], "temperature": 0.1, "max_tokens": 520},
            timeout=35,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S)
        data = json.loads(raw)
        summary = clean(str(data.get("full_brief", "")), 1600)
        sents = unique(split_sentences(summary))[:6]
        if len(sents) >= 3:
            result = " ".join(sents)
            cache[key] = {"summary": result, "model": AI_MODEL}
            return result
    except Exception as ex:
        print(f"[WARN] AI article summary failed: {title[:80]} :: {ex}", file=sys.stderr)
    return ""


def fallback_summary(article_text: str) -> str:
    return " ".join(unique(split_sentences(article_text))[:6])


def process(path: Path, lang: str, cache: dict) -> bool:
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for section in ("latest", "radar"):
        for item in data.get(section, []) or []:
            link = item.get("link") or ""
            title = item.get("title") or ""
            article_text, status = fetch_article_text(link)
            item["article_read_status"] = status
            if len(article_text) >= MIN_ARTICLE_CHARS:
                summary = ai_summarize(title, lang, article_text, link, cache) or fallback_summary(article_text)
                if summary:
                    item["full_brief"] = summary
                    item["article_text_chars"] = len(article_text)
                    item["summary_basis"] = "article_text"
                    changed = True
            else:
                item["summary_basis"] = "rss_only_insufficient_article_text"
                item["article_text_chars"] = len(article_text)
                changed = True
    data["brief_methodology"] = {
        "pl": "Zasada: najpierw przeczytać dostępny tekst artykułu, potem streścić jego sens. Komentarz ma być prosty, logiczny, gramatyczny i samodzielny zdaniowo. Nie może zaczynać się od urwanego czasownika typu „Dodał”, od symbolu, waluty, polecenia redakcyjnego ani środka zdania.",
        "en": "Rule: first read the available article text, then summarise its meaning. The comment must be simple, logical, grammatical and sentence-complete. It must not start with orphan reporting verbs, symbols, currencies, editorial commands or clipped fragments.",
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
