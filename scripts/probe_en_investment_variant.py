#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

TABS = ("overview", "portfolio", "benchmark", "agents", "projections", "rules", "brace", "analytics", "history")


def main() -> int:
    url = os.environ["PROBE_URL"]
    output = Path(os.environ["PROBE_OUTPUT"])
    result = {
        "url": url,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": False,
        "tabs": [],
        "page_errors": [],
        "console_errors": [],
        "request_failures": [],
    }
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("pageerror", lambda error: result["page_errors"].append(str(error)))
        page.on("console", lambda message: result["console_errors"].append(message.text) if message.type == "error" else None)
        page.on("requestfailed", lambda request: result["request_failures"].append({"url": request.url, "error": request.failure or "unknown"}))
        response = page.goto(url, wait_until="domcontentloaded", timeout=10000)
        page.wait_for_timeout(4000)
        snapshot = page.evaluate(
            """tabs => {
              const status=(document.querySelector('#data-status')?.textContent||'').trim();
              const value=(document.querySelector('#portfolio-value')?.textContent||'').trim();
              const positions=(document.querySelector('#positions-count')?.textContent||'').trim();
              const loaded=!/loading|ładowanie|checking|sprawdzanie/i.test(status)
                && !!value
                && !/^[-—]+(?:\\s*(?:zł|PLN|USD|\\$))?$/i.test(value)
                && /^\\d+$/.test(positions);
              const tabResults=[];
              for (const tab of tabs) {
                const button=document.querySelector(`.i10k-tabs [data-tab="${tab}"]`);
                const panel=document.querySelector(`.i10k-panel[data-panel="${tab}"]`);
                if (!button || !panel) {
                  tabResults.push({tab,exists:false,clicked:false,active:false,visible:false,content_length:0,passed:false});
                  continue;
                }
                button.click();
                const style=getComputedStyle(panel);
                const text=(panel.innerText||'').trim();
                const active=panel.classList.contains('active');
                const visible=style.display!=='none' && style.visibility!=='hidden' && panel.getClientRects().length>0;
                tabResults.push({tab,exists:true,clicked:true,active,visible,content_length:text.length,passed:active&&visible&&text.length>=20});
              }
              return {status,value,positions,loaded,tabs:tabResults};
            }""",
            list(TABS),
        )
        result.update(snapshot)
        result["http_status"] = response.status if response else None
        result["passed"] = bool(
            snapshot.get("loaded")
            and all(item.get("passed") for item in snapshot.get("tabs", []))
            and not result["page_errors"]
        )
    except PlaywrightError as exc:
        result["fatal_error"] = str(exc)
    except Exception as exc:
        result["fatal_error"] = f"{type(exc).__name__}: {exc}"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
