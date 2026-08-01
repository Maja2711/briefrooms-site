#!/usr/bin/env python3
"""Build and publish PL/EN section-news pages independently from homepage AI."""

from __future__ import annotations

import argparse
import hashlib
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


def _attr(tag: str, name: str) -> str:
    match = re.search(
        rf"\b{re.escape(name)}\s*=\s*([\"'])(.*?)\1",
        tag,
        flags=re.I | re.S,
    )
    return html.unescape(match.group(2)).strip() if match else ""


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _tag_with_class(block: str, tag_name: str, class_name: str) -> tuple[str, str] | None:
    pattern = re.compile(
        rf"(<{tag_name}\b[^>]*>)(.*?)</{tag_name}>",
        flags=re.I | re.S,
    )
    for match in pattern.finditer(block):
        classes = _attr(match.group(1), "class").split()
        if class_name in classes:
            return match.group(1), match.group(2)
    return None


def canonical_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def page_timestamp(source: str, lang: str) -> datetime | None:
    marker = re.search(
        r'<meta\s+name=["\']briefrooms-news-updated-at["\']\s+content=["\']([^"\']+)',
        source,
        flags=re.I,
    )
    if marker:
        try:
            parsed = datetime.fromisoformat(marker.group(1).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    timed = re.search(r'<time\b[^>]*datetime=["\']([^"\']+)', source, flags=re.I)
    if timed:
        try:
            parsed = datetime.fromisoformat(timed.group(1).replace("Z", "+00:00"))
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
    found: dict[str, str] = {}
    for section in re.finditer(r"<section\b([^>]*)>(.*?)</section>", source, flags=re.I | re.S):
        section_id = _attr(section.group(1), "id")
        if section_id:
            found[section_id] = section.group(2)

    missing = [section_id for section_id in expected if section_id not in found]
    if missing:
        raise SectionPublicationError(f"{lang} missing sections: {', '.join(missing)}")

    counts: dict[str, int] = {}
    seen_urls: set[str] = set()
    for section_id, (minimum, maximum) in expected.items():
        cards = re.findall(r"<li\b[^>]*>(.*?)</li>", found[section_id], flags=re.I | re.S)
        counts[section_id] = len(cards)
        if not minimum <= len(cards) <= maximum:
            raise SectionPublicationError(
                f"{lang} section {section_id} has {len(cards)} cards; expected {minimum}-{maximum}"
            )
        for index, card in enumerate(cards, 1):
            anchor_tag = None
            for tag in re.findall(r"<a\b[^>]*>", card, flags=re.I | re.S):
                if "news-main-link" in _attr(tag, "class").split():
                    anchor_tag = tag
                    break
            href = canonical_url(_attr(anchor_tag or "", "href"))
            title = _tag_with_class(card, "span", "news-text")
            source_line = _tag_with_class(card, "span", "source-line")
            image_url = ""
            for image_tag in re.findall(r"<img\b[^>]*>", card, flags=re.I | re.S):
                image_url = canonical_url(_attr(image_tag, "src") or _attr(image_tag, "data-src"))
                if image_url:
                    break
            if not href:
                raise SectionPublicationError(f"{lang} {section_id} card {index} has no source URL")
            if not title or not _text(title[1]):
                raise SectionPublicationError(f"{lang} {section_id} card {index} has no title")
            if not source_line or not _text(source_line[1]):
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
        tail = "\n".join((result.stdout + "\n" + result.stderr).splitlines()[-80:])
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
        selected.append(config["path"])
        selected.append(config["history"])
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
                request = urllib.request.Request(url, headers={"User-Agent": "BriefRooms-section-verifier/1.0"})
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
        status = build_and_promote(args.root.resolve(), args.stage_dir.resolve(), args.publication_id)
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
