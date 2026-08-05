#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

TABS = ("overview", "portfolio", "benchmark", "agents", "projections", "rules", "brace", "analytics", "history")
BASE_URL = os.environ.get("AUDIT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
OUTPUT_PATH = Path(os.environ.get("AUDIT_OUTPUT_PATH", "data/portfolio10k/investment_room_full_audit.json"))
SCREENSHOT_DIR = Path(os.environ.get("AUDIT_SCREENSHOT_DIR", "artifacts/investment-room-audit"))
PAGES = {
    "pl": f"{BASE_URL}/pl/inwestycje/portfel-10k.html",
    "en": f"{BASE_URL}/en/investing/portfolio-10k.html",
}
EXPECTED_NAV = ("news", "investing", "health", "science", "geopolitics", "about")
LOADING_RE = re.compile(r"loading|ładowanie|checking|sprawdzanie", re.I)
PLACEHOLDER_RE = re.compile(r"^\s*[-—]+(?:\s*(?:zł|PLN|USD|\$))?\s*$", re.I)


def is_critical_url(url: str, expected_origin: str) -> bool:
    parsed = urlparse(url)
    if f"{parsed.scheme}://{parsed.netloc}" != expected_origin:
        return False
    return parsed.path.startswith(("/scripts/", "/data/", "/assets/", "/pl/", "/en/"))


def body_text(locator) -> str:
    try:
        return (locator.inner_text(timeout=3000) or "").strip()
    except PlaywrightError:
        return ""


def wait_for_data(page) -> None:
    page.wait_for_function(
        """
        () => {
          const status = document.querySelector('#data-status')?.textContent?.trim() || '';
          const value = document.querySelector('#portfolio-value')?.textContent?.trim() || '';
          const positions = document.querySelector('#positions-count')?.textContent?.trim() || '';
          const loading = /loading|ładowanie|checking|sprawdzanie/i.test(status);
          const placeholder = !value || /^[-—]+(?:\\s*(?:zł|PLN|USD|\\$))?$/i.test(value);
          return !loading && !placeholder && /^\\d+$/.test(positions);
        }
        """,
        timeout=25000,
    )


def audit_language(browser, language: str, url: str) -> dict:
    context = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="en-US" if language == "en" else "pl-PL")
    page = context.new_page()
    expected_origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    page_errors: list[str] = []
    console_errors: list[str] = []
    critical_request_failures: list[dict] = []
    critical_http_errors: list[dict] = []

    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on(
        "requestfailed",
        lambda request: critical_request_failures.append({
            "url": request.url,
            "error": request.failure or "unknown",
        }) if is_critical_url(request.url, expected_origin) else None,
    )
    page.on(
        "response",
        lambda response: critical_http_errors.append({
            "url": response.url,
            "status": response.status,
        }) if response.status >= 400 and is_critical_url(response.url, expected_origin) else None,
    )

    result = {
        "language": language,
        "url": url,
        "passed": False,
        "data": {},
        "navigation_order": [],
        "navigation_expected": list(EXPECTED_NAV),
        "navigation_passed": False,
        "tabs": [],
        "page_errors": page_errors,
        "console_errors": console_errors,
        "critical_request_failures": critical_request_failures,
        "critical_http_errors": critical_http_errors,
    }

    try:
        page.goto(f"{url}?audit={datetime.now(timezone.utc).timestamp()}", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#site-header .br-site-header__nav", timeout=15000)
        page.wait_for_selector(".i10k-tabs [data-tab='overview']", timeout=15000)

        data_error = ""
        try:
            wait_for_data(page)
        except PlaywrightTimeoutError as exc:
            data_error = str(exc)

        status = body_text(page.locator("#data-status"))
        value = body_text(page.locator("#portfolio-value"))
        positions = body_text(page.locator("#positions-count"))
        data_loaded = (
            not LOADING_RE.search(status)
            and bool(value)
            and not PLACEHOLDER_RE.match(value)
            and positions.isdigit()
        )
        result["data"] = {
            "status": status,
            "portfolio_value": value,
            "positions": positions,
            "loaded": data_loaded,
            "wait_error": data_error,
        }

        result["navigation_order"] = page.locator(
            "#site-header .br-site-header__nav > a[data-section]"
        ).evaluate_all("nodes => nodes.map(node => node.dataset.section)")
        result["navigation_passed"] = tuple(result["navigation_order"]) == EXPECTED_NAV

        for tab in TABS:
            trigger = page.locator(f".i10k-tabs [data-tab='{tab}']").first
            item = {
                "tab": tab,
                "exists": trigger.count() == 1,
                "clicked": False,
                "active": False,
                "visible": False,
                "content_length": 0,
                "contains_loading_placeholder": False,
                "passed": False,
            }
            if item["exists"]:
                try:
                    trigger.click(timeout=10000)
                    item["clicked"] = True
                    page.wait_for_timeout(350)
                    panel = page.locator(f".i10k-panel[data-panel='{tab}']").first
                    item["active"] = "active" in (panel.get_attribute("class") or "").split()
                    item["visible"] = panel.is_visible()
                    text = body_text(panel)
                    item["content_length"] = len(text)
                    item["contains_loading_placeholder"] = bool(LOADING_RE.search(text))
                except PlaywrightError as exc:
                    item["click_error"] = str(exc)
            item["passed"] = (
                item["exists"]
                and item["clicked"]
                and item["active"]
                and item["visible"]
                and item["content_length"] >= 20
                and not item["contains_loading_placeholder"]
            )
            result["tabs"].append(item)

        result["passed"] = (
            data_loaded
            and result["navigation_passed"]
            and all(item["passed"] for item in result["tabs"])
            and not page_errors
            and not critical_request_failures
            and not critical_http_errors
        )
    except PlaywrightError as exc:
        result["fatal_error"] = str(exc)
    finally:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(SCREENSHOT_DIR / f"{language}.png"), full_page=True)
        except PlaywrightError as exc:
            result["screenshot_error"] = str(exc)
        context.close()

    return result


def main() -> int:
    report = {
        "schema_version": "investment-room-full-audit-v4",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "passed": False,
        "results": {},
    }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for language, url in PAGES.items():
                    report["results"][language] = audit_language(browser, language, url)
            finally:
                browser.close()
        report["passed"] = all(result.get("passed") for result in report["results"].values())
    except Exception as exc:
        report["fatal_error"] = f"{type(exc).__name__}: {exc}"

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
