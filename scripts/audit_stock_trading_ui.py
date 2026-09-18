#!/usr/bin/env python3
"""Viewport regression audit for the public Stock Trading room."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "internal" / "stock_trading_ui_audit.json"
SHOT_DIR = ROOT / "data" / "internal" / "stock_trading_ui_screenshots"
BASE = "http://127.0.0.1:8000"
PAGES = {
    "pl": "/pl/inwestycje/stock-trading.html",
    "en": "/en/investing/stock-trading.html",
}
VIEWPORTS = [(1600, 900), (1366, 768)]


def visible_count(page, selector: str) -> int:
    return page.locator(selector).evaluate_all(
        "els => els.filter(el => { const s=getComputedStyle(el); const r=el.getBoundingClientRect(); return s.display!=='none' && s.visibility!=='hidden' && r.width>0 && r.height>0; }).length"
    )


def card_widths(page, selector: str) -> list[float]:
    return page.locator(selector).evaluate_all(
        """els => els.filter(el => {
          const s=getComputedStyle(el), r=el.getBoundingClientRect();
          return s.display!=='none' && s.visibility!=='hidden' && r.width>0 && r.height>0;
        }).map(el => Math.round(el.getBoundingClientRect().width*10)/10)"""
    )


def text_overflow_count(page, selector: str) -> int:
    return page.locator(selector).evaluate_all(
        "els => els.filter(el => el.scrollWidth > el.clientWidth + 2).length"
    )


def run() -> int:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    failures: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for language, path in PAGES.items():
                for width, height in VIEWPORTS:
                    page = browser.new_page(viewport={"width": width, "height": height})
                    label = f"{language}-{width}x{height}"
                    try:
                        page.goto(BASE + path, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_selector("#str-market-us .str-position", timeout=15000)
                        page.wait_for_timeout(500)

                        default_visible = visible_count(page, "#str-market-us .str-position")
                        total_us = page.locator("#str-market-us .str-position").count()
                        if total_us > 1 and default_visible != 1:
                            failures.append(f"{label}: collapsed view exposes {default_visible}/{total_us} US cards")

                        default_widths = card_widths(page, "#str-market-us .str-position")
                        if default_widths and min(default_widths) < 520:
                            failures.append(f"{label}: collapsed active card too narrow: {min(default_widths)}px")

                        toggle = page.locator("[data-toggle-extra]")
                        expanded_visible = default_visible
                        expanded_widths = default_widths
                        if toggle.count():
                            toggle.click()
                            page.wait_for_timeout(250)
                            expanded_visible = visible_count(page, "#str-market-us .str-position")
                            expanded_widths = card_widths(page, "#str-market-us .str-position")
                            if expanded_visible != total_us:
                                failures.append(f"{label}: expanded view shows {expanded_visible}/{total_us} US cards")
                            if total_us > 1 and not page.locator("#str-market-us").evaluate("el => el.classList.contains('is-expanded-market')"):
                                failures.append(f"{label}: expanded market class missing")
                            if expanded_widths and min(expanded_widths) < 400:
                                failures.append(f"{label}: expanded card too narrow: {min(expanded_widths)}px")

                        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                        if overflow > 2:
                            failures.append(f"{label}: page horizontal overflow {overflow}px")

                        metric_overflows = text_overflow_count(page, "#str-market-us .str-position:not(.is-extra) .str-metrics strong")
                        if metric_overflows:
                            failures.append(f"{label}: {metric_overflows} metric values overflow their tiles")

                        shot = SHOT_DIR / f"stock-trading-{label}.png"
                        page.screenshot(path=str(shot), full_page=True)
                        rows.append({
                            "case": label,
                            "total_us_cards": total_us,
                            "default_visible_us_cards": default_visible,
                            "default_card_widths": default_widths,
                            "expanded_visible_us_cards": expanded_visible,
                            "expanded_card_widths": expanded_widths,
                            "horizontal_overflow_px": overflow,
                            "metric_overflow_count": metric_overflows,
                            "screenshot": str(shot.relative_to(ROOT)),
                        })
                    finally:
                        page.close()
        finally:
            browser.close()

    report = {
        "schema_version": "stock-trading-ui-audit-v1",
        "passed": not failures,
        "failures": failures,
        "cases": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(run())
