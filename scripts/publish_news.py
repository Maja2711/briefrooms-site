#!/usr/bin/env python3
"""Build and promote one atomic PL+EN news publication."""

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
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "1.0"
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}
LANGUAGE_PATHS = {
    "pl": {
        "news": Path("pl/aktualnosci.html"),
        "home": Path("pl/home_brief.json"),
        "index": Path("pl/index.html"),
        "brief_template": Path("pl/brief.html"),
        "brief_dir": Path("pl/briefy"),
        "archive": Path("data/permanent_briefs_pl.json"),
        "history": Path("data/news_story_history_pl.json"),
        "brief_url_dir": "/pl/briefy",
    },
    "en": {
        "news": Path("en/news.html"),
        "home": Path("en/home_brief.json"),
        "index": Path("en/index.html"),
        "brief_template": Path("en/brief.html"),
        "brief_dir": Path("en/briefs"),
        "archive": Path("data/permanent_briefs_en.json"),
        "history": Path("data/news_story_history_en.json"),
        "brief_url_dir": "/en/briefs",
    },
}
SHARED_PATHS = (
    Path("sitemap.xml"),
    Path("data/news_publication_status.json"),
    Path("data/news_source_report.json"),
)
BUILD_INPUT_PATHS = (
    Path("scripts"),
    Path("data/external_media_policy.json"),
    Path("data/content_update_contract.json"),
    Path("assets/site.css"),
    Path(".github/workflows/publish-news.yml"),
)
IGNORED_COPY_NAMES = {
    ".git",
    ".build",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
}
PRODUCTION_COMMANDS = (
    ("fetch_pl_news", "scripts/fetch_news_pl_deep.py"),
    ("fetch_en_news", "scripts/fetch_news_en_context.py"),
    ("install_en_hook", "scripts/add_en_news_hook.py"),
    ("backup_home", "scripts/protect_home_feed.py", "--backup"),
    ("build_pl_home", "scripts/build_home_brief_pl.py"),
    ("build_en_home", "scripts/build_home_brief_en.py"),
    ("summarize_articles", "scripts/read_and_summarize_articles.py"),
    ("enforce_methodology", "scripts/enforce_brief_length.py"),
    ("remove_badges", "scripts/remove_urgent_badge_categories.py"),
    ("quality_gate", "scripts/validate_brief_quality.py"),
    ("dedupe_home", "scripts/dedupe_home_brief_stories.py"),
    ("hide_labels", "scripts/hide_urgent_home_labels.py"),
    ("comment_source", "scripts/patch_brief_comment_source.py"),
    ("complete_pl_home", "scripts/protect_home_feed.py", "--validate", "--lang", "pl"),
    ("complete_en_home", "scripts/protect_home_feed.py", "--validate", "--lang", "en"),
    ("generate_briefs", "scripts/generate_permanent_briefs.py"),
    ("normalize_pl", "scripts/normalize_home_publish_count.py", "--lang", "pl"),
    ("normalize_en", "scripts/normalize_home_publish_count.py", "--lang", "en"),
    ("validate_pl_home", "scripts/protect_home_feed.py", "--validate", "--lang", "pl"),
    ("validate_en_home", "scripts/protect_home_feed.py", "--validate", "--lang", "en"),
    ("static_quality", "scripts/fix_static_quality.py"),
    ("external_media_policy", "scripts/enforce_external_media_policy.py"),
    ("homepage_photo_policy", "scripts/enforce_homepage_photo_only.py"),
    (
        "validate_pl_unit",
        "scripts/validate_news_home_publish.py",
        "--lang",
        "pl",
        "--max-age-minutes",
        "360",
    ),
    (
        "validate_en_unit",
        "scripts/validate_news_home_publish.py",
        "--lang",
        "en",
        "--max-age-minutes",
        "360",
    ),
)


class PublicationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublicationContext:
    run_id: str
    generated_at: datetime
    source_commit_sha: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    port = f":{parsed.port}" if parsed.port else ""
    host = parsed.hostname.lower()
    query = urlencode(
        sorted(
            (key, val)
            for key, val in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in TRACKING_QUERY_KEYS
        )
    )
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), host + port, path, query, ""))


def _safe_remove_stage(stage_dir: Path) -> None:
    resolved = stage_dir.resolve()
    if resolved in {ROOT.resolve(), ROOT.parent.resolve()}:
        raise PublicationError(f"unsafe stage directory: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def copy_repository(source: Path, destination: Path) -> None:
    _safe_remove_stage(destination)

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in IGNORED_COPY_NAMES}

    shutil.copytree(source, destination, ignore=ignore)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _home_items(root: Path, lang: str) -> list[dict[str, Any]]:
    payload = load_json(root / LANGUAGE_PATHS[lang]["home"])
    return [
        item
        for section in ("latest", "radar")
        for item in (payload.get(section) or [])
        if isinstance(item, dict)
    ]


def _news_links(root: Path, lang: str) -> set[str]:
    try:
        source = (root / LANGUAGE_PATHS[lang]["news"]).read_text(encoding="utf-8")
    except OSError:
        return set()
    return {
        canonical
        for canonical in (
            canonical_url(html.unescape(value))
            for value in re.findall(
                r'class=["\']news-main-link["\'][^>]+href=["\']([^"\']+)',
                source,
                flags=re.IGNORECASE,
            )
        )
        if canonical
    }


def story_urls(root: Path, lang: str) -> set[str]:
    links = _news_links(root, lang)
    links.update(
        canonical
        for canonical in (canonical_url(item.get("link")) for item in _home_items(root, lang))
        if canonical
    )
    return links


def _published_times(root: Path, lang: str) -> list[datetime]:
    values: list[datetime] = []
    for item in _home_items(root, lang):
        if item.get("published_at_inferred") is True:
            continue
        parsed = parse_time(item.get("published_at"))
        if parsed:
            values.append(parsed)
    history = load_json(root / LANGUAGE_PATHS[lang]["history"])
    for item in history.get("stories") or []:
        if not isinstance(item, dict) or item.get("published_at_inferred") is True:
            continue
        parsed = parse_time(item.get("published_at"))
        if parsed:
            values.append(parsed)
    return values


def _news_timestamp(root: Path, lang: str) -> str:
    try:
        source = (root / LANGUAGE_PATHS[lang]["news"]).read_text(encoding="utf-8")
    except OSError:
        return ""
    marker = re.search(
        r'<meta\s+name=["\']briefrooms-news-updated-at["\']\s+content=["\']([^"\']+)',
        source,
        flags=re.IGNORECASE,
    )
    if marker:
        return iso(parse_time(marker.group(1)) or utc_now())
    if lang == "pl":
        match = re.search(r'Ostatnia aktualizacja:\s*<time datetime="([^"]+)"', source)
        return iso(parse_time(match.group(1)) or utc_now()) if match else ""
    match = re.search(r"Last updated:\s*(\d{4}-\d{2}-\d{2})", source)
    if not match:
        return ""
    return iso(datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc))


def _home_timestamp(root: Path, lang: str) -> str:
    parsed = parse_time(load_json(root / LANGUAGE_PATHS[lang]["home"]).get("updated_at"))
    return iso(parsed) if parsed else ""


def run_command(
    stage_root: Path,
    label: str,
    arguments: Iterable[str],
    *,
    env: dict[str, str],
    log_path: Path,
) -> None:
    command = [sys.executable, *arguments]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        command,
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
    if result.returncode != 0:
        output = "\n".join(
            (str(result.stdout or "") + "\n" + str(result.stderr or "")).splitlines()[-80:]
        )
        if output:
            print(f"\n===== {label} failure tail =====\n{output}", file=sys.stderr)
        raise PublicationError(f"{label} failed with exit code {result.returncode}")


def run_production_build(stage_root: Path, diagnostics_dir: Path) -> None:
    attempt = len(list(diagnostics_dir.glob("commands-attempt-*.log"))) + 1
    events_path = diagnostics_dir / f"source-events-attempt-{attempt}.jsonl"
    commands_log = diagnostics_dir / f"commands-attempt-{attempt}.log"
    env = os.environ.copy()
    env.update(
        {
            "BR_NEWS_PERSIST_HISTORY": "1",
            "BR_NEWS_DIAGNOSTIC_EVENTS": str(events_path),
            "PYTHONUTF8": "1",
        }
    )
    for command in PRODUCTION_COMMANDS:
        run_command(
            stage_root,
            command[0],
            command[1:],
            env=env,
            log_path=commands_log,
        )
    shutil.copy2(events_path, diagnostics_dir / "source-events.jsonl")


def _fixture_entries(path: Path) -> list[dict[str, Any]]:
    root = ET.parse(path).getroot()
    entries: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        value = {child.tag: (child.text or "").strip() for child in list(item)}
        published = parsedate_to_datetime(value["pubDate"]).astimezone(timezone.utc)
        value["published_at"] = iso(published)
        entries.append(value)
    return entries


def _fixture_comment(entry: dict[str, Any], lang: str) -> str:
    description = re.sub(r"\s+", " ", entry.get("description", "")).strip()
    if len(description.split()) >= 28:
        return description
    if lang == "pl":
        return (
            f"{description} Materiał opisuje potwierdzone wydarzenie i jego bezpośredni kontekst. "
            "Źródło wskazuje najważniejszych uczestników, termin oraz możliwe kolejne działania. "
            "Dalszy rozwój sytuacji zależy od oficjalnych decyzji opisanych w publikacji."
        )
    return (
        f"{description} The report describes the confirmed event and its immediate context. "
        "The source identifies the main participants, timing and the next likely procedural steps. "
        "Further developments depend on the official decisions described in the publication."
    )


def _fixture_item(entry: dict[str, Any], lang: str) -> dict[str, Any]:
    comment = _fixture_comment(entry, lang)
    return {
        "category": entry.get("category") or ("Aktualności" if lang == "pl" else "World"),
        "title": entry["title"],
        "summary": comment,
        "details": comment,
        "full_brief": comment,
        "source": entry.get("source") or "Fixture News",
        "link": entry["link"],
        "image": entry.get("image") or "https://example.com/news.jpg",
        "time": "dzisiaj" if lang == "pl" else "today",
        "published_at": entry["published_at"],
        "published_at_inferred": False,
        "summary_basis": "article_text_ai_reviewed",
        "comment_generation_status": "ai_review_approved",
        "comment_quality_status": "passed_strict_v7",
        "comment_quality_version": 7,
    }


def _fixture_news_html(items: list[dict[str, Any]], lang: str, generated_at: datetime) -> str:
    title = "Aktualności" if lang == "pl" else "News"
    updated = iso(generated_at)
    cards = "\n".join(
        (
            '<li><a class="news-main-link" '
            f'href="{html.escape(str(item["link"]), quote=True)}">'
            f'<span class="news-text">{html.escape(str(item["title"]))}</span></a>'
            f'<div class="ai-note">{html.escape(str(item["full_brief"]))}</div></li>'
        )
        for item in items
    )
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        f'<meta name="briefrooms-news-updated-at" content="{updated}">'
        f"<title>{title}</title></head><body><main><ul>{cards}</ul></main></body></html>"
    )


def run_fixture_build(
    stage_root: Path,
    fixture_dir: Path,
    context: PublicationContext,
    diagnostics_dir: Path,
) -> None:
    try:
        from generate_permanent_briefs import generate_all
    except ImportError:
        from scripts.generate_permanent_briefs import generate_all

    events: list[dict[str, Any]] = []
    for lang in ("pl", "en"):
        entries = _fixture_entries(fixture_dir / f"{lang}.xml")
        items = [_fixture_item(entry, lang) for entry in entries]
        paths = LANGUAGE_PATHS[lang]
        (stage_root / paths["news"]).write_text(
            _fixture_news_html(items, lang, context.generated_at),
            encoding="utf-8",
            newline="\n",
        )
        write_json(
            stage_root / paths["home"],
            {
                "version": "fixture-v1",
                "language": lang,
                "updated_at": iso(context.generated_at),
                "count": min(10, len(items)),
                "latest": items[:10],
                "radar": [],
                "comment_quality_gate": {"status": "passed_strict_v7", "version": 7},
            },
        )
        for entry in entries:
            events.append(
                {
                    "kind": "feed",
                    "lang": lang,
                    "pipeline": "fixture",
                    "url": str((fixture_dir / f"{lang}.xml").resolve()),
                    "fetched": True,
                    "parsed": 1,
                    "error": "",
                }
            )
            events.append(
                {
                    "kind": "item",
                    "lang": lang,
                    "pipeline": "fixture",
                    "url": str((fixture_dir / f"{lang}.xml").resolve()),
                    "result": "accepted",
                    "published_at": entry["published_at"],
                }
            )
    events_path = diagnostics_dir / "source-events.jsonl"
    events_path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
        newline="\n",
    )
    generate_all(root=stage_root, now=context.generated_at)


def aggregate_source_report(
    events_path: Path,
    context: PublicationContext,
) -> dict[str, Any]:
    records: dict[str, dict[tuple[str, str], dict[str, Any]]] = {"pl": {}, "en": {}}
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except (TypeError, ValueError):
                continue
            lang = event.get("lang")
            if lang not in records:
                continue
            key = (str(event.get("pipeline") or "news"), str(event.get("url") or "unknown"))
            source = records[lang].setdefault(
                key,
                {
                    "pipeline": key[0],
                    "url": key[1],
                    "fetched": False,
                    "parsed": 0,
                    "rejected_stale": 0,
                    "rejected_duplicate": 0,
                    "rejected_invalid": 0,
                    "accepted": 0,
                    "error": "",
                    "newest_source_published_at": "",
                },
            )
            if event.get("kind") == "feed":
                source["fetched"] = source["fetched"] or bool(event.get("fetched"))
                source["parsed"] += max(0, int(event.get("parsed") or 0))
                if event.get("error"):
                    source["error"] = str(event["error"])[:300]
            elif event.get("kind") == "item":
                result = str(event.get("result") or "")
                if result in source:
                    source[result] += 1
                published = parse_time(event.get("published_at"))
                current = parse_time(source["newest_source_published_at"])
                if published and (current is None or published > current):
                    source["newest_source_published_at"] = iso(published)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": context.run_id,
        "generated_at": iso(context.generated_at),
        "languages": {},
    }
    for lang, indexed in records.items():
        sources = sorted(indexed.values(), key=lambda item: (item["pipeline"], item["url"]))
        totals = {
            key: sum(int(source.get(key) or 0) for source in sources)
            for key in (
                "parsed",
                "rejected_stale",
                "rejected_duplicate",
                "rejected_invalid",
                "accepted",
            )
        }
        totals["fetched"] = sum(1 for source in sources if source["fetched"])
        totals["errors"] = sum(1 for source in sources if source["error"])
        report["languages"][lang] = {"totals": totals, "sources": sources}
    return report


def _replace_path(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        temporary = target.with_name(target.name + ".publication-new")
        backup = target.with_name(target.name + ".publication-old")
        if temporary.exists():
            shutil.rmtree(temporary)
        if backup.exists():
            shutil.rmtree(backup)
        shutil.copytree(source, temporary)
        if target.exists():
            target.replace(backup)
        temporary.replace(target)
        if backup.exists():
            shutil.rmtree(backup)
        return
    with tempfile.NamedTemporaryFile(delete=False, dir=target.parent) as handle:
        temporary_file = Path(handle.name)
        handle.write(source.read_bytes())
    os.replace(temporary_file, target)


def _restore_language(stage_root: Path, source_root: Path, lang: str) -> None:
    for key in (
        "news",
        "home",
        "index",
        "brief_template",
        "brief_dir",
        "archive",
        "history",
    ):
        relative = LANGUAGE_PATHS[lang][key]
        source = source_root / relative
        target = stage_root / relative
        if source.exists():
            _replace_path(source, target)


def inject_publication_markers(path: Path, context: PublicationContext) -> None:
    source = path.read_text(encoding="utf-8")
    source = re.sub(
        r'\s*<meta\s+name=["\']briefrooms-(?:news-)?publication["\'][^>]*>',
        "",
        source,
        flags=re.IGNORECASE,
    )
    marker = (
        f'<meta name="briefrooms-news-publication" '
        f'content="{html.escape(context.run_id, quote=True)}">'
    )
    if "</head>" not in source:
        raise PublicationError(f"HTML head is missing in {path}")
    source = source.replace("</head>", f"  {marker}\n</head>", 1)
    path.write_text(source, encoding="utf-8", newline="\n")


def build_manifest(
    source_root: Path,
    stage_root: Path,
    source_report: dict[str, Any],
    context: PublicationContext,
) -> tuple[dict[str, Any], dict[str, set[str]]]:
    previous = load_json(source_root / "data/news_publication_status.json")
    previous_urls = {lang: story_urls(source_root, lang) for lang in ("pl", "en")}
    current_urls = {lang: story_urls(stage_root, lang) for lang in ("pl", "en")}
    new_urls = {lang: current_urls[lang] - previous_urls[lang] for lang in ("pl", "en")}
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "publication_id": context.run_id,
        "run_id": context.run_id,
        "generated_at": iso(context.generated_at),
        "commit_sha": context.source_commit_sha,
        "commit_sha_semantics": "source main commit used to generate this atomic publication",
        "served_from_last_good": False,
        "source_report": "data/news_source_report.json",
    }
    for lang in ("pl", "en"):
        totals = ((source_report.get("languages") or {}).get(lang) or {}).get("totals") or {}
        if int(totals.get("fetched") or 0) < 1 or int(totals.get("parsed") or 0) < 1:
            raise PublicationError(f"{lang} source acquisition failed: no successfully parsed feed")
        if not current_urls[lang]:
            raise PublicationError(f"{lang} publication contains no accepted article URLs")
        newest = max(_published_times(stage_root, lang), default=None)
        status = "fresh" if new_urls[lang] else "no-new-stories"
        previous_lang = previous.get(lang) if isinstance(previous.get(lang), dict) else {}
        manifest[lang] = {
            "source_candidates": int(totals.get("parsed") or 0),
            "accepted_articles": len(current_urls[lang]),
            "new_articles": len(new_urls[lang]),
            "newest_source_published_at": iso(newest) if newest else "",
            "homepage_updated_at": _home_timestamp(stage_root, lang),
            "news_page_updated_at": _news_timestamp(stage_root, lang),
            "last_successful_fetch_at": iso(context.generated_at),
            "last_successful_publication_at": iso(context.generated_at),
            "last_new_publication_at": (
                iso(context.generated_at)
                if status == "fresh"
                else str(
                    previous_lang.get("last_new_publication_at")
                    or previous_lang.get("homepage_updated_at")
                    or _home_timestamp(source_root, lang)
                )
            ),
            "status": status,
            "served_from_last_good": False,
        }
    return manifest, new_urls


def _rebuild_after_no_new(stage_root: Path, context: PublicationContext) -> None:
    run_command(
        stage_root,
        "regenerate_after_no_new",
        ("scripts/generate_permanent_briefs.py",),
        env={**os.environ, "PYTHONUTF8": "1"},
        log_path=stage_root / ".build" / "publication-regenerate.log",
    )


def _path_hash(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
        return digest.hexdigest()
    if path.is_dir():
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            digest.update(child.relative_to(path).as_posix().encode("utf-8"))
            digest.update(child.read_bytes())
    return digest.hexdigest()


def controlled_paths() -> list[Path]:
    paths = list(SHARED_PATHS)
    for lang in ("pl", "en"):
        paths.extend(
            LANGUAGE_PATHS[lang][key]
            for key in (
                "news",
                "home",
                "index",
                "brief_template",
                "brief_dir",
                "archive",
                "history",
            )
        )
    paths.append(Path(".cache"))
    return paths


def repository_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in controlled_paths():
        digest.update(relative.as_posix().encode("utf-8"))
        path = root / relative
        digest.update(b"1" if path.exists() else b"0")
        if path.exists():
            digest.update(_path_hash(path).encode("ascii"))
    return digest.hexdigest()


def repository_build_input_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in BUILD_INPUT_PATHS:
        digest.update(relative.as_posix().encode("utf-8"))
        path = root / relative
        digest.update(b"1" if path.exists() else b"0")
        if path.exists():
            digest.update(_path_hash(path).encode("ascii"))
    return digest.hexdigest()


def changed_paths(source_root: Path, stage_root: Path) -> list[str]:
    changed = []
    for relative in controlled_paths():
        first, second = source_root / relative, stage_root / relative
        if first.exists() != second.exists() or _path_hash(first) != _path_hash(second):
            changed.append(relative.as_posix())
    return changed


def summary_markdown(manifest: dict[str, Any], production_status: str = "pending") -> str:
    rows = [
        "| Language | Candidates | Accepted | New | Latest source time | Publish status | Production status |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for lang in ("pl", "en"):
        item = manifest.get(lang) or {}
        rows.append(
            "| {lang} | {source_candidates} | {accepted_articles} | {new_articles} | "
            "{newest_source_published_at} | {status} | {production} |".format(
                lang=lang.upper(),
                production=production_status,
                **item,
            )
        )
    return "\n".join(rows) + "\n"


def failed_manifest(context: PublicationContext, error: Exception) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "publication_id": context.run_id,
        "run_id": context.run_id,
        "generated_at": iso(context.generated_at),
        "commit_sha": context.source_commit_sha,
        "served_from_last_good": True,
        "error": str(error)[:500],
        "pl": {"status": "failed", "served_from_last_good": True},
        "en": {"status": "failed", "served_from_last_good": True},
    }


def prepare(
    source_root: Path,
    stage_dir: Path,
    context: PublicationContext,
    fixture_dir: Path | None = None,
) -> dict[str, Any]:
    site_root = stage_dir / "site"
    diagnostics_dir = stage_dir / "diagnostics"
    retry_cache = stage_dir / "retry-cache"
    previous_cache = site_root / ".cache"
    if previous_cache.is_dir():
        shutil.copytree(previous_cache, retry_cache, dirs_exist_ok=True)
    copy_repository(source_root, site_root)
    if retry_cache.is_dir():
        shutil.copytree(retry_cache, site_root / ".cache", dirs_exist_ok=True)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    try:
        if fixture_dir:
            run_fixture_build(site_root, fixture_dir, context, diagnostics_dir)
        else:
            run_production_build(site_root, diagnostics_dir)
        source_report = aggregate_source_report(
            diagnostics_dir / "source-events.jsonl",
            context,
        )
        write_json(site_root / "data/news_source_report.json", source_report)
        manifest, new_urls = build_manifest(source_root, site_root, source_report, context)
        no_new = [lang for lang in ("pl", "en") if not new_urls[lang]]
        if no_new:
            for lang in no_new:
                _restore_language(site_root, source_root, lang)
            _rebuild_after_no_new(site_root, context)
            for lang in no_new:
                manifest[lang]["homepage_updated_at"] = _home_timestamp(site_root, lang)
                manifest[lang]["news_page_updated_at"] = _news_timestamp(site_root, lang)
        for lang in ("pl", "en"):
            inject_publication_markers(site_root / LANGUAGE_PATHS[lang]["news"], context)
            inject_publication_markers(site_root / LANGUAGE_PATHS[lang]["index"], context)
        write_json(site_root / "data/news_publication_status.json", manifest)
        changed = changed_paths(source_root, site_root)
        (diagnostics_dir / "changed-files.txt").write_text(
            "\n".join(changed) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        (diagnostics_dir / "summary.md").write_text(
            summary_markdown(manifest),
            encoding="utf-8",
            newline="\n",
        )
        write_json(
            stage_dir / "publication-plan.json",
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": context.run_id,
                "source_root": str(source_root.resolve()),
                "site_root": str(site_root.resolve()),
                "source_commit_sha": context.source_commit_sha,
                "source_fingerprint": repository_fingerprint(source_root),
                "source_input_fingerprint": repository_build_input_fingerprint(
                    source_root
                ),
                "changed_paths": changed,
            },
        )
        return manifest
    except Exception as exc:
        current_cache = site_root / ".cache"
        if current_cache.is_dir():
            shutil.copytree(current_cache, retry_cache, dirs_exist_ok=True)
        write_json(diagnostics_dir / "failed-manifest.json", failed_manifest(context, exc))
        raise


def refresh_plan(source_root: Path, stage_dir: Path) -> dict[str, Any]:
    plan_path = stage_dir / "publication-plan.json"
    plan = load_json(plan_path)
    if not plan or Path(str(plan.get("source_root"))).resolve() != source_root.resolve():
        raise PublicationError("publication plan does not belong to this repository")
    if repository_fingerprint(source_root) != str(plan.get("source_fingerprint") or ""):
        raise PublicationError(
            "news publication files changed on main; publication must be regenerated"
        )
    if repository_build_input_fingerprint(source_root) != str(
        plan.get("source_input_fingerprint") or ""
    ):
        raise PublicationError(
            "news generator inputs changed on main; publication must be regenerated"
        )
    plan["source_commit_sha"] = _source_commit(source_root)
    plan["source_fingerprint"] = repository_fingerprint(source_root)
    plan["source_input_fingerprint"] = repository_build_input_fingerprint(source_root)
    write_json(plan_path, plan)
    return plan


def promote(source_root: Path, stage_dir: Path) -> list[str]:
    plan = load_json(stage_dir / "publication-plan.json")
    if not plan or Path(str(plan.get("source_root"))).resolve() != source_root.resolve():
        raise PublicationError("publication plan does not belong to this repository")
    expected_commit = str(plan.get("source_commit_sha") or "")
    current_commit = _source_commit(source_root)
    if expected_commit not in {"", "unknown"} and current_commit != expected_commit:
        raise PublicationError(
            "repository commit changed after prepare; publication must be rebuilt from current main"
        )
    if repository_fingerprint(source_root) != str(plan.get("source_fingerprint") or ""):
        raise PublicationError(
            "publication inputs changed after prepare; refusing a stale or parallel promotion"
        )
    site_root = Path(str(plan.get("site_root"))).resolve()
    if not site_root.is_dir() or stage_dir.resolve() not in site_root.parents:
        raise PublicationError("publication stage is missing or unsafe")
    changed = [str(item) for item in plan.get("changed_paths") or []]
    for relative in controlled_paths():
        source = site_root / relative
        if source.exists():
            _replace_path(source, source_root / relative)
    return changed


def _source_commit(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            text=True,
            capture_output=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--promote", action="store_true")
    mode.add_argument("--refresh-plan", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--stage-dir", type=Path, required=True)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument("--fixture-dir", type=Path)
    parser.add_argument("--generated-at")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    stage_dir = args.stage_dir.resolve()
    if args.refresh_plan:
        plan = refresh_plan(root, stage_dir)
        print(
            json.dumps(
                {
                    "refreshed": True,
                    "source_commit_sha": plan.get("source_commit_sha"),
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.promote:
        changed = promote(root, stage_dir)
        print(json.dumps({"promoted": changed}, ensure_ascii=False))
        return 0
    generated_at = parse_time(args.generated_at) if args.generated_at else utc_now()
    context = PublicationContext(
        run_id=str(args.run_id),
        generated_at=generated_at or utc_now(),
        source_commit_sha=_source_commit(root),
    )
    try:
        manifest = prepare(
            root,
            stage_dir,
            context,
            args.fixture_dir.resolve() if args.fixture_dir else None,
        )
    except Exception as exc:
        print(f"Atomic news publication failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
