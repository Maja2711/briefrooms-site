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


def child_outside_card_count(page, child_selector: str) -> int:
    return page.locator(child_selector).evaluate_all(
        """els => els.filter(el => {
          const child = el.getBoundingClientRect();
          const cardEl = el.closest('.str-position');
          if (!cardEl) return true;
          const card = cardEl.getBoundingClientRect();
          return child.left < card.left - 1 || child.right > card.right + 1 ||
                 child.top < card.top - 1 || child.bottom > card.bottom + 1;
        }).length"""
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
                        page.wait_for_selector(".str-overview-position", timeout=15000)
                        page.wait_for_timeout(600)

                        nav_rects = page.locator(".stock-room-hero-switcher a").evaluate_all(
                            "els => els.map(el => { const r=el.getBoundingClientRect(); return {left:r.left, top:r.top, right:r.right, width:r.width, height:r.height}; })"
                        )
                        if len(nav_rects) != 3:
                            failures.append(f"{label}: expected 3 Trading Room hero buttons, got {len(nav_rects)}")
                        elif max(abs(item["top"] - nav_rects[0]["top"]) for item in nav_rects) > 2:
                            failures.append(f"{label}: Trading Room hero buttons are not in one horizontal row")
                        elif any(item["width"] < 86 for item in nav_rects):
                            failures.append(f"{label}: Trading Room hero button too narrow")

                        expected_motto = (
                            "Przewaga nie bierze się z aktywności. Bierze się z selekcji."
                            if language == "pl"
                            else "The edge is not activity. The edge is selection."
                        )
                        motto = " ".join(page.locator(".stock-room-quote p").inner_text().split())
                        normalized_motto = motto.strip('„”“”"')
                        if normalized_motto != expected_motto:
                            failures.append(f"{label}: unexpected hero motto: {motto}")

                        overview_total = page.locator(".str-overview-position").count()
                        overview_visible = visible_count(page, ".str-overview-position")
                        us_overview = page.locator('.str-overview-position[data-summary-market="US"]').count()
                        if overview_total != overview_visible:
                            failures.append(f"{label}: overview hides {overview_total-overview_visible} open positions")
                        if us_overview != 3:
                            failures.append(f"{label}: expected 3 visible US overview positions, got {us_overview}")

                        mpc = page.locator('.str-overview-position[data-summary-market="US"]').filter(has_text="MPC")
                        if mpc.count() != 1:
                            failures.append(f"{label}: MPC overview card missing")
                        else:
                            mpc_text = mpc.inner_text()
                            if "5" not in mpc_text or "000" not in mpc_text:
                                failures.append(f"{label}: MPC overview does not show 5K notional")

                        summary_widths = card_widths(page, ".str-overview-position")
                        if summary_widths and min(summary_widths) < 300:
                            failures.append(f"{label}: overview card too narrow: {min(summary_widths)}px")

                        overview_shot = SHOT_DIR / f"stock-trading-overview-{label}.png"
                        page.screenshot(path=str(overview_shot), full_page=True)

                        # Market tab is navigation into that market's complete ticket view.
                        page.locator('[data-market-jump="US"]').click()
                        page.wait_for_selector("#str-market-us .str-position", timeout=5000)
                        page.wait_for_timeout(200)
                        us_detail_visible = visible_count(page, "#str-market-us .str-position")
                        gpw_detail_count = page.locator("#str-market-gpw .str-position").count()
                        if us_detail_visible != 3:
                            failures.append(f"{label}: US market view shows {us_detail_visible}/3 tickets")
                        if gpw_detail_count != 0:
                            failures.append(f"{label}: US market view unexpectedly contains GPW ticket panel")

                        detail_widths = card_widths(page, "#str-market-us .str-position")
                        if detail_widths and min(detail_widths) < 300:
                            failures.append(f"{label}: detailed ticket too narrow: {min(detail_widths)}px")

                        # Return to overview, then the generic ticket button must show all tickets.
                        page.locator("[data-back-overview]").click()
                        page.wait_for_selector("[data-show-details]", timeout=5000)
                        page.locator("[data-show-details]").click()
                        page.wait_for_selector("#str-market-us .str-position", timeout=5000)
                        all_us_visible = visible_count(page, "#str-market-us .str-position")
                        if all_us_visible != 3:
                            failures.append(f"{label}: all-ticket view shows {all_us_visible}/3 US tickets")

                        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                        if overflow > 2:
                            failures.append(f"{label}: page horizontal overflow {overflow}px")

                        metric_overflows = text_overflow_count(page, "#str-market-us .str-position .str-metrics strong")
                        if metric_overflows:
                            failures.append(f"{label}: {metric_overflows} metric values overflow their tiles")

                        pnl_outside = child_outside_card_count(page, "#str-market-us .str-position .str-pnl")
                        if pnl_outside:
                            failures.append(f"{label}: {pnl_outside} P&L panels escape their ticket boundary")

                        pnl_text_overflows = text_overflow_count(page, "#str-market-us .str-position .str-pnl")
                        if pnl_text_overflows:
                            failures.append(f"{label}: {pnl_text_overflows} P&L panels contain clipped text")

                        detail_shot = SHOT_DIR / f"stock-trading-details-{label}.png"
                        page.screenshot(path=str(detail_shot), full_page=True)
                        rows.append({
                            "case": label,
                            "hero_nav_rects": nav_rects,
                            "motto": motto,
                            "overview_total": overview_total,
                            "overview_visible": overview_visible,
                            "us_overview_positions": us_overview,
                            "overview_card_widths": summary_widths,
                            "us_market_detail_visible": us_detail_visible,
                            "detail_card_widths": detail_widths,
                            "all_ticket_us_visible": all_us_visible,
                            "horizontal_overflow_px": overflow,
                            "metric_overflow_count": metric_overflows,
                            "pnl_outside_card_count": pnl_outside,
                            "pnl_text_overflow_count": pnl_text_overflows,
                            "overview_screenshot": str(overview_shot.relative_to(ROOT)),
                            "detail_screenshot": str(detail_shot.relative_to(ROOT)),
                        })
                    finally:
                        page.close()
        finally:
            browser.close()

    report = {
        "schema_version": "stock-trading-ui-audit-v2",
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
