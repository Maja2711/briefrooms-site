#!/usr/bin/env python3
from __future__ import annotations

import json
import os
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
EXPECTED_CONTROLLER = os.environ.get("AUDIT_CONTROLLER", "resilient-v9")
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
    console_errors: list[str] = []
    request_errors: list[dict] = []
    http_errors: list[dict] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("requestfailed", lambda req: request_errors.append({"url": req.url, "error": req.failure or "unknown"}) if critical(req.url, origin) else None)
    page.on("response", lambda res: http_errors.append({"url": res.url, "status": res.status}) if res.status >= 400 and critical(res.url, origin) else None)
    result = {
        "language": language,
        "url": url,
        "passed": False,
        "document": {},
        "data": {},
        "navigation_order": [],
        "navigation_passed": False,
        "tabs": [],
        "sidebar_tabs": [],
        "language_switch": {},
        "tournament_cta": {},
        "console_errors": console_errors,
        "page_errors": page_errors,
        "critical_request_failures": request_errors,
        "critical_http_errors": http_errors,
    }
    try:
        response = page.goto(f"{url}?audit={datetime.now(timezone.utc).timestamp()}", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(12000)
        snapshot = page.evaluate(
            """({tabs, language, expectedController}) => {
              const overview=document.querySelector('.i10k-tabs [data-tab="overview"]');
              const tabsRoot=document.querySelector('.i10k-tabs');
              const overviewStyle=overview ? getComputedStyle(overview) : null;
              const status=(document.querySelector('#data-status')?.textContent||'').trim();
              const value=(document.querySelector('#portfolio-value')?.textContent||'').trim();
              const positions=(document.querySelector('#positions-count')?.textContent||'').trim();
              const dataLoaded=!/loading|ładowanie|checking|sprawdzanie/i.test(status)
                && !!value
                && !/^[-—]+(?:\\s*(?:zł|PLN|USD|\\$))?$/i.test(value)
                && /^\\d+$/.test(positions)
                && document.body.dataset.investmentController===expectedController
                && document.body.dataset.investmentDataSource==='network'
                && document.body.dataset.investmentNetwork==='healthy'
                && document.body.dataset.investmentBrace==='ready'
                && document.body.dataset.investmentCurrency===(language==='pl'?'PLN':'USD');
              const nav=[...document.querySelectorAll('#site-header .br-site-header__nav > a[data-section]')].map(a=>a.dataset.section);
              const required={
                overview:['#portfolio-value','#allocation-list .allocation-row','#benchmark-bars .bar-row'],
                portfolio:['#portfolio-table tr'],benchmark:['#benchmark-full .bar-row'],
                agents:['#agent-cards .aitx-shell','#agent-cards .aitx-agent-card'],
                projections:['.projection-policy > div'],rules:['#rules-grid > div'],
                brace:['#brace-control-root > *','#brace-summary > *','#brace-positions > *'],
                analytics:['#kpis > *','#chart > *','#positions > *'],
                history:['#reviews > *','#audit-body > tr']
              };
              const inspect=(tab,selector)=>{
                const button=document.querySelector(selector);
                const panel=document.querySelector(`.i10k-panel[data-panel="${tab}"]`);
                if (!button || !panel) {
                  return {tab,exists:false,clicked:false,active:false,visible:false,content_length:0,passed:false};
                }
                button.click();
                const style=getComputedStyle(panel);
                const text=(panel.innerText||'').trim();
                const active=panel.classList.contains('active');
                const visible=style.display!=='none' && style.visibility!=='hidden' && panel.getClientRects().length>0;
                const nodeCounts=Object.fromEntries((required[tab]||[]).map(item=>[item,panel.querySelectorAll(item).length]));
                const nodesReady=(required[tab]||[]).every(item=>tab==='agents'&&item.includes('aitx-agent-card')?nodeCounts[item]===5:nodeCounts[item]>0);
                const noLoading=!/loading|ładowanie|checking|sprawdzanie/i.test(text);
                return {tab,exists:true,clicked:true,active,visible,content_length:text.length,node_counts:nodeCounts,nodes_ready:nodesReady,no_loading:noLoading,hash:location.hash,body_active:document.body.dataset.investmentActiveTab||'',guard:document.body.dataset.investmentNavigationGuard||'',passed:active&&visible&&text.length>=20&&nodesReady&&noLoading&&location.hash===`#${tab}`&&document.body.dataset.investmentActiveTab===tab&&document.body.dataset.investmentNavigationGuard==='active-v2'};
              };
              const tabResults=tabs.map(tab=>inspect(tab,`.i10k-tabs [data-tab="${tab}"]`));
              const sidebarResults=tabs.map(tab=>inspect(tab,`.i10k-side-nav [data-tab="${tab}"]`));
              window.BriefRoomsInvestmentNavigation?.activate('overview',false);
              const tournament=inspect('agents','.agents-wide [data-tab="agents"]');
              const languageLink=document.querySelector('#site-header .br-site-header__lang');
              const expectedOther=language==='pl'?'/en/investing/portfolio-10k.html':'/pl/inwestycje/portfel-10k.html';
              const languageSwitch={href:languageLink?new URL(languageLink.href).pathname:'',expected:expectedOther,passed:!!languageLink&&new URL(languageLink.href).pathname===expectedOther};
              return {
                document:{
                  title:document.title,
                  current_url:location.href,
                  ready_state:document.readyState,
                  body_child_count:document.body?.children.length||0,
                  body_text_length:(document.body?.innerText||'').length,
                  body_prefix:(document.body?.innerText||'').slice(0,600),
                  html_length:document.documentElement.outerHTML.length,
                  tabs_root_exists:!!tabsRoot,
                  overview_exists:!!overview,
                  overview_display:overviewStyle?.display||'',
                  overview_visibility:overviewStyle?.visibility||'',
                  overview_rects:overview?.getClientRects().length||0,
                  overview_outer_html:overview?.outerHTML||''
                },
                data:{status,portfolio_value:value,positions,loaded:dataLoaded,controller:document.body.dataset.investmentController||'',source:document.body.dataset.investmentDataSource||'',network:document.body.dataset.investmentNetwork||'',brace:document.body.dataset.investmentBrace||'',currency:document.body.dataset.investmentCurrency||''},
                navigation_order:nav,
                tabs:tabResults,
                sidebar_tabs:sidebarResults,
                language_switch:languageSwitch,
                tournament_cta:tournament
              };
            }""",
            {"tabs": list(TABS), "language": language, "expectedController": EXPECTED_CONTROLLER},
        )
        result["document"] = snapshot["document"]
        result["document"]["http_status"] = response.status if response else None
        result["data"] = snapshot["data"]
        result["navigation_order"] = snapshot["navigation_order"]
        result["navigation_passed"] = tuple(snapshot["navigation_order"]) == NAV
        result["tabs"] = snapshot["tabs"]
        result["sidebar_tabs"] = snapshot["sidebar_tabs"]
        result["language_switch"] = snapshot["language_switch"]
        result["tournament_cta"] = snapshot["tournament_cta"]
        result["passed"] = bool(
            result["data"].get("loaded")
            and result["navigation_passed"]
            and all(item.get("passed") for item in result["tabs"])
            and all(item.get("passed") for item in result["sidebar_tabs"])
            and result["language_switch"].get("passed")
            and result["tournament_cta"].get("passed")
            and not console_errors
            and not page_errors
            and not request_errors
            and not http_errors
        )
    except PlaywrightError as exc:
        result["fatal_error"] = str(exc)
    return result


def main() -> int:
    report = {
        "schema_version": "investment-room-full-audit-v9-bounded",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE,
        "expected_controller": EXPECTED_CONTROLLER,
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
