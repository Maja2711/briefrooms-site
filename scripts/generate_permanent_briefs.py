#!/usr/bin/env python3
"""Generate permanent, search-friendly pages for approved homepage briefs."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.11 always provides zoneinfo
    ZoneInfo = None


ROOT = Path(__file__).resolve().parents[1]
SITE_URL = "https://briefrooms.com"
DEFAULT_IMAGE = f"{SITE_URL}/assets/logo.svg"
PUBLISH_STATUS = "passed_strict_v7"
PUBLISH_VERSION = 7
PUBLISH_BASIS = "article_text_ai_reviewed"
PUBLISH_GENERATION_STATUS = "ai_review_approved"
BRIEF_ID_LENGTH = 12
MAX_SLUG_LENGTH = 80
TRACKING_QUERY_KEYS = {
    "at_campaign",
    "at_medium",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
}
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
HOME_BRIEFS_START = "<!-- HOME_BRIEFS_START -->"
HOME_BRIEFS_END = "<!-- HOME_BRIEFS_END -->"
HOME_CARD_LIMIT = 12
REMOVED_PUBLIC_URLS = {f"{SITE_URL}/en/geo/topic.html"}

LANGUAGES = {
    "pl": {
        "home": Path("pl/home_brief.json"),
        "index": Path("pl/index.html"),
        "archive": Path("data/permanent_briefs_pl.json"),
        "directory": Path("pl/briefy"),
        "url_directory": "/pl/briefy",
        "author": "Redakcja BriefRooms",
        "back": "← Strona główna",
        "core": "Sedno sprawy",
        "published": "Opublikowano",
        "source": "Źródło",
        "open_source": "Otwórz artykuł źródłowy ↗",
        "back_to_site": "Wróć do BriefRooms",
        "default_category": "Brief",
        "default_source": "Źródło",
        "read_brief": "Czytaj brief →",
        "updated_label": "Aktualizacja: ",
    },
    "en": {
        "home": Path("en/home_brief.json"),
        "index": Path("en/index.html"),
        "archive": Path("data/permanent_briefs_en.json"),
        "directory": Path("en/briefs"),
        "url_directory": "/en/briefs",
        "author": "BriefRooms Editorial Team",
        "back": "← Home",
        "core": "Core point",
        "published": "Published",
        "source": "Source",
        "open_source": "Open source article ↗",
        "back_to_site": "Back to BriefRooms",
        "default_category": "Brief",
        "default_source": "Source",
        "read_brief": "Read brief →",
        "updated_label": "Update: ",
    },
}

BRIEF_STYLE = """
    :root{--bg:#06131f;--line:rgba(255,255,255,.10);--txt:#eef7ff;--muted:#9fb2c8;--teal:#38d6c9;--teal2:#15978f;--shadow:0 24px 80px rgba(0,0,0,.38)}
    *{box-sizing:border-box}html{background:#06131f}body{margin:0;color:var(--txt);font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;background:radial-gradient(1000px 520px at 12% -10%,rgba(46,214,201,.18),transparent 58%),linear-gradient(180deg,#06131f 0%,#071827 70%,#081522 100%);min-height:100vh}.page{max-width:1020px;margin:0 auto;padding:28px 24px 42px}.top{display:flex;align-items:center;justify-content:space-between;gap:16px;padding-bottom:22px;border-bottom:1px solid var(--line)}.logo{font-size:27px;line-height:1;font-weight:850;letter-spacing:-.03em;text-decoration:none;color:#fff}.back{border:1px solid var(--line);background:rgba(255,255,255,.035);color:#eaf6ff;border-radius:999px;min-height:40px;padding:0 16px;display:inline-flex;align-items:center;text-decoration:none;font-weight:800;font-size:13px}.card{margin-top:44px;border:1px solid var(--line);background:linear-gradient(180deg,rgba(255,255,255,.07),rgba(255,255,255,.025));border-radius:28px;overflow:hidden;box-shadow:var(--shadow)}.image{height:360px;background:radial-gradient(circle at 25% 15%,rgba(56,214,201,.32),transparent 40%),linear-gradient(135deg,#0d344a,#081827);position:relative;overflow:hidden}.image img{width:100%;height:100%;object-fit:cover;display:block}.image:after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,transparent 30%,rgba(6,19,31,.65) 100%)}.fallback{height:100%;display:grid;place-items:center;font-size:52px;font-weight:900;color:rgba(255,255,255,.65)}.body{padding:32px}.tag{display:inline-flex;border:1px solid rgba(56,214,201,.25);background:rgba(56,214,201,.10);color:#8ffff6;border-radius:999px;padding:7px 12px;font-size:12px;font-weight:900;margin-bottom:18px}h1{font-size:clamp(34px,5vw,52px);line-height:1.06;letter-spacing:-.045em;margin:0 0 24px}.brief-blocks{display:grid;gap:16px;margin:24px 0 28px}.brief-block{border:1px solid rgba(255,255,255,.10);background:rgba(255,255,255,.035);border-radius:18px;padding:18px 18px 17px}.brief-block h2{font-size:15px;letter-spacing:.04em;text-transform:uppercase;color:#8ffff6;margin:0 0 10px}.brief-block p{margin:0;color:#c9d8e7;font-size:16px;line-height:1.62}.meta{display:flex;gap:12px;flex-wrap:wrap;color:#91a6ba;font-size:14px;margin-bottom:28px}.cta{display:flex;gap:14px;flex-wrap:wrap}.btn{display:inline-flex;align-items:center;gap:10px;min-height:48px;padding:0 22px;border-radius:999px;text-decoration:none;font-weight:850;border:1px solid var(--line);background:rgba(255,255,255,.035);color:#eaf6ff}.btn.primary{background:linear-gradient(135deg,var(--teal),var(--teal2));color:#042227;border-color:rgba(56,214,201,.45);box-shadow:0 18px 50px rgba(21,151,143,.28)}.empty{padding:46px;color:#c9d8e7}@media(max-width:680px){.page{padding:20px 14px 34px}.image{height:230px}.body{padding:22px}.brief-block p{font-size:15px}}
""".strip()


def canonical_source_url(raw_url: object) -> str:
    """Return a deterministic HTTP(S) URL without tracking parameters."""
    value = str(raw_url or "").strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username or parsed.password:
            return ""
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port
        default_port = (parsed.scheme.lower() == "http" and port == 80) or (
            parsed.scheme.lower() == "https" and port == 443
        )
        netloc = host if port is None or default_port else f"{host}:{port}"
        path = parsed.path or "/"
        query = []
        for key, item_value in parse_qsl(parsed.query, keep_blank_values=True):
            low_key = key.lower()
            if low_key.startswith("utm_") or low_key in TRACKING_QUERY_KEYS:
                continue
            query.append((key, item_value))
        return urlunsplit(
            (parsed.scheme.lower(), netloc, path, urlencode(sorted(query)), "")
        )
    except (TypeError, ValueError, UnicodeError):
        return ""


def brief_id_for_url(source_url: object) -> str:
    canonical = canonical_source_url(source_url)
    if not canonical:
        raise ValueError("source_url must be a valid HTTP(S) URL")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:BRIEF_ID_LENGTH]


def slugify(title: object, max_length: int = MAX_SLUG_LENGTH) -> str:
    value = str(title or "").lower().translate(
        str.maketrans({"ł": "l", "đ": "d", "ð": "d", "þ": "th", "æ": "ae", "œ": "oe"})
    )
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    value = value[:max_length].rstrip("-")
    return value or "brief"


def normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def visible_category(value: object, lang: str) -> str:
    category = normalize_text(value) or LANGUAGES[lang]["default_category"]
    if re.fullmatch(r"pilne|breaking|urgent|alert", category, flags=re.IGNORECASE):
        return "Aktualności" if lang == "pl" else "World / news"
    return category


def meta_description(full_brief: object, max_length: int = 158) -> str:
    value = html.unescape(re.sub(r"<[^>]*>", " ", str(full_brief or "")))
    value = normalize_text(value)
    if len(value) <= max_length:
        return value
    words = value.split()
    kept: list[str] = []
    for word in words:
        candidate = " ".join([*kept, word])
        if len(candidate) > max_length - 1:
            break
        kept.append(word)
    return (" ".join(kept).rstrip(".,;:!?") + "…") if kept else ""


def parse_datetime(value: object, fallback: datetime | None = None) -> datetime:
    raw = str(value or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return (fallback or datetime.now(timezone.utc)).astimezone(timezone.utc)


def iso_datetime(value: object, fallback: datetime | None = None) -> str:
    return parse_datetime(value, fallback).isoformat(timespec="seconds")


def visible_date(value: str, lang: str) -> str:
    published = parse_datetime(value)
    converted = False
    if ZoneInfo is not None:
        try:
            published = published.astimezone(ZoneInfo("Europe/Warsaw"))
            converted = True
        except Exception:
            pass
    if not converted:
        year = published.year
        march_last = datetime(year, 3, monthrange(year, 3)[1], 1, tzinfo=timezone.utc)
        october_last = datetime(year, 10, monthrange(year, 10)[1], 1, tzinfo=timezone.utc)
        march_sunday = march_last - timedelta(days=(march_last.weekday() + 1) % 7)
        october_sunday = october_last - timedelta(days=(october_last.weekday() + 1) % 7)
        offset = 2 if march_sunday <= published < october_sunday else 1
        published += timedelta(hours=offset)
    if lang == "pl":
        return published.strftime("%d.%m.%Y, %H:%M")
    return published.strftime("%d %b %Y, %H:%M")


def is_approved(item: object) -> bool:
    return bool(
        isinstance(item, dict)
        and item.get("comment_quality_status") == PUBLISH_STATUS
        and item.get("comment_quality_version") == PUBLISH_VERSION
        and item.get("summary_basis") == PUBLISH_BASIS
        and item.get("comment_generation_status") == PUBLISH_GENERATION_STATUS
        and normalize_text(item.get("full_brief"))
    )


def safe_external_url(raw_url: object) -> str:
    value = str(raw_url or "").strip()
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        ):
            return value
    except ValueError:
        pass
    return ""


def absolute_image_url(raw_url: object) -> str:
    return safe_external_url(raw_url) or DEFAULT_IMAGE


def valid_home_permalink(raw_path: object, lang: str) -> str:
    path = str(raw_path or "").strip()
    directory = re.escape(LANGUAGES[lang]["url_directory"])
    if re.fullmatch(rf"{directory}/[a-z0-9-]+-[0-9a-f]{{12}}\.html", path):
        return path
    return ""


def approved_home_items(home: dict, lang: str) -> list[dict]:
    selected: list[dict] = []
    seen: set[str] = set()
    for section in ("latest", "radar"):
        items = home.get(section, []) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not is_approved(item) or not valid_home_permalink(item.get("permalink"), lang):
                continue
            identity = str(item.get("link") or item.get("title") or "")
            if not identity or identity in seen:
                continue
            seen.add(identity)
            selected.append(item)
            if len(selected) >= HOME_CARD_LIMIT:
                return selected
    return selected


def _fallback_label(category: object) -> str:
    label = "".join(list(normalize_text(category))[:2]).upper()
    return label or "BR"


def render_home_card(item: dict, lang: str) -> str:
    cfg = LANGUAGES[lang]
    permalink = valid_home_permalink(item.get("permalink"), lang)
    if not permalink:
        raise ValueError("unsafe homepage brief permalink")
    title = normalize_text(item.get("title"))
    description = normalize_text(item.get("full_brief"))
    if not title or not description:
        raise ValueError("homepage card is missing approved text")
    category = visible_category(item.get("category"), lang)
    source = normalize_text(item.get("source")) or cfg["default_source"]
    image_url = safe_external_url(item.get("image"))
    fallback = (
        f'<div class="fallback-art" aria-hidden="true">'
        f'{html.escape(_fallback_label(category))}</div>'
    )
    if image_url:
        image = (
            f'<img src="{html.escape(image_url, quote=True)}" alt="" loading="lazy" '
            'referrerpolicy="no-referrer">'
        )
        thumb = f'<div class="thumb has-image">{fallback}{image}</div>'
    else:
        thumb = f'<div class="thumb">{fallback}</div>'
    return (
        f'<a class="brief-card" href="{html.escape(permalink, quote=True)}">'
        f'{thumb}<div class="brief-body">'
        f'<h3 class="brief-title">{html.escape(title)}</h3>'
        f'<p class="brief-desc">{html.escape(description)}</p>'
        f'<span class="brief-source"><b>{html.escape(source)}</b>'
        f'<span class="brief-link">{html.escape(cfg["read_brief"])}</span></span>'
        '</div></a>'
    )


def _home_date(updated_at: object, lang: str) -> str:
    generated = parse_datetime(updated_at)
    if lang == "pl":
        return generated.strftime("%d.%m.%Y")
    return generated.strftime("%d/%m/%Y")


def render_homepage_static(index_path: Path, home: dict, lang: str) -> bytes | None:
    items = approved_home_items(home, lang)
    if not items:
        print(
            f"WARNING: {lang} homepage has no newly approved briefs; preserving existing static cards.",
            file=sys.stderr,
        )
        return None
    source = index_path.read_text(encoding="utf-8")
    cards = "\n".join(render_home_card(item, lang) for item in items)
    generated_block = f"{HOME_BRIEFS_START}\n{cards}\n{HOME_BRIEFS_END}"
    if HOME_BRIEFS_START in source and HOME_BRIEFS_END in source:
        source = re.sub(
            rf"{re.escape(HOME_BRIEFS_START)}.*?{re.escape(HOME_BRIEFS_END)}",
            lambda _match: generated_block,
            source,
            count=1,
            flags=re.DOTALL,
        )
    else:
        source, replacements = re.subn(
            r'(<div\s+id=["\']latest-briefs["\'][^>]*>).*?(</div>\s*</section>\s*<div\s+class=["\']more-wrap["\'])',
            lambda match: f"{match.group(1)}\n{generated_block}\n{match.group(2)}",
            source,
            count=1,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if replacements != 1:
            raise ValueError(f"homepage card container not found in {index_path}")
    updated_at = iso_datetime(home.get("updated_at"))
    source, replacements = re.subn(
        r'(<div\s+id=["\']latest-briefs["\']\s+class=["\']brief-grid["\'])'
        r'(?:\s+data-home-updated-at=["\'][^"\']*["\'])?\s*>',
        rf'\1 data-home-updated-at="{html.escape(updated_at, quote=True)}">',
        source,
        count=1,
        flags=re.IGNORECASE,
    )
    if replacements != 1:
        raise ValueError(f"homepage update timestamp target not found in {index_path}")
    label = LANGUAGES[lang]["updated_label"] + _home_date(home.get("updated_at"), lang)
    source, replacements = re.subn(
        r'(<span\s+class=["\']pill["\']\s+id=["\']updated-at["\']>).*?(</span>)',
        rf"\1{html.escape(label)}\2",
        source,
        count=1,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if replacements != 1:
        raise ValueError(f"homepage visible update date not found in {index_path}")
    return source.encode("utf-8")


def item_content_hash(data: dict) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def safe_permalink(lang: str, slug: str, brief_id: str) -> str:
    if lang not in LANGUAGES:
        raise ValueError("unsupported language")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("unsafe slug")
    if not re.fullmatch(r"[0-9a-f]{12}", brief_id):
        raise ValueError("unsafe brief id")
    return f"{LANGUAGES[lang]['url_directory']}/{slug}-{brief_id}.html"


def _valid_existing_permalink(lang: str, record: dict) -> bool:
    try:
        source_id = brief_id_for_url(record.get("source_url"))
        brief_id = str(record.get("brief_id", ""))
        slug = str(record.get("slug", ""))
        return brief_id == source_id and str(record.get("permalink", "")) == safe_permalink(
            lang, slug, brief_id
        )
    except ValueError:
        return False


def _load_archive(path: Path, lang: str) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload if isinstance(payload, list) else payload.get("items", [])
    if not isinstance(records, list):
        raise ValueError(f"Invalid archive index: {path}")
    safe_records = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if not canonical_source_url(record.get("source_url")):
            continue
        if not _valid_existing_permalink(lang, record):
            continue
        safe_records.append(record)
    return safe_records


def _json_ld(record: dict, lang: str) -> str:
    canonical = f"{SITE_URL}{record['permalink']}"
    payload = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
        "headline": record["title"],
        "description": record["description"],
        "image": [record["image"]],
        "datePublished": record["date_published"],
        "dateModified": record["date_modified"],
        "author": {
            "@type": "Organization",
            "name": LANGUAGES[lang]["author"],
            "url": SITE_URL,
        },
        "publisher": {
            "@type": "Organization",
            "name": "BriefRooms",
            "url": SITE_URL,
            "logo": {
                "@type": "ImageObject",
                "url": f"{SITE_URL}/assets/favicon.svg",
            },
        },
    }
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def render_brief_html(record: dict, full_brief: str, lang: str) -> str:
    cfg = LANGUAGES[lang]
    canonical = f"{SITE_URL}{record['permalink']}"
    page_title = f"{record['title']} | BriefRooms"
    category = record["category"] or cfg["default_category"]
    fallback_text = (category or "BR")[:2].upper()
    esc = lambda value: html.escape(str(value), quote=True)
    published_label = f"{cfg['published']}: {visible_date(record['date_published'], lang)}"
    image = record["image"]
    return f"""<!doctype html>
<html lang="{lang}">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{esc(page_title)}</title>
  <meta name="description" content="{esc(record['description'])}" />
  <link rel="canonical" href="{esc(canonical)}" />
  <meta property="og:type" content="article" />
  <meta property="og:title" content="{esc(page_title)}" />
  <meta property="og:description" content="{esc(record['description'])}" />
  <meta property="og:image" content="{esc(image)}" />
  <meta property="og:url" content="{esc(canonical)}" />
  <meta property="og:site_name" content="BriefRooms" />
  <meta property="article:published_time" content="{esc(record['date_published'])}" />
  <meta property="article:modified_time" content="{esc(record['date_modified'])}" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="{esc(page_title)}" />
  <meta name="twitter:description" content="{esc(record['description'])}" />
  <meta name="twitter:image" content="{esc(image)}" />
  <link rel="icon" href="/assets/favicon.svg" />
  <link rel="stylesheet" href="/assets/site.css?v=brief13" />
  <script type="application/ld+json">{_json_ld(record, lang)}</script>
  <style>
{BRIEF_STYLE}
  </style>
</head>
<body>
  <div class="page">
    <header class="top"><a class="logo" href="/{lang}/">BRIEFROOMS</a><a class="back" href="/{lang}/">{esc(cfg['back'])}</a></header>
    <main class="card">
      <div class="image"><img src="{esc(image)}" alt="" referrerpolicy="no-referrer" onerror="this.style.display='none';this.nextElementSibling.style.display='grid'" /><div class="fallback" style="display:none">{esc(fallback_text)}</div></div>
      <div class="body">
        <span class="tag">{esc(category)}</span>
        <h1>{esc(record['title'])}</h1>
        <div class="brief-blocks"><section class="brief-block"><h2>{esc(cfg['core'])}</h2><p>{esc(full_brief)}</p></section></div>
        <div class="meta"><span>{esc(cfg['source'])}: <b>{esc(record['source'])}</b></span><span>{esc(published_label)}</span></div>
        <div class="cta"><a class="btn primary" href="{esc(record['source_url'])}" target="_blank" rel="noopener">{esc(cfg['open_source'])}</a><a class="btn" href="/{lang}/">{esc(cfg['back_to_site'])}</a></div>
      </div>
    </main>
  </div>
</body>
</html>
"""


def _record_for_item(
    item: dict,
    lang: str,
    home_updated_at: object,
    now: datetime,
    existing: dict | None,
) -> tuple[dict, str]:
    source_url = canonical_source_url(item.get("link"))
    if not source_url:
        raise ValueError("invalid source URL")
    title = normalize_text(item.get("title"))
    full_brief = normalize_text(item.get("full_brief"))
    if not title or not full_brief:
        raise ValueError("missing approved title or full_brief")
    brief_id = brief_id_for_url(source_url)
    if existing and _valid_existing_permalink(lang, existing):
        slug = str(existing["slug"])
        permalink = str(existing["permalink"])
    else:
        slug = slugify(title)
        permalink = safe_permalink(lang, slug, brief_id)
    image = absolute_image_url(item.get("image"))
    description = meta_description(full_brief)
    if not description:
        raise ValueError("approved full_brief produced an empty description")
    published_fallback = parse_datetime(home_updated_at, now)
    published = (
        str(existing.get("date_published"))
        if existing and existing.get("date_published")
        else iso_datetime(item.get("published_at"), published_fallback)
    )
    content = {
        "lang": lang,
        "source_url": source_url,
        "title": title,
        "full_brief": full_brief,
        "description": description,
        "image": image,
        "category": visible_category(item.get("category"), lang),
        "source": normalize_text(item.get("source")) or urlsplit(source_url).hostname or "BriefRooms",
    }
    content_hash = item_content_hash(content)
    unchanged = bool(existing and existing.get("content_hash") == content_hash)
    modified = (
        str(existing.get("date_modified"))
        if unchanged and existing and existing.get("date_modified")
        else now.astimezone(timezone.utc).isoformat(timespec="seconds")
    )
    record = {
        "brief_id": brief_id,
        "slug": slug,
        "permalink": permalink,
        "source_url": source_url,
        "title": title,
        "description": description,
        "image": image,
        "category": content["category"],
        "source": content["source"],
        "date_published": iso_datetime(published, published_fallback),
        "date_modified": iso_datetime(modified, now),
        "content_hash": content_hash,
    }
    return record, full_brief


def _archive_payload(lang: str, records: list[dict]) -> bytes:
    payload = {
        "version": 1,
        "language": lang,
        "items": sorted(records, key=lambda record: record["permalink"]),
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _sitemap_bytes(root_path: Path, archives: dict[str, list[dict]]) -> bytes:
    ET.register_namespace("", SITEMAP_NS)
    if root_path.exists():
        root = ET.fromstring(root_path.read_bytes())
    else:
        root = ET.Element(f"{{{SITEMAP_NS}}}urlset")
    preserved = []
    seen: set[str] = set()
    for node in list(root):
        loc_node = node.find(f"{{{SITEMAP_NS}}}loc")
        loc = normalize_text(loc_node.text if loc_node is not None else "")
        if not loc or loc in seen or loc in REMOVED_PUBLIC_URLS:
            continue
        if "brief.html?u=" in loc or "/pl/briefy/" in loc or "/en/briefs/" in loc:
            continue
        seen.add(loc)
        preserved.append(node)
    root[:] = preserved
    permanent = sorted(
        (record for records in archives.values() for record in records),
        key=lambda record: record["permalink"],
    )
    for record in permanent:
        loc = f"{SITE_URL}{record['permalink']}"
        if loc in seen:
            continue
        seen.add(loc)
        url_node = ET.SubElement(root, f"{{{SITEMAP_NS}}}url")
        ET.SubElement(url_node, f"{{{SITEMAP_NS}}}loc").text = loc
        ET.SubElement(url_node, f"{{{SITEMAP_NS}}}lastmod").text = record["date_modified"]
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def _atomic_write_many(files: dict[Path, bytes]) -> None:
    prepared: dict[Path, Path] = {}
    previous: dict[Path, bytes | None] = {}
    committed: list[Path] = []
    try:
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            previous[path] = path.read_bytes() if path.exists() else None
            handle = tempfile.NamedTemporaryFile(
                mode="wb", delete=False, dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
            )
            try:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                handle.close()
            prepared[path] = Path(handle.name)
        for path, temporary in prepared.items():
            os.replace(temporary, path)
            committed.append(path)
    except Exception:
        for path in reversed(committed):
            old_content = previous[path]
            if old_content is None:
                path.unlink(missing_ok=True)
            else:
                rollback = tempfile.NamedTemporaryFile(
                    mode="wb",
                    delete=False,
                    dir=path.parent,
                    prefix=f".{path.name}.rollback.",
                    suffix=".tmp",
                )
                try:
                    rollback.write(old_content)
                    rollback.flush()
                    os.fsync(rollback.fileno())
                finally:
                    rollback.close()
                os.replace(rollback.name, path)
        raise
    finally:
        for temporary in prepared.values():
            temporary.unlink(missing_ok=True)


def generate_all(root: Path = ROOT, now: datetime | None = None) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    pending: dict[Path, bytes] = {}
    archives: dict[str, list[dict]] = {}
    stats = {
        "generated": 0,
        "unchanged": 0,
        "skipped": 0,
        "homepages_updated": 0,
        "homepages_preserved": 0,
        "errors": [],
    }

    for lang, cfg in LANGUAGES.items():
        home_path = root / cfg["home"]
        archive_path = root / cfg["archive"]
        home = json.loads(home_path.read_text(encoding="utf-8"))
        if not isinstance(home, dict):
            raise ValueError(f"Invalid homepage feed: {home_path}")
        old_records = _load_archive(archive_path, lang)
        records_by_source = {
            canonical_source_url(record["source_url"]): dict(record) for record in old_records
        }
        processed: dict[str, dict] = {}
        for section in ("latest", "radar"):
            items = home.get(section, []) or []
            if not isinstance(items, list):
                raise ValueError(f"Invalid {section} section in {home_path}")
            for item in items:
                if not is_approved(item):
                    stats["skipped"] += 1
                    continue
                try:
                    source_url = canonical_source_url(item.get("link"))
                    if not source_url:
                        raise ValueError("invalid source URL")
                    if source_url in processed:
                        record = processed[source_url]
                    else:
                        existing = records_by_source.get(source_url)
                        record, full_brief = _record_for_item(
                            item, lang, home.get("updated_at"), now, existing
                        )
                        output_path = root / record["permalink"].lstrip("/")
                        expected_directory = (root / cfg["directory"]).resolve()
                        if output_path.resolve().parent != expected_directory:
                            raise ValueError("generated path escaped the brief directory")
                        html_bytes = render_brief_html(record, full_brief, lang).encode("utf-8")
                        if existing and existing.get("content_hash") == record["content_hash"]:
                            stats["unchanged"] += 1
                            if not output_path.exists():
                                pending[output_path] = html_bytes
                        else:
                            pending[output_path] = html_bytes
                            stats["generated"] += 1
                        records_by_source[source_url] = record
                        processed[source_url] = record
                    item["brief_id"] = record["brief_id"]
                    item["slug"] = record["slug"]
                    item["permalink"] = record["permalink"]
                except Exception as exc:
                    stats["skipped"] += 1
                    item_title = item.get("title", "") if isinstance(item, dict) else ""
                    stats["errors"].append(
                        {"lang": lang, "title": normalize_text(item_title), "error": str(exc)}
                    )
                    continue
        records = sorted(records_by_source.values(), key=lambda record: record["permalink"])
        archives[lang] = records
        pending[archive_path] = _archive_payload(lang, records)
        pending[home_path] = (json.dumps(home, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        index_path = root / cfg["index"]
        if index_path.exists():
            homepage_html = render_homepage_static(index_path, home, lang)
            if homepage_html is None:
                stats["homepages_preserved"] += 1
            else:
                pending[index_path] = homepage_html
                stats["homepages_updated"] += 1

    pending[root / "sitemap.xml"] = _sitemap_bytes(root / "sitemap.xml", archives)
    _atomic_write_many(pending)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository root")
    args = parser.parse_args()
    try:
        stats = generate_all(args.root.resolve())
    except Exception as exc:
        print(f"Permanent brief generation failed: {exc}", file=sys.stderr)
        return 1
    for error in stats["errors"]:
        print(
            f"Skipped {error['lang']} brief {error['title']!r}: {error['error']}",
            file=sys.stderr,
        )
    print(
        "Permanent briefs: "
        f"{stats['generated']} generated, {stats['unchanged']} unchanged, "
        f"{stats['skipped']} skipped; homepage HTML: "
        f"{stats['homepages_updated']} updated, {stats['homepages_preserved']} preserved"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
