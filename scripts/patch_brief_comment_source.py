#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Make article pages use the same approved full_brief as homepage cards.

The server-side quality gate is the only editorial validator. The browser must
not run a second, different validator that can reject a comment already marked
as passed_3_to_6_sentences.
"""
from pathlib import Path

PATCHES = {
    Path("pl/brief.html"): (
        "function cleanShortSummary(text){const s=cleanSentence(text||'');return s && !brokenPolish(s) ? s : '';}\n"
        "function buildSummary(item){return cleanArticleComment(item.full_brief)||cleanArticleComment(item.details)||cleanShortSummary(item.summary)||'Komentarz nie przeszedł kontroli jakości. Otwórz artykuł źródłowy, aby przeczytać pełny tekst u wydawcy.';}",
        "function approvedComment(item){const text=normalizeText(item.full_brief||item.details||'');const count=(text.replace(/…/g,'.').match(/[^.!?]+[.!?]+/g)||[]).length;return String(item.comment_quality_status||'').startsWith('passed_')&&count>=3&&count<=6?text:'';}\n"
        "function buildSummary(item){return approvedComment(item)||'Ten wpis nie ma zatwierdzonego komentarza i nie powinien być widoczny jako karta BriefRooms.';}"
    ),
    Path("en/brief.html"): (
        "function buildSummary(item){return cleanArticleComment(item.full_brief)||cleanArticleComment(item.details)||'';}",
        "function approvedComment(item){const text=removeBoilerplate(item.full_brief||item.details||'');const count=(text.replace(/…/g,'.').match(/[^.!?]+[.!?]+/g)||[]).length;return String(item.comment_quality_status||'').startsWith('passed_')&&count>=3&&count<=6?text:'';}\n"
        "function buildSummary(item){return approvedComment(item);}"
    ),
}

for path, (old, new) in PATCHES.items():
    text = path.read_text(encoding="utf-8")
    if new in text:
        print(f"{path}: already patched")
        continue
    if old not in text:
        raise SystemExit(f"Expected block not found in {path}")
    path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
    print(f"{path}: patched")
