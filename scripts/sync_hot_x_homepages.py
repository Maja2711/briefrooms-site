#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
FEED_PATH = ROOT / "data" / "hot_tweets.json"
HOME_PATHS = {
    "pl": ROOT / "pl" / "index.html",
    "en": ROOT / "en" / "index.html",
}
INITIAL_VISIBLE = 4
DIRECT_X_POST = re.compile(r"^/[^/\s]+/status/\d+/?$", re.I)
MARKER_BLOCK = re.compile(
    r'<div\s+class=["\']source-feed["\'][^>]*>\s*'
    r'<!-- HOT_X_STATIC_START -->[\s\S]*?<!-- HOT_X_STATIC_END -->\s*</div>',
    re.I,
)
EMPTY_BLOCK = re.compile(r'<div\s+class=["\']source-feed["\']\s*>\s*</div>', re.I)


def load_feed() -> dict:
    data = json.loads(FEED_PATH.read_text(encoding="utf-8"))
    items = data.get("items")
    if not isinstance(items, list) or len(items) < INITIAL_VISIBLE:
        raise RuntimeError("Hot X feed must contain at least four items")
    return data


def clean_direct_x_url(value: object) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"}:
        return ""
    if (parsed.hostname or "").lower() not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        return ""
    path = re.sub(r"/{2,}", "/", parsed.path or "").rstrip("/")
    if not DIRECT_X_POST.match(path):
        return ""
    return "https://x.com" + path


def text(item: dict, field: str, lang: str) -> str:
    return str(item.get(f"{field}_{lang}") or "").strip()


def card_html(item: dict, lang: str, index: int) -> str:
    url = clean_direct_x_url(item.get("tweet_url"))
    title = text(item, "title", lang)
    label = text(item, "label", lang) or "X"
    comment = text(item, "comment", lang) or text(item, "summary", lang)
    image = str(item.get("image") or "").strip()
    if not url or not title or len(comment) < 40:
        raise RuntimeError(f"Invalid Hot X item at position {index + 1}")
    extra = " hot-x-extra" if index >= INITIAL_VISIBLE else ""
    comment_label = "Komentarz" if lang == "pl" else "Comment"
    post_label = "Konkretny post" if lang == "pl" else "Specific post"
    cta = "Otwórz post na X →" if lang == "pl" else "Open post on X →"
    image_html = ""
    if image:
        image_html = (
            '<div class="tweet-img">'
            f'<img src="{html.escape(image, quote=True)}" alt="" loading="lazy" referrerpolicy="no-referrer">'
            "</div>"
        )
    return (
        f'<article class="source-card hot-tweet hot-x-card{extra}">'
        f'<a class="hot-x-card-link" href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer external">'
        f'{image_html}<div class="hot-x-badges">'
        f'<span class="tweet-kicker">{html.escape(label)}</span>'
        f'<span class="hot-x-link-type">{post_label}</span>'
        f'</div><h3>{html.escape(title)}</h3></a>'
        f'<p class="hot-x-mode">{comment_label}</p>'
        f'<p class="hot-x-text">{html.escape(comment)}</p>'
        f'<a class="hot-x-source" href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer external">{cta}</a>'
        "</article>"
    )


def static_block(data: dict, lang: str) -> str:
    updated_at = str(data.get("updated_at") or "").strip()
    items = data["items"][:10]
    cards = "\n".join(card_html(item, lang, index) for index, item in enumerate(items))
    more = "Więcej z X" if lang == "pl" else "More from X"
    button = ""
    if len(items) > INITIAL_VISIBLE:
        button = (
            '\n<div class="hot-x-more-wrap">'
            f'<button type="button" class="hot-x-more" aria-expanded="false">{more}</button>'
            "</div>"
        )
    return (
        f'<div class="source-feed" data-hot-x-static-updated-at="{html.escape(updated_at, quote=True)}" '
        f'data-hot-x-count="{len(items)}">\n'
        '<!-- HOT_X_STATIC_START -->\n'
        f'{cards}{button}\n'
        '<!-- HOT_X_STATIC_END -->\n'
        '</div>'
    )


def patch_homepage(path: Path, lang: str, data: dict) -> bool:
    source = path.read_text(encoding="utf-8")
    replacement = static_block(data, lang)
    if MARKER_BLOCK.search(source):
        updated, count = MARKER_BLOCK.subn(replacement, source, count=1)
    else:
        updated, count = EMPTY_BLOCK.subn(replacement, source, count=1)
    if count != 1:
        raise RuntimeError(f"Could not locate one source-feed container in {path}")
    if updated == source:
        return False
    path.write_text(updated, encoding="utf-8", newline="\n")
    return True


def check_files(data: dict) -> None:
    updated_at = str(data.get("updated_at") or "")
    expected_urls = [clean_direct_x_url(item.get("tweet_url")) for item in data["items"][:10]]
    for lang, path in HOME_PATHS.items():
        source = path.read_text(encoding="utf-8")
        if f'data-hot-x-static-updated-at="{updated_at}"' not in source:
            raise RuntimeError(f"Missing current Hot X marker in {path}")
        if "<!-- HOT_X_STATIC_START -->" not in source or "<!-- HOT_X_STATIC_END -->" not in source:
            raise RuntimeError(f"Missing Hot X static markers in {path}")
        for url in expected_urls:
            if not url or url not in source:
                raise RuntimeError(f"Missing Hot X URL {url!r} in {path}")
        expected_title = text(data["items"][0], "title", lang)
        if expected_title not in source:
            raise RuntimeError(f"Missing first {lang} Hot X title in {path}")


def verify_production(data: dict, base_url: str, status_path: Path) -> None:
    updated_at = str(data.get("updated_at") or "")
    first_url = clean_direct_x_url(data["items"][0].get("tweet_url"))
    status = {
        "expected_updated_at": updated_at,
        "expected_first_url": first_url,
        "status": "failed",
        "attempts": 0,
        "pages": {},
    }
    last_error = ""
    for attempt in range(1, 41):
        status["attempts"] = attempt
        pages = {}
        try:
            for lang, page in (("pl", "pl/"), ("en", "en/")):
                url = f"{base_url.rstrip('/')}/{page}?hotx={int(time.time())}-{attempt}"
                request = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "BriefRooms-Hot-X-Homepage-Verifier/1.0",
                        "Cache-Control": "no-cache",
                    },
                )
                with urllib.request.urlopen(request, timeout=20) as response:
                    body = response.read().decode("utf-8", errors="replace")
                ok = (
                    f'data-hot-x-static-updated-at="{updated_at}"' in body
                    and first_url in body
                    and "<!-- HOT_X_STATIC_START -->" in body
                )
                pages[lang] = {"url": url, "passed": ok}
                if not ok:
                    raise RuntimeError(f"production page {lang} does not contain current static Hot X cards")
            status.update({"status": "passed", "pages": pages})
            last_error = ""
            break
        except Exception as exc:
            status["pages"] = pages
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(15)
    status["last_error"] = last_error
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if status["status"] != "passed":
        raise RuntimeError(last_error or "Hot X homepage production verification failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--verify-production", action="store_true")
    parser.add_argument("--base-url", default="https://briefrooms.com")
    parser.add_argument(
        "--status-path",
        type=Path,
        default=ROOT / "data" / "hot_x_homepage_production_status.json",
    )
    args = parser.parse_args()
    data = load_feed()
    if args.verify_production:
        verify_production(data, args.base_url, args.status_path)
        return
    if args.check:
        check_files(data)
        return
    changed = []
    for lang, path in HOME_PATHS.items():
        if patch_homepage(path, lang, data):
            changed.append(lang)
    check_files(data)
    print("Hot X static homepage cards synchronized: " + (", ".join(changed) if changed else "already current"))


if __name__ == "__main__":
    main()
