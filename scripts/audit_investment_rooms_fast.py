#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
TABS = ("overview", "portfolio", "benchmark", "agents", "projections", "rules", "brace", "analytics", "history")
BASE_URL = os.environ.get("AUDIT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
OUTPUT_PATH = Path(os.environ.get("AUDIT_OUTPUT_PATH", "data/portfolio10k/investment_room_full_audit.json"))
PAGES = {
    "pl": f"{BASE_URL}/pl/inwestycje/portfel-10k.html",
    "en": f"{BASE_URL}/en/investing/portfolio-10k.html",
}
EXPECTED_NAV = ("news", "investing", "health", "science", "geopolitics", "about")


def chrome_path() -> str:
    for name in CHROME_NAMES:
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError("Chrome/Chromium not found on runner")


def dump_dom(chrome: str, url: str) -> tuple[int, str, str]:
    process = subprocess.run(
        [
            chrome,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=12000",
            "--dump-dom",
            url,
        ],
        text=True,
        capture_output=True,
        timeout=35,
    )
    return process.returncode, process.stdout, process.stderr[-8000:]


def text_by_id(dom: str, element_id: str) -> str:
    match = re.search(rf'id="{re.escape(element_id)}"[^>]*>(.*?)</', dom, re.S)
    if not match:
        return ""
    return re.sub(r"<[^>]+>", "", match.group(1)).strip()


def active_panel(dom: str, tab: str) -> bool:
    patterns = (
        rf'<section[^>]+class="[^"]*\bi10k-panel\b[^"]*\bactive\b[^"]*"[^>]+data-panel="{tab}"',
        rf'<section[^>]+data-panel="{tab}"[^>]+class="[^"]*\bi10k-panel\b[^"]*\bactive\b[^"]*"',
    )
    return any(re.search(pattern, dom) for pattern in patterns)


def navigation_order(dom: str) -> list[str]:
    patterns = (
        r'<a[^>]+data-section="([^"]+)"[^>]+class="[^"]*br-site-header__link',
        r'<a[^>]+class="[^"]*br-site-header__link[^"]*"[^>]+data-section="([^"]+)"',
    )
    for pattern in patterns:
        values = re.findall(pattern, dom)
        if values:
            return values
    return []


def main() -> int:
    chrome = chrome_path()
    report: dict = {
        "schema_version": "investment-room-full-audit-v3",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "passed": True,
        "results": {},
    }

    for language, base_url in PAGES.items():
        language_result = {
            "passed": True,
            "tabs": [],
            "navigation_order": [],
            "navigation_expected": list(EXPECTED_NAV),
        }
        first_dom = ""
        for tab in TABS:
            code, dom, stderr = dump_dom(chrome, f"{base_url}?audit={datetime.now().timestamp()}#{tab}")
            if not first_dom:
                first_dom = dom
            status = text_by_id(dom, "data-status")
            value = text_by_id(dom, "portfolio-value")
            positions = text_by_id(dom, "positions-count")
            item = {
                "tab": tab,
                "chrome_exit": code,
                "panel_exists": f'data-panel="{tab}"' in dom,
                "active": active_panel(dom, tab),
                "status": status,
                "portfolio_value": value,
                "positions": positions,
                "stderr_tail": stderr,
            }
            item["loading"] = bool(re.search(r"loading|ładowanie|checking|sprawdzanie", status, re.I))
            item["data_loaded"] = not item["loading"] and value not in ("", "—", "-") and positions.isdigit()
            item["passed"] = code == 0 and item["panel_exists"] and item["active"] and item["data_loaded"]
            if not item["passed"]:
                language_result["passed"] = False
                report["passed"] = False
            language_result["tabs"].append(item)

        language_result["navigation_order"] = navigation_order(first_dom)
        language_result["navigation_passed"] = tuple(language_result["navigation_order"]) == EXPECTED_NAV
        if not language_result["navigation_passed"]:
            language_result["passed"] = False
            report["passed"] = False
        report["results"][language] = language_result

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
