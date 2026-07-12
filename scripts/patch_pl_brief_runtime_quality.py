#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch the PL article page so it never falls back to a one-line RSS fragment."""
from pathlib import Path

PATH = Path("pl/brief.html")


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    before = text

    text = text.replace(
        "function splitSentences(text){if(brokenPolish(text))return [];return normalizeText(text).replace(/…/g,'.').match(/[^.!?]+[.!?]+|[^.!?]+$/g)?.map(s=>cleanSentence(s)).filter(Boolean)||[];}",
        "function splitSentences(text){return normalizeText(text).replace(/…/g,'.').match(/[^.!?]+[.!?]+|[^.!?]+$/g)?.map(s=>cleanSentence(s)).filter(Boolean)||[];}"
    )

    if "function authorCredit" not in text:
        text = text.replace(
            "function usefulSentence(s){",
            "function authorCredit(s){return /^(?:(?:[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż-]+|[A-ZĄĆĘŁŃÓŚŹŻ]\\.)(?:\\s+|$)){2,5}\\s*\\/+/.test(String(s||''));}\nfunction usefulSentence(s){"
        )

    text = text.replace(
        "return s.length>=45 && hasLogicalStart(s) && !brokenPolish(s) &&",
        "return s.length>=45 && hasLogicalStart(s) && !brokenPolish(s) && !authorCredit(s) &&"
    )

    text = text.replace(
        "function cleanArticleComment(text){if(brokenPolish(text))return '';const s=uniqueSentences(splitSentences(text||'').filter(usefulSentence));return s.length>=3?s.slice(0,6).join(' '):'';}",
        "function cleanArticleComment(text){const s=uniqueSentences(splitSentences(text||'').filter(usefulSentence));return s.length>=3?s.slice(0,6).join(' '):'';}"
    )

    text = text.replace(
        "function cleanShortSummary(text){const s=cleanSentence(text||'');return s && !brokenPolish(s) ? s : '';}\n",
        ""
    )

    text = text.replace(
        "function buildSummary(item){return cleanArticleComment(item.full_brief)||cleanArticleComment(item.details)||cleanShortSummary(item.summary)||'Komentarz nie przeszedł kontroli jakości. Otwórz artykuł źródłowy, aby przeczytać pełny tekst u wydawcy.';}",
        "function buildSummary(item){return cleanArticleComment(item.full_brief)||cleanArticleComment(item.details)||'Ten news nie ma jeszcze pełnego komentarza spełniającego zasady BriefRooms. Nie pokazujemy skrótu z samego tytułu, podpisu autora ani fragmentu RSS.';}"
    )

    text = text.replace("/assets/site.css?v=brief11", "/assets/site.css?v=brief12")

    if text != before:
        PATH.write_text(text, encoding="utf-8", newline="\n")
        print("PL brief runtime quality patch applied")
    else:
        print("PL brief runtime quality patch already applied")


if __name__ == "__main__":
    main()
