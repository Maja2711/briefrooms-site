#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Keep article pages on the same approved comment source as homepage cards.

The server-side quality gate is the only editorial validator. The browser must
not count sentences or run a second language-quality test after a comment has
already received comment_quality_status=passed_*. This patch is idempotent.
"""
from pathlib import Path

CONFIG = {
    Path("pl/brief.html"): {
        "normalizer": "normalizeText",
        "fallback": "Ten wpis nie ma zatwierdzonego komentarza i nie powinien być widoczny jako karta BriefRooms.",
        "old_build": "function buildSummary(item){return cleanArticleComment(item.full_brief)||cleanArticleComment(item.details)||cleanShortSummary(item.summary)||'Komentarz nie przeszedł kontroli jakości. Otwórz artykuł źródłowy, aby przeczytać pełny tekst u wydawcy.';}",
    },
    Path("en/brief.html"): {
        "normalizer": "removeBoilerplate",
        "fallback": "This item has no approved comment and should not be visible as a BriefRooms card.",
        "old_build": "function buildSummary(item){return cleanArticleComment(item.full_brief)||cleanArticleComment(item.details)||'';}",
    },
}

for path, cfg in CONFIG.items():
    text = path.read_text(encoding="utf-8")
    approved = (
        f"function approvedComment(item){{const text={cfg['normalizer']}(item.full_brief||item.details||'');"
        "return String(item.comment_quality_status||'').startsWith('passed_')&&text?text:'';}"
    )
    build = f"function buildSummary(item){{return approvedComment(item)||'{cfg['fallback']}';}}"
    desired = approved + "\n" + build

    start = text.find("function approvedComment(item){")
    if start >= 0:
        build_start = text.find("function buildSummary(item){", start)
        if build_start < 0:
            raise SystemExit(f"buildSummary not found in {path}")
        next_function = text.find("\nfunction ", build_start + len("function buildSummary(item){"))
        if next_function < 0:
            raise SystemExit(f"next function not found in {path}")
        text = text[:start] + desired + text[next_function:]
    elif cfg["old_build"] in text:
        text = text.replace(cfg["old_build"], desired)
    elif desired in text:
        print(f"{path}: already patched")
        continue
    else:
        raise SystemExit(f"Expected comment block not found in {path}")

    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"{path}: approved comment source enforced")
