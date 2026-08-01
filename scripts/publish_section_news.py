#!/usr/bin/env python3
"""Build and publish PL/EN section-news pages independently from homepage AI."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
PAGE_CONFIG = {
    "pl": {
        "path": Path("pl/aktualnosci.html"),
        "history": Path("data/news_story_history_pl.json"),
        "sections": {
            "polityka": (6, 10),
            "ekonomia": (6, 10),
            "zdrowie": (6, 10),
            "nauka": (6, 10),
            "sport": (6, 10),
        },
    },
    "en": {
        "path": Path("en/news.html"),
        "history": Path("data/news_story_history_en.json"),
        "sections": {
            "world-news": (6, 9),
            "asia-pacific": (6, 9),
            "europe": (6, 9),
            "middle-east": (6, 9),
            "business": (6, 9),
            "science": (6, 9),
            "health": (6, 9),
            "sport": (6, 9),
        },
    },
}
STATUS_PATH = Path("data/news_section_publication_status.json")
BUILD_COMMANDS = (
    ("pl", "scripts/fetch_news_pl_deep.py"),
    ("en", "scripts/fetch_news_en_context.py"),
    ("en_hook", "scripts/add_en_news_hook.py"),
)
IGNORE_NAMES = {".git", ".build", ".pytest_cache", "__pycache__", "node_modules"}


class SectionPublicationError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def canonical_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def _attrs(values: list[tuple[str, str | None]]) -> dict[str, str]:
    return {str(key).lower(): str(value or "") for key, value in values}


class NewsPageParser(HTMLParser):
    def __init__(self, expected_sections: set[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.expected_sections = expected_sections
        self.current_section = ""
        self.current_card: dict[str, Any] | None = None
        self.capture = ""
        self.sections: dict[str, list[dict[str, Any]]] = {
            section: [] for section in expected_sections
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = _attrs(attrs)
        classes = set(values.get("class", "").split())
        if tag == "section":
            section_id = values.get("id", "")
            self.current_section = section_id if section_id in self.expected_sections else ""
            return
        if tag == "li" and self.current_section:
            self.current_card = {"href": "", "title": [], "source": [], "image": ""}
            return
        if self.current_card is None:
            return
        if tag == "a" and "news-main-link" in classes:
            self.current_card["href"] = values.get("href", "")
        elif tag == "span" and "news-text" in classes:
            self.capture = "title"
        elif tag == "span" and "source-line" in classes:
            self.capture = "source"
        elif tag == "img" and not self.current_card["image"]:
            self.current_card["image"] = values.get("src") or values.get("data-src") or ""

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "span" and self.capture:
            self.capture = ""
        elif tag == "li" and self.current_card is not None:
            if self.current_section:
                self.sections[self.current_section].append(self.current_card)
            self.current_card = None
            self.capture = ""
        elif tag == "section":
            self.current_section = ""

    def handle_data(self, data: str) -> None:
        if self.current_card is not None and self.capture:
            self.current_card[self.capture].append(data)


def page_timestamp(source: str, lang: str) -> datetime | None:
    patterns = (
        r'<meta\s+name=["\']briefrooms-news-updated-at["\']\s+content=["\']([^"\']+)',
        r'<time\b[^>]*datetime=["\']([^"\']+)',
    )
    for pattern in patterns:
        match = re.search(pattern, source, flags=re.I)
        if not match:
            continue
        try:
            parsed = datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if lang == "en":
        dated = re.search(r"Last updated:\s*(\d{4}-\d{2}-\d{2})", source, flags=re.I)
        if dated:
            return datetime.fromisoformat(dated.group(1)).replace(tzinfo=timezone.utc)
    return None


def validate_page(path: Path, lang: str, *, now: datetime | None = None) -> dict[str, Any]:
    if lang not in PAGE_CONFIG:
        raise ValueError(f"unsupported language: {lang}")
    source = path.read_text(encoding="utf-8")
    expected = PAGE_CONFIG[lang]["sections"]
    parser = NewsPageParser(set(expected))
    parser.feed(source)

    counts: dict[str, int] = {}
    seen_urls: set[str] = set()
    for section_id, (minimum, maximum) in expected.items():
        cards = parser.sections.get(section_id, [])
        counts[section_id] = len(cards)
        if not minimum <= len(cards) <= maximum:
            raise SectionPublicationError(
                f"{lang} section {section_id} has {len(cards)} cards; expected {minimum}-{maximum}"
            )
        for index, card in enumerate(cards, 1):
            href = canonical_url(card.get("href", ""))
            title = re.sub(r"\s+", " ", "".join(card.get("title", []))).strip()
            source_label = re.sub(r"\s+", " ", "".join(card.get("source", []))).strip()
            image_url = canonical_url(card.get("image", ""))
            if not href:
                raise SectionPublicationError(f"{lang} {section_id} card {index} has no source URL")
            if not title:
                raise SectionPublicationError(f"{lang} {section_id} card {index} has no title")
            if not source_label:
                raise SectionPublicationError(f"{lang} {section_id} card {index} has no source label")
            if not image_url:
                raise SectionPublicationError(f"{lang} {section_id} card {index} has no source image")
            if href in seen_urls:
                raise SectionPublicationError(f"{lang} duplicate article URL: {href}")
            seen_urls.add(href)

    updated_at = page_timestamp(source, lang)
    reference = now or utc_now()
    if updated_at is None:
        raise SectionPublicationError(f"{lang} page has no parseable update timestamp")
    age_hours = (reference - updated_at.astimezone(timezone.utc)).total_seconds() / 3600
    if not -2 <= age_hours <= 30:
        raise SectionPublicationError(f"{lang} page timestamp is stale: {iso(updated_at)}")
    return {
        "language": lang,
        "updated_at": iso(updated_at),
        "sections": counts,
        "articles": len(seen_urls),
    }


def inject_marker(path: Path, publication_id: str) -> None:
    source = path.read_text(encoding="utf-8")
    source = re.sub(
        r"\s*<meta\s+name=[\"']briefrooms-section-publication[\"'][^>]*>",
        "",
        source,
        flags=re.I,
    )
    if "</head>" not in source:
        raise SectionPublicationError(f"missing HTML head in {path}")
    marker = (
        '<meta name="briefrooms-section-publication" '
        f'content="{html.escape(publication_id, quote=True)}">'
    )
    source = source.replace("</head>", f"  {marker}\n</head>", 1)
    path.write_text(source, encoding="utf-8", newline="\n")


def copy_repository(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in IGNORE_NAMES}

    shutil.copytree(source, destination, ignore=ignore)


def _run(stage_root: Path, label: str, script: str, env: dict[str, str], log_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, script],
        cwd=stage_root,
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    with log_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"\n===== {label} =====\n")
        handle.write(result.stdout or "")
        handle.write(result.stderr or "")
    if result.returncode:
        tail = "\n".join(((result.stdout or "") + "\n" + (result.stderr or "")).splitlines()[-80:])
        if tail:
            print(tail, file=sys.stderr)
        raise SectionPublicationError(f"{label} failed with exit code {result.returncode}")


def _replace(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, dir=target.parent) as handle:
        temporary = Path(handle.name)
        handle.write(source.read_bytes())
    os.replace(temporary, target)


def build_and_promote(root: Path, stage_dir: Path, publication_id: str) -> dict[str, Any]:
    site_root = stage_dir / "site"
    diagnostics = stage_dir / "diagnostics"
    diagnostics.mkdir(parents=True, exist_ok=True)
    copy_repository(root, site_root)
    events = diagnostics / "source-events.jsonl"
    log_path = diagnostics / "commands.log"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONUTF8": "1",
            "BR_NEWS_PERSIST_HISTORY": "1",
            "BR_NEWS_DIAGNOSTIC_EVENTS": str(events),
        }
    )
    for label, script in BUILD_COMMANDS:
        _run(site_root, label, script, env, log_path)

    generated_at = utc_now()
    reports: dict[str, Any] = {}
    for lang, config in PAGE_CONFIG.items():
        page = site_root / config["path"]
        reports[lang] = validate_page(page, lang, now=generated_at)
        inject_marker(page, publication_id)

    status = {
        "schema_version": "1.0",
        "publication_id": publication_id,
        "generated_at": iso(generated_at),
        "mode": "section_news_fast_lane",
        "homepage_changed": False,
        "languages": reports,
    }
    status_file = site_root / STATUS_PATH
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    selected = [STATUS_PATH]
    for config in PAGE_CONFIG.values():
        selected.extend((config["path"], config["history"]))
    for relative in selected:
        source = site_root / relative
        if source.is_file():
            _replace(source, root / relative)
    return status


def verify_production(base_url: str, publication_id: str, attempts: int, interval: int, timeout: int) -> None:
    marker = f'content="{publication_id}"'.encode("utf-8")
    remaining: list[str] = []
    for attempt in range(1, attempts + 1):
        remaining = []
        for config in PAGE_CONFIG.values():
            relative = config["path"].as_posix()
            url = f"{base_url.rstrip('/')}/{relative}?publication={publication_id}&attempt={attempt}"
            try:
                request = urllib.request.Request(
                    url,
                    headers={"User-Agent": "BriefRooms-section-verifier/1.0"},
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    body = response.read()
                if marker not in body:
                    remaining.append(relative)
            except Exception:
                remaining.append(relative)
        if not remaining:
            return
        if attempt < attempts:
            time.sleep(interval)
    raise SectionPublicationError(
        f"production did not expose publication {publication_id}: {', '.join(remaining)}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--publish", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--stage-dir", type=Path)
    parser.add_argument("--publication-id", required=True)
    parser.add_argument("--base-url", default="https://briefrooms.com")
    parser.add_argument("--attempts", type=int, default=40)
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--timeout", type=int, default=20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.publish:
        if args.stage_dir is None:
            raise SystemExit("--stage-dir is required with --publish")
        status = build_and_promote(
            args.root.resolve(),
            args.stage_dir.resolve(),
            args.publication_id,
        )
        print(json.dumps(status, ensure_ascii=False, sort_keys=True))
        return 0
    verify_production(
        args.base_url,
        args.publication_id,
        args.attempts,
        args.interval,
        args.timeout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
