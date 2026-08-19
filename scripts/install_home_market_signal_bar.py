#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_PATHS = [ROOT / "pl" / "index.html", ROOT / "en" / "index.html"]
ASSET_VERSION = 3
SCRIPT_RE = re.compile(r'<script\s+src="/scripts/home-weekly-top-position\.js\?v=\d+"\s+defer></script>', re.I)
SCRIPT_TAG = f'<script src="/scripts/home-weekly-top-position.js?v={ASSET_VERSION}" defer></script>'
LOCALE_LINK_ID = "home-market-signal-locale-link"
LOCALE_LINK_RE = re.compile(
    rf'<script\s+id="{re.escape(LOCALE_LINK_ID)}">.*?</script>',
    re.I | re.S,
)
LOCALE_LINK_TAG = f'''<script id="{LOCALE_LINK_ID}">(function(){{
  'use strict';
  var lang=document.documentElement.lang==='en'?'en':'pl';
  var target=lang==='en'?'/en/investing/portfolio-10k.html#overview':'/pl/inwestycje/portfel-10k.html#overview';
  function fixDailyTradeLink(){{
    var link=document.getElementById('home-market-signal');
    if(!link)return;
    var kicker=link.querySelector('.home-market-signal__kicker');
    if(!kicker||!/^Daily Trade/i.test(String(kicker.textContent||'').trim()))return;
    if(link.getAttribute('href')!==target)link.setAttribute('href',target);
  }}
  document.addEventListener('click',function(event){{
    var node=event.target&&event.target.closest?event.target.closest('#home-market-signal'):null;
    if(node)fixDailyTradeLink();
  }},true);
  if(typeof MutationObserver==='function')new MutationObserver(fixDailyTradeLink).observe(document.documentElement,{{childList:true,subtree:true}});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',fixDailyTradeLink,{{once:true}});else fixDailyTradeLink();
}})();</script>'''


def patch(source: str) -> str:
    if SCRIPT_RE.search(source):
        source = SCRIPT_RE.sub(SCRIPT_TAG, source, count=1)
    else:
        body_end = source.lower().rfind("</body>")
        if body_end < 0:
            raise RuntimeError("Homepage has no </body> marker")
        source = source[:body_end] + SCRIPT_TAG + "\n" + source[body_end:]

    source = LOCALE_LINK_RE.sub("", source)
    marker = source.find(SCRIPT_TAG)
    if marker < 0:
        raise RuntimeError("Current market signal script tag could not be located")
    insert_at = marker + len(SCRIPT_TAG)
    return source[:insert_at] + "\n" + LOCALE_LINK_TAG + source[insert_at:]


def validate(source: str) -> None:
    if SCRIPT_TAG not in source:
        raise RuntimeError(f"Missing current market signal script tag: {SCRIPT_TAG}")
    if source.count("home-weekly-top-position.js") != 1:
        raise RuntimeError("Homepage must load the market signal script exactly once")
    if source.count(f'id="{LOCALE_LINK_ID}"') != 1:
        raise RuntimeError("Homepage must contain exactly one localized Daily Trade link guard")
    if "/pl/inwestycje/portfel-10k.html#overview" not in source:
        raise RuntimeError("Polish Daily Trade overview target is missing")
    if "/en/investing/portfolio-10k.html#overview" not in source:
        raise RuntimeError("English Daily Trade overview target is missing")
    if "br-share-strip" not in source:
        raise RuntimeError("Homepage share bar anchor is missing")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed: list[str] = []
    for path in HOME_PATHS:
        source = path.read_text(encoding="utf-8")
        if args.check:
            validate(source)
            continue
        updated = patch(source)
        validate(updated)
        if updated != source:
            path.write_text(updated, encoding="utf-8", newline="\n")
            changed.append(str(path.relative_to(ROOT)))
    if args.check:
        print("HOME_MARKET_SIGNAL_BAR_OK")
    else:
        print("Updated: " + (", ".join(changed) if changed else "already current"))


if __name__ == "__main__":
    main()
