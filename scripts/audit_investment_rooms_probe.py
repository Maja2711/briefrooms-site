#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

TABS = ("overview", "portfolio", "benchmark", "agents", "projections", "rules", "brace", "analytics", "history")
NAV = ("news", "investing", "health", "science", "geopolitics", "about")
BASE = os.environ.get("AUDIT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
OUTPUT = Path(os.environ.get("AUDIT_OUTPUT_PATH", "data/portfolio10k/investment_room_full_audit.json"))
PAGES = {
    "pl": f"{BASE}/pl/inwestycje/portfel-10k.html",
    "en": f"{BASE}/en/investing/portfolio-10k.html",
}


def critical(url: str, origin: str) -> bool:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" == origin and parsed.path.startswith(("/scripts/", "/data/", "/assets/", "/pl/", "/en/"))


def audit(browser, language: str, url: str) -> dict:
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
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
        page.goto(f"{url}?audit={datetime.now(timezone.utc).timestamp()}", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_selector(".i10k-tabs [data-tab='overview']", timeout=10000)
        page.wait_for_timeout(8000)
        snapshot = page.evaluate(
            """tabs => {
              const status=(document.querySelector('#data-status')?.textContent||'').trim();
              const value=(document.querySelector('#portfolio-value')?.textContent||'').trim();
              const positions=(document.querySelector('#positions-count')?.textContent||'').trim();
              const loaded=!/loading|ładowanie|checking|sprawdzanie/i.test(status)
                && !!value
                && !/^[-—]+(?:\\s*(?:zł|PLN|USD|\\$))?$/i.test(value)
                && /^\\d+$/.test(positions);
              const nav=[...document.querySelectorAll('#site-header .br-site-header__nav > a[data-section]')].map(a=>a.dataset.section);
              const results=[];
              for (const tab of tabs) {
                const button=document.querySelector(`.i10k-tabs [data-tab="${tab}"]`);
                const panel=document.querySelector(`.i10k-panel[data-panel="${tab}"]`);
                if (!button || !panel) {
                  results.push({tab,exists:false,clicked:false,active:false,visible:false,content_length:0,passed:false});
                  continue;
                }
                button.click();
                const style=getComputedStyle(panel);
                const text=(panel.innerText||'').trim();
                const active=panel.classList.contains('active');
                const visible=style.display!=='none' && style.visibility!=='hidden' && panel.getClientRects().length>0;
                results.push({tab,exists:true,clicked:true,active,visible,content_length:text.length,passed:active&&visible&&text.length>=20});
              }
              return {data:{status,portfolio_value:value,positions,loaded},navigation_order:nav,tabs:results};
            }""",
            list(TABS),
        )
        result["data"] = snapshot["data"]
        result["navigation_order"] = snapshot["navigation_order"]
        result["navigation_passed"] = tuple(snapshot["navigation_order"]) == NAV
        result["tabs"] = snapshot["tabs"]
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
    return result


def main() -> int:
    report = {
        "schema_version": "investment-room-full-audit-v6",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE,
        "passed": False,
        "results": {},
    }
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        for language, url in PAGES.items():
            report["results"][language] = audit(browser, language, url)
        report["passed"] = all(value.get("passed") for value in report["results"].values())
    except Exception as exc:
        report["fatal_error"] = f"{type(exc).__name__}: {exc}"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
