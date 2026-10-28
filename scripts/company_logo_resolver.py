#!/usr/bin/env python3
"""Read-only Stock Trading company identity/logo cache builder (GPW + US).

Reads canonical open positions; writes ONLY a public logo manifest and vetted PNG
assets. This process never writes portfolio, price, engine, or execution state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO = ROOT / "data/investments/stock_trading_portfolio.json"
MANIFEST = ROOT / "data/investments/company_logo_cache.json"
ASSET_ROOT = ROOT / "assets/company-logos"
USER_AGENT = "BriefRooms-CompanyLogoResolver/1.0 (public presentation metadata; contact https://briefrooms.com)"
MAX_IMAGE_BYTES = 300_000
MAX_LOOKUPS = 15
# Exchange identification is REQUIRED. Ticker alone is never an identity proof.
EXCHANGES = {"GPW": {"Q849235"}, "US": {"Q13677", "Q82059"}}
STOP = {"inc", "incorporated", "corporation", "corp", "co", "company", "limited",
        "ltd", "plc", "sa", "se", "spa", "spolka", "akcyjna", "the", "a",
        "common", "stock", "class", "ordinary", "shares", "ads", "adr", "ag", "group"}
TICKER_RE = re.compile(r"^[A-Z0-9]{1,12}$")
HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")

def get_json(url: str, timeout: int = 9) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if response.status != 200 or int(response.headers.get("Content-Length", "0")) > 1_000_000:
            raise ValueError("invalid upstream response")
        return json.loads(response.read(1_000_001))

def get_bytes(url: str, timeout: int = 9) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/png"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if response.status != 200 or int(response.headers.get("Content-Length", "0")) > MAX_IMAGE_BYTES:
            raise ValueError("oversized image")
        return response.read(MAX_IMAGE_BYTES + 1)

def tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", text or "")
    normalized = "".join(c for c in normalized if not unicodedata.combining(c)).lower()
    return {w for w in re.findall(r"[a-z0-9]+", normalized) if len(w) >= 3 and w not in STOP}

def matches_name(expected: str, entity: dict) -> bool:
    reference = tokens(expected)
    if not reference:
        return False
    aliases = [v.get("value", "") for v in entity.get("labels", {}).values()]
    for group in entity.get("aliases", {}).values():
        aliases.extend(v.get("value", "") for v in group)
    # Name overlap PLUS matching market ticker/exchange are all required.
    for label in aliases:
        actual = tokens(label)
        if actual and len(reference & actual) / min(len(reference), len(actual)) >= 0.75:
            return True
    return False

def statement_value(stmt: dict):
    return (stmt.get("mainsnak") or {}).get("datavalue", {}).get("value")

def ticker_values(stmt: dict) -> set[str]:
    result = set()
    for prop in ["P249"]:
        for claim in (stmt.get("qualifiers") or {}).get(prop, []):
            val = (claim.get("datavalue") or {}).get("value")
            if isinstance(val, str):
                result.add(val.upper().replace(".WA", ""))
    return result

def verified_identity(entity: dict, market: str, ticker: str, company_name: str) -> str | None:
    if not matches_name(company_name, entity):
        return None
    direct = {str(statement_value(s)).upper().replace(".WA", "")
              for s in entity.get("claims", {}).get("P249", [])
              if isinstance(statement_value(s), str) and s.get("rank") != "deprecated"}
    verified_venue = False
    for statement in entity.get("claims", {}).get("P414", []):
        if statement.get("rank") == "deprecated":
            continue
        venue = statement_value(statement)
        if not isinstance(venue, dict) or venue.get("id") not in EXCHANGES[market]:
            continue
        qual = ticker_values(statement)
        if ticker in qual or (not qual and ticker in direct):
            verified_venue = True
            break
    if not verified_venue:
        return None
    for statement in entity.get("claims", {}).get("P856", []):
        if statement.get("rank") == "deprecated":
            continue
        url = statement_value(statement)
        if not isinstance(url, str):
            continue
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
            continue
        host = (parsed.hostname or "").lower().rstrip(".")
        if host.startswith("www."):
            host = host[4:]
        # The favicon service receives only an ordinary HTTPS DNS hostname,
        # never an arbitrary URL, IP address, loopback or untrusted endpoint.
        if not HOST_RE.fullmatch(host) or "." not in host or ".." in host:
            continue
        if host.endswith((".local", ".internal", ".localhost", ".test", ".invalid")):
            continue
        if all(part.isdigit() for part in host.split(".")):
            continue
        return host
    return None

def search_queries(name: str) -> list[str]:
    text = re.sub(r"\s*[-–]\s*(?:class\s+[a-z]\s+)?(?:common|ordinary)\s+stock.*$", "", name, flags=re.I)
    text = re.sub(r"\s+(?:ADS|ADR)$", "", text, flags=re.I)
    clean = re.sub(r"\s+", " ", text).strip(" ,.")
    return [clean] if clean else []

def discover(market: str, ticker: str, name: str, get=get_json) -> tuple[str, str] | None:
    # Resolve Warsaw's Wikidata Q-ID by its exact name, rather than trusting
    # an unverified hard-coded exchange identifier.
    if market == "GPW":
        venue_query = urllib.parse.urlencode({"action": "wbsearchentities", "format": "json",
                                               "language": "en", "limit": "5",
                                               "search": "Warsaw Stock Exchange", "type": "item"})
        try:
            venues = get("https://www.wikidata.org/w/api.php?" + venue_query)
            for venue in venues.get("search", []):
                if str(venue.get("label", "")).casefold() == "warsaw stock exchange" and re.fullmatch(r"Q[0-9]+", venue.get("id", "")):
                    EXCHANGES["GPW"].add(venue["id"])
        except (OSError, ValueError, urllib.error.URLError):
            pass
    for query in search_queries(name):
        params = urllib.parse.urlencode({"action": "wbsearchentities", "format": "json", "language": "en",
                                          "limit": "8", "search": query, "type": "item"})
        try:
            found = get("https://www.wikidata.org/w/api.php?" + params)
        except (OSError, ValueError, urllib.error.URLError):
            continue
        candidates = [x.get("id") for x in found.get("search", []) if re.fullmatch(r"Q[0-9]+", x.get("id", ""))]
        for qid in candidates[:8]:
            try:
                item = get("https://www.wikidata.org/wiki/Special:EntityData/" + qid + ".json")
                entity = item.get("entities", {}).get(qid, {})
                domain = verified_identity(entity, market, ticker, name)
                if domain:
                    return domain, qid
            except (OSError, ValueError, urllib.error.URLError):
                continue
    return None

def valid_png(payload: bytes) -> bool:
    if len(payload) > MAX_IMAGE_BYTES or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    p = 8
    width = height = 0
    found_data = found_end = False
    while p + 12 <= len(payload):
        n = struct.unpack(">I", payload[p:p+4])[0]
        if n > MAX_IMAGE_BYTES or p + 12 + n > len(payload):
            return False
        kind = payload[p+4:p+8]
        body = payload[p+8:p+8+n]
        checksum = struct.unpack(">I", payload[p+8+n:p+12+n])[0]
        if zlib.crc32(kind + body) & 0xffffffff != checksum:
            return False
        if kind == b"IHDR":
            if p != 8 or n != 13:
                return False
            width, height = struct.unpack(">II", body[:8])
            if not (32 <= width <= 1024 and 32 <= height <= 1024):
                return False
        elif kind == b"IDAT":
            found_data = True
        elif kind == b"IEND":
            found_end = True
            if n or p + 12 != len(payload):
                return False
            break
        p += 12 + n
    return bool(found_end and found_data and width and height)

def curated_domain(market: str, ticker: str,
                   source: Path = ROOT / "scripts/stock-trading-company-logos-v2.js") -> str | None:
    """Existing reviewed UI mapping, split into its GPW and US namespaces.

    This is a *fallback* for legacy known stocks, never a global ticker guess.
    Newly encountered companies are always looked up via Wikidata first.
    If the existing JS format changes, fail closed instead of mis-scoping.
    """
    if market not in ("GPW", "US") or not TICKER_RE.fullmatch(ticker):
        return None
    try:
        code = source.read_text(encoding="utf-8")
        block = code.split("const DOMAINS = {", 1)[1].split("\n  };", 1)[0]
        split_at = block.index("\n\n    JSW:")
        us_block, gpw_block = block[:split_at], block[split_at:]
        us_overrides = code.split("const US_DOMAINS = Object.freeze({", 1)[1].split("\n  });", 1)[0]
    except (OSError, ValueError, IndexError):
        return None
    pairs = re.compile(r"\b([A-Z0-9]+)\s*:\s*'([a-zA-Z0-9.-]+)'")
    known = dict(pairs.findall(gpw_block if market == "GPW" else us_block))
    if market == "US":
        known.update(dict(pairs.findall(us_overrides)))
    host = known.get(ticker, "").lower()
    if not HOST_RE.fullmatch(host) or "." not in host or ".." in host:
        return None
    return host or None

def open_positions(data: dict) -> list[tuple[str, str, str]]:
    selected = []
    for market in ("GPW", "US"):
        for row in (data.get("markets", {}).get(market, {}).get("open_positions") or []):
            if not isinstance(row, dict) or str(row.get("status", "OPEN")).upper() != "OPEN":
                continue
            ticker = str(row.get("ticker") or row.get("symbol") or "").upper().removesuffix(".WA")
            name = str(row.get("name") or row.get("company") or "").strip()
            if TICKER_RE.fullmatch(ticker) and name:
                selected.append((market, ticker, name))
    return list(dict.fromkeys(selected))[:MAX_LOOKUPS]

def process(portfolio_path: Path = PORTFOLIO, manifest_path: Path = MANIFEST,
            asset_root: Path = ASSET_ROOT, discover_fn=discover,
            download=get_bytes) -> dict:
    positions = open_positions(json.loads(portfolio_path.read_text(encoding="utf-8")))
    existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    entries = existing.get("entries", {}) if isinstance(existing.get("entries"), dict) else {}
    entries = {k: v for k, v in entries.items() if isinstance(v, dict)}
    summary = {"positions": len(positions), "cached": 0, "created": 0, "unresolved": 0}
    for market, ticker, name in positions:
        key = market + ":" + ticker
        local = asset_root / market.lower() / (ticker.lower() + ".png")
        cached = entries.get(key, {})
        if cached.get("path") == "/assets/company-logos/" + market.lower() + "/" + ticker.lower() + ".png":
            if local.is_file() and valid_png(local.read_bytes()) and hashlib.sha256(local.read_bytes()).hexdigest() == cached.get("sha256"):
                summary["cached"] += 1
                continue
        try:
            matched = discover_fn(market, ticker, name)
            evidence = "wikidata_name_ticker_exchange_website"
            if not matched:
                # The existing UI has a market-scoped, maintainer-reviewed
                # domain map. Cache those already used logos too.
                known_domain = curated_domain(market, ticker)
                if known_domain:
                    matched = (known_domain, "")
                    evidence = "existing_market_scoped_ui_domain_map"
            if not matched:
                summary["unresolved"] += 1
                continue
            domain, entity_id = matched
            url = "https://www.google.com/s2/favicons?" + urllib.parse.urlencode(
                {"sz": "128", "domain_url": "https://" + domain})
            image = download(url)
            if not valid_png(image):
                summary["unresolved"] += 1
                continue
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(image)
            entries[key] = {
                "market": market, "ticker": ticker, "name": name,
                "path": "/assets/company-logos/" + market.lower() + "/" + ticker.lower() + ".png",
                "domain": domain, "entity_id": entity_id,
                "evidence": evidence,
                "image_source": "google_favicon_for_verified_website",
                "sha256": hashlib.sha256(image).hexdigest()
            }
            summary["created"] += 1
        except (OSError, ValueError, urllib.error.URLError) as err:
            print("logo_unresolved", key, type(err).__name__)
            summary["unresolved"] += 1
    result = {"schema_version": 1, "entries": dict(sorted(entries.items()))}
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if not manifest_path.exists() or manifest_path.read_text(encoding="utf-8") != rendered:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(rendered, encoding="utf-8")
    return summary

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portfolio", type=Path, default=PORTFOLIO)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--asset-root", type=Path, default=ASSET_ROOT)
    args = parser.parse_args()
    print(json.dumps(process(args.portfolio, args.manifest, args.asset_root), sort_keys=True))

if __name__ == "__main__":
    main()
