#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

TABS = ("overview", "portfolio", "benchmark", "agents", "projections", "rules", "brace", "analytics", "history")
NAV = ("news", "investing", "health", "science", "geopolitics", "about")
BASE = os.environ.get("AUDIT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
OUTPUT = Path(os.environ.get("AUDIT_OUTPUT_PATH", "data/portfolio10k/investment_room_full_audit.json"))
SHOTS = Path(os.environ.get("AUDIT_SCREENSHOT_DIR", "artifacts/investment-room-audit"))
PAGES = {
    "pl": f"{BASE}/pl/inwestycje/portfel-10k.html",
    "en": f"{BASE}/en/investing/portfolio-10k.html",
}


def critical(url: str, origin: str) -> bool:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" == origin and parsed.path.startswith(("/scripts/", "/data/", "/assets/", "/pl/", "/en/"))


def audit(browser, language: str, url: str) -> dict:
    context = browser.new_context(viewport={"width": 1600, "height": 1000})
    page = context.new_page()
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    page_errors: list[str] = []
    request_errors: list[dict] = []
    http_errors: list[dict] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("requestfailed", lambda req: request_errors.append({"url": req.url, "error": req.failure or "unknown"}) if critical(req.url, origin) else None)
    page.on("response", lambda res: http_errors.append({"url": res.url, "status": res.status}) if res.status >= 400 and critical(res.url, origin) else None)
    result = {
        "language": language,
        "url": url,
        "passed": False,
        "data": {},
        "navigation_order": [],
        "navigation_passed": False,
        "tabs": [],
        "page_errors": page_errors,
        "critical_request_failures": request_errors,
        "critical_http_errors": http_errors,
    }
    try:
        page.goto(f"{url}?audit={datetime.now(timezone.utc).timestamp()}", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#site-header .br-site-header__nav", timeout=15000)
        page.wait_for_selector(".i10k-tabs [data-tab='overview']", timeout=15000)
        wait_error = ""
        try:
            page.wait_for_function(
                """() => {
                  const status=(document.querySelector('#data-status')?.textContent||'').trim();
                  const value=(document.querySelector('#portfolio-value')?.textContent||'').trim();
                  const count=(document.querySelector('#positions-count')?.textContent||'').trim();
                  return !/loading|ładowanie|checking|sprawdzanie/i.test(status)
                    && value && !/^[-—]+(?:\\s*(?:zł|PLN|USD|\\$))?$/i.test(value)
                    && /^\\d+$/.test(count);
                }""",
                timeout=18000,
            )
        except PlaywrightTimeoutError as exc:
            wait_error = str(exc)
        result["data"] = page.evaluate(
            """() => ({
              status:(document.querySelector('#data-status')?.textContent||'').trim(),
              portfolio_value:(document.querySelector('#portfolio-value')?.textContent||'').trim(),
              positions:(document.querySelector('#positions-count')?.textContent||'').trim(),
              loaded:!(/loading|ładowanie|checking|sprawdzanie/i.test((document.querySelector('#data-status')?.textContent||'')))
                && !!(document.querySelector('#portfolio-value')?.textContent||'').trim()
                && !/^[-—]+(?:\\s*(?:zł|PLN|USD|\\$))?$/i.test((document.querySelector('#portfolio-value')?.textContent||'').trim())
                && /^\\d+$/.test((document.querySelector('#positions-count')?.textContent||'').trim())
            })"""
        )
        result["data"]["wait_error"] = wait_error
        result["navigation_order"] = page.locator("#site-header .br-site-header__nav > a[data-section]").evaluate_all("nodes => nodes.map(n => n.dataset.section)")
        result["navigation_passed"] = tuple(result["navigation_order"]) == NAV
        for tab in TABS:
            item = page.evaluate(
                """tab => {
                  const button=document.querySelector(`.i10k-tabs [data-tab="${tab}"]`);
                  const panel=document.querySelector(`.i10k-panel[data-panel="${tab}"]`);
                  if (!button || !panel) return {tab,exists:false,clicked:false,active:false,visible:false,content_length:0,passed:false};
                  button.click();
                  const style=getComputedStyle(panel);
                  const text=(panel.innerText||'').trim();
                  const active=panel.classList.contains('active');
                  const visible=style.display!=='none' && style.visibility!=='hidden' && panel.getClientRects().length>0;
                  return {tab,exists:true,clicked:true,active,visible,content_length:text.length,passed:active&&visible&&text.length>=20};
                }""",
                tab,
            )
            result["tabs"].append(item)
        result["passed"] = bool(
            result["data"].get("loaded")
            and result["navigation_passed"]
            and all(item.get("passed") for item in result["tabs"])
            and not page_errors
            and not request_errors
            and not http_errors
        )
    except PlaywrightError as exc:
        result["fatal_error"] = str(exc)
    finally:
        SHOTS.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(SHOTS / f"{language}.png"), full_page=False)
        except PlaywrightError as exc:
            result["screenshot_error"] = str(exc)
        context.close()
    return result


def main() -> int:
    report = {
        "schema_version": "investment-room-full-audit-v5",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE,
        "passed": False,
        "results": {},
    }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for language, url in PAGES.items():
                    report["results"][language] = audit(browser, language, url)
            finally:
                browser.close()
        report["passed"] = all(value.get("passed") for value in report["results"].values())
    except Exception as exc:
        report["fatal_error"] = f"{type(exc).__name__}: {exc}"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
