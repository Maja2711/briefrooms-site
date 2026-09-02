#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

TABS = ("overview", "portfolio", "benchmark", "agents", "analytics", "history", "rules", "lab")
NAV = ("news", "investing", "health", "science", "geopolitics", "about")
BASE = os.environ.get("AUDIT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
OUTPUT = Path(os.environ.get("AUDIT_OUTPUT_PATH", "data/portfolio10k/investment_room_full_audit.json"))
EXPECTED_CONTROLLER = os.environ.get("AUDIT_CONTROLLER", "resilient-v9")
WORKER_TIMEOUT = int(os.environ.get("AUDIT_WORKER_TIMEOUT", "65"))
MAX_WORKERS = int(os.environ.get("AUDIT_MAX_WORKERS", "2"))
SETTLE_MS = int(os.environ.get("AUDIT_SETTLE_MS", "12000"))
PAGES = {
    "pl": f"{BASE}/pl/inwestycje/portfel-10k.html",
    "en": f"{BASE}/en/investing/portfolio-10k.html",
}
REQUIRED = {
    "overview": ("#portfolio-value", "#allocation-list .allocation-row", "#benchmark-bars .bar-row"),
    "portfolio": ("#portfolio-table tr",),
    "benchmark": ("#benchmark-full .bar-row",),
    "agents": ("#agent-cards .aitx-shell", "#agent-cards .aitx-agent-card"),
    "analytics": ("#kpis > *", "#chart > *", "#positions > *"),
    "history": ("#reviews > *", "#audit-body > tr"),
    "rules": ("#rules-grid > div",),
    "lab": ("#experiment-registry-content .experiment-registry-table tbody tr",),
}


def critical(url: str, origin: str) -> bool:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" == origin and parsed.path.startswith(("/scripts/", "/data/", "/assets/", "/pl/", "/en/"))


def pointer_click(page, selector: str) -> dict:
    locator = page.locator(selector).first
    if locator.count() != 1:
        return {"exists": False, "clicked": False}
    locator.scroll_into_view_if_needed(timeout=4000)
    box = locator.bounding_box(timeout=4000)
    if not box:
        return {"exists": True, "clicked": False, "error": "no bounding box"}
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    hit = page.evaluate(
        """({x,y}) => {
          const node=document.elementFromPoint(x,y);
          return {tag:node?.tagName||'',tab:node?.closest?.('[data-tab]')?.dataset?.tab||'',pointer_events:node?getComputedStyle(node).pointerEvents:''};
        }""",
        {"x": x, "y": y},
    )
    page.mouse.click(x, y)
    page.wait_for_timeout(250)
    return {"exists": True, "clicked": True, "hit": hit, "point": {"x": round(x), "y": round(y)}}


def panel_state(page, tab: str) -> dict:
    selectors = REQUIRED[tab]
    return page.evaluate(
        """({tab,selectors}) => {
          const panel=document.querySelector(`.i10k-panel[data-panel="${tab}"]`);
          if (!panel) return {tab,exists:false,passed:false};
          const text=(panel.innerText||'').trim();
          const style=getComputedStyle(panel);
          const counts=Object.fromEntries(selectors.map(selector=>[selector,panel.querySelectorAll(selector).length]));
          const nodesReady=selectors.every(selector=>tab==='agents'&&selector.includes('aitx-agent-card')?counts[selector]===5:counts[selector]>0);
          const active=panel.classList.contains('active');
          const visible=!panel.hidden&&style.display!=='none'&&style.visibility!=='hidden'&&panel.getClientRects().length>0;
          const noLoading=!/loading|ładowanie|checking|sprawdzanie/i.test(text);
          const guard=document.body.dataset.investmentNavigationGuard||'';
          const bodyActive=document.body.dataset.investmentActiveTab||'';
          return {tab,exists:true,active,visible,content_length:text.length,node_counts:counts,nodes_ready:nodesReady,no_loading:noLoading,hash:location.hash,body_active:bodyActive,guard,passed:active&&visible&&text.length>=20&&nodesReady&&noLoading&&location.hash===`#${tab}`&&bodyActive===tab&&guard==='active-v2'};
        }""",
        {"tab": tab, "selectors": list(selectors)},
    )


def click_and_verify(page, selector: str, tab: str) -> dict:
    result = {"tab": tab, "selector": selector, "passed": False}
    result.update(pointer_click(page, selector))
    if result.get("clicked"):
        result.update(panel_state(page, tab))
    result["passed"] = bool(
        result.get("clicked")
        and result.get("hit", {}).get("tab") == tab
        and result.get("hit", {}).get("pointer_events") != "none"
        and result.get("active")
        and result.get("visible")
        and result.get("nodes_ready")
        and result.get("no_loading")
        and result.get("hash") == f"#{tab}"
        and result.get("body_active") == tab
        and result.get("guard") == "active-v2"
    )
    return result


def worker(language: str, tab: str) -> int:
    url = PAGES[language]
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    result = {
        "language": language,
        "tab": tab,
        "url": url,
        "passed": False,
        "data": {},
        "navigation_order": [],
        "navigation_passed": False,
        "top": {},
        "sidebar": {},
        "language_switch": {},
        "tournament_cta": {},
        "console_errors": [],
        "page_errors": [],
        "critical_request_failures": [],
        "critical_http_errors": [],
    }
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1680, "height": 936}, locale="pl-PL" if language == "pl" else "en-US")
        page.on("pageerror", lambda error: result["page_errors"].append(str(error)))
        page.on("console", lambda message: result["console_errors"].append(message.text) if message.type == "error" else None)
        page.on("requestfailed", lambda request: result["critical_request_failures"].append({"url": request.url, "error": request.failure or "unknown"}) if critical(request.url, origin) else None)
        page.on("response", lambda response: result["critical_http_errors"].append({"url": response.url, "status": response.status}) if response.status >= 400 and critical(response.url, origin) else None)
        response = page.goto(f"{url}?audit={uuid.uuid4().hex}#overview", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(SETTLE_MS)
        result["data"] = page.evaluate(
            """({language,expectedController}) => {
              const status=(document.querySelector('#data-status')?.textContent||'').trim();
              const value=(document.querySelector('#portfolio-value')?.textContent||'').trim();
              const positions=(document.querySelector('#positions-count')?.textContent||'').trim();
              const controller=document.body.dataset.investmentController||'';
              const source=document.body.dataset.investmentDataSource||'';
              const network=document.body.dataset.investmentNetwork||'';
              const brace=document.body.dataset.investmentBrace||'';
              const currency=document.body.dataset.investmentCurrency||'';
              const loaded=!/loading|ładowanie|checking|sprawdzanie/i.test(status)&&!!value&&!/^[-—]+(?:\\s*(?:zł|PLN|USD|\\$))?$/i.test(value)&&/^\\d+$/.test(positions)&&controller===expectedController&&source==='network'&&network==='healthy'&&brace==='ready'&&currency===(language==='pl'?'PLN':'USD');
              return {status,portfolio_value:value,positions,controller,source,network,brace,currency,loaded};
            }""",
            {"language": language, "expectedController": EXPECTED_CONTROLLER},
        )
        result["data"]["http_status"] = response.status if response else None
        result["navigation_order"] = page.locator("#site-header .br-site-header__nav > a[data-section]").evaluate_all("nodes => nodes.map(node => node.dataset.section)")
        result["navigation_passed"] = tuple(result["navigation_order"]) == NAV
        result["top"] = click_and_verify(page, f".i10k-tabs [data-tab='{tab}']", tab)
        pointer_click(page, ".i10k-tabs [data-tab='overview']")
        result["sidebar"] = click_and_verify(page, f".i10k-side-nav [data-tab='{tab}']", tab)
        language_link = page.locator("#site-header .br-site-header__lang").first
        expected_other = "/en/investing/portfolio-10k.html" if language == "pl" else "/pl/inwestycje/portfel-10k.html"
        href = language_link.evaluate("node => new URL(node.href).pathname") if language_link.count() == 1 else ""
        result["language_switch"] = {"href": href, "expected": expected_other, "passed": href == expected_other}
        if tab == "overview" and result["language_switch"]["passed"]:
            pointer = pointer_click(page, "#site-header .br-site-header__lang")
            page.wait_for_timeout(1000)
            switched_path = page.evaluate("location.pathname")
            result["language_switch"].update({"pointer": pointer, "switched_path": switched_path, "passed": pointer.get("clicked") and switched_path == expected_other})
        if tab == "agents":
            pointer_click(page, ".i10k-tabs [data-tab='overview']")
            result["tournament_cta"] = click_and_verify(page, ".agents-wide [data-tab='agents']", "agents")
        else:
            result["tournament_cta"] = {"not_applicable": True, "passed": True}
        result["passed"] = bool(
            result["data"].get("loaded")
            and result["navigation_passed"]
            and result["top"].get("passed")
            and result["sidebar"].get("passed")
            and result["language_switch"].get("passed")
            and result["tournament_cta"].get("passed")
            and not result["console_errors"]
            and not result["page_errors"]
            and not result["critical_request_failures"]
            and not result["critical_http_errors"]
        )
    except Exception as exc:
        result["fatal_error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(result, ensure_ascii=False), flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if result["passed"] else 1)


def run_worker(language: str, tab: str) -> dict:
    env = os.environ.copy()
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", language, tab]
    try:
        process = subprocess.run(command, env=env, text=True, capture_output=True, timeout=WORKER_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        return {"language": language, "tab": tab, "passed": False, "timed_out": True, "fatal_error": str(exc)}
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    if not lines:
        return {"language": language, "tab": tab, "passed": False, "process_exit": process.returncode, "fatal_error": "worker produced no report", "stderr_tail": process.stderr[-1000:]}
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        return {"language": language, "tab": tab, "passed": False, "process_exit": process.returncode, "fatal_error": f"invalid worker report: {exc}", "stdout_tail": process.stdout[-1000:], "stderr_tail": process.stderr[-1000:]}
    payload["process_exit"] = process.returncode
    payload["timed_out"] = False
    if process.returncode != 0:
        payload["passed"] = False
    return payload


def main() -> int:
    OUTPUT.unlink(missing_ok=True)
    report = {
        "schema_version": "investment-room-isolated-audit-v10",
        "run_id": uuid.uuid4().hex,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE,
        "expected_controller": EXPECTED_CONTROLLER,
        "passed": False,
        "results": {},
    }
    jobs = [(language, tab) for language in PAGES for tab in TABS]
    workers: dict[tuple[str, str], dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(MAX_WORKERS, len(jobs)))) as executor:
        futures = {executor.submit(run_worker, language, tab): (language, tab) for language, tab in jobs}
        for future in as_completed(futures):
            language, tab = futures[future]
            workers[(language, tab)] = future.result()
            print(json.dumps({"language": language, "tab": tab, "passed": workers[(language, tab)].get("passed"), "timed_out": workers[(language, tab)].get("timed_out")}), flush=True)
    for language in PAGES:
        language_workers = [workers[(language, tab)] for tab in TABS]
        overview = workers[(language, "overview")]
        agents = workers[(language, "agents")]
        result = {
            "language": language,
            "url": PAGES[language],
            "passed": all(item.get("passed") for item in language_workers),
            "data": overview.get("data", {}),
            "navigation_order": overview.get("navigation_order", []),
            "navigation_passed": overview.get("navigation_passed", False),
            "tabs": [item.get("top", {"tab": item.get("tab"), "passed": False}) for item in language_workers],
            "sidebar_tabs": [item.get("sidebar", {"tab": item.get("tab"), "passed": False}) for item in language_workers],
            "language_switch": overview.get("language_switch", {}),
            "tournament_cta": agents.get("tournament_cta", {}),
            "console_errors": [error for item in language_workers for error in item.get("console_errors", [])],
            "page_errors": [error for item in language_workers for error in item.get("page_errors", [])],
            "critical_request_failures": [error for item in language_workers for error in item.get("critical_request_failures", [])],
            "critical_http_errors": [error for item in language_workers for error in item.get("critical_http_errors", [])],
            "timed_out_workers": [item.get("tab") for item in language_workers if item.get("timed_out")],
            "workers": language_workers,
        }
        report["results"][language] = result
    report["passed"] = all(value.get("passed") for value in report["results"].values())
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        worker(sys.argv[2], sys.argv[3])
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)