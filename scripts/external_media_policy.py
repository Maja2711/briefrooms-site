#!/usr/bin/env python3
"""Source-linked external image policy for BriefRooms.

The policy keeps editorial photography on publisher infrastructure. It never
writes remote image bytes to the repository. A URL is accepted only when the
image host belongs to the article host family or to a configured publisher CDN.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data" / "external_media_policy.json"
_FETCH_CACHE: dict[tuple[str, str], bool] = {}


def _load_policy() -> dict:
    try:
        payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


POLICY = _load_policy()
SOURCE_TO_IMAGE_HOSTS = {
    str(source).lower().strip("."): tuple(str(host).lower().strip(".") for host in hosts)
    for source, hosts in (POLICY.get("source_to_image_hosts") or {}).items()
    if isinstance(hosts, list)
}
BLOCKED_NAME_RE = re.compile(
    r"(?:^|[-_./])(?:" + "|".join(
        re.escape(str(item)) for item in (POLICY.get("blocked_name_patterns") or [])
    ) + r")(?:[-_./]|$)",
    re.I,
) if POLICY.get("blocked_name_patterns") else re.compile(r"a^", re.I)


def host_matches(host: str, suffix: str) -> bool:
    host = str(host or "").lower().strip(".")
    suffix = str(suffix or "").lower().strip(".")
    return bool(host and suffix and (host == suffix or host.endswith("." + suffix)))


def _hostname(url: str) -> str:
    try:
        return (urlsplit(str(url or "")).hostname or "").lower().strip(".")
    except ValueError:
        return ""


def allowed_image_hosts(source_url: str) -> tuple[str, ...]:
    source_host = _hostname(source_url)
    if not source_host:
        return ()
    allowed: list[str] = [source_host]
    for source_suffix, image_hosts in SOURCE_TO_IMAGE_HOSTS.items():
        if host_matches(source_host, source_suffix):
            allowed.extend(image_hosts)
    return tuple(dict.fromkeys(host for host in allowed if host))


def external_image_url(value: object, source_url: object, base_url: object = "") -> str:
    """Return a policy-approved HTTPS image URL or an empty string."""
    raw = html.unescape(str(value or "")).strip()
    source = str(source_url or "").strip()
    if not raw or not source:
        return ""
    candidate = urljoin(str(base_url or source), raw)
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return ""
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    image_host = parsed.hostname.lower().strip(".")
    source_host = _hostname(source)
    if not source_host:
        return ""
    path_and_query = f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
    if BLOCKED_NAME_RE.search(path_and_query):
        return ""
    allowed = allowed_image_hosts(source)
    same_family = host_matches(image_host, source_host) or host_matches(source_host, image_host)
    if not same_family and not any(host_matches(image_host, host) for host in allowed):
        return ""
    port = parsed.port
    default_port = port in (None, 443)
    netloc = image_host if default_port else f"{image_host}:{port}"
    return urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))


def image_is_fetchable(value: object, source_url: object, timeout: int = 8) -> bool:
    """Check content type and redirect target without storing the image."""
    source = str(source_url or "").strip()
    image_url = external_image_url(value, source)
    if not image_url:
        return False
    key = (source, image_url)
    if key in _FETCH_CACHE:
        return _FETCH_CACHE[key]
    response = None
    available = False
    try:
        response = requests.get(
            image_url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; BriefRoomsMediaGuard/1.0; +https://briefrooms.com)",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
            timeout=timeout,
            allow_redirects=True,
            stream=True,
        )
        final_url = external_image_url(response.url, source)
        content_type = str(response.headers.get("Content-Type") or "").lower()
        first_chunk = next(response.iter_content(chunk_size=96), b"") if response.ok else b""
        available = bool(response.ok and final_url and content_type.startswith("image/") and first_chunk)
    except Exception:
        available = False
    finally:
        if response is not None:
            response.close()
    _FETCH_CACHE[key] = available
    return available


def source_preview_label(source_name: object, lang: str = "pl") -> str:
    source = re.sub(r"\s+", " ", str(source_name or "")).strip()
    prefix = "Podgląd źródła" if lang == "pl" else "Source preview"
    return f"{prefix}: {source}" if source else prefix
