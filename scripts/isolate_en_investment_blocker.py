#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "en/investing/portfolio-10k.html"
VARIANTS = ROOT / "audit/investment-en-variants"
OUTPUT = ROOT / "data/portfolio10k/investment_room_en_isolation.json"
BASE_URL = "http://127.0.0.1:8000"

SCRIPT_RE = re.compile(r'<script\b[^>]*\bsrc=["\']([^"\']+)["\'][^>]*>\s*</script>', re.I)
STATUS_RE = re.compile(r'id=["\']data-status["\'][^>]*>(.*?)</', re.I | re.S)
VALUE_RE = re.compile(r'id=["\']portfolio-value["\'][^>]*>(.*?)</', re.I | re.S)
POSITIONS_RE = re.compile(r'id=["\']positions-count["\'][^>]*>(.*?)</', re.I | re.S)
TAG_RE = re.compile(r'<[^>]+>')


def text(match: re.Match[str] | None) -> str:
    return TAG_RE.sub("", match.group(1)).strip() if match else ""


def slug(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return clean[:90] or "baseline"


def blocker_script(blocked: Iterable[str]) -> str:
    names = sorted({Path(item.split("?", 1)[0]).name for item in blocked})
    payload = json.dumps(names)
    return f"""<script>
(() => {{
  const blocked = new Set({payload});
  const shouldBlock = value => {{
    try {{ return blocked.has(new URL(String(value || ''), location.href).pathname.split('/').pop()); }}
    catch (_) {{ return false; }}
  }};
  const patch = (prototype, name) => {{
    const original = prototype[name];
    if (typeof original !== 'function') return;
    prototype[name] = function(node, ...rest) {{
      if (node && node.tagName === 'SCRIPT' && shouldBlock(node.src)) return node;
      return original.call(this, node, ...rest);
    }};
  }};
  patch(Node.prototype, 'appendChild');
  patch(Node.prototype, 'insertBefore');
}})();
</script>"""


def variant_html(source: str, blocked: Iterable[str]) -> str:
    blocked_names = {Path(item.split("?", 1)[0]).name for item in blocked}

    def replace(match: re.Match[str]) -> str:
        src = match.group(1)
        name = Path(src.split("?", 1)[0]).name
        if name in blocked_names:
            return f"<!-- audit blocked: {src} -->"
        return match.group(0)

    updated = SCRIPT_RE.sub(replace, source)
    guard = blocker_script(blocked)
    return updated.replace("<head>", "<head>" + guard, 1)


def run_variant(executable: str, name: str, blocked: list[str]) -> dict:
    path = VARIANTS / f"{slug(name)}.html"
    path.write_text(variant_html(SOURCE.read_text(encoding="utf-8"), blocked), encoding="utf-8")
    url = f"{BASE_URL}/{path.relative_to(ROOT).as_posix()}?audit={datetime.now(timezone.utc).timestamp()}"
    command = [
        executable,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=6500",
        "--dump-dom",
        url,
    ]
    started = datetime.now(timezone.utc)
    try:
        process = subprocess.run(command, text=True, capture_output=True, timeout=12)
        dom = process.stdout
        status = text(STATUS_RE.search(dom))
        value = text(VALUE_RE.search(dom))
        positions = text(POSITIONS_RE.search(dom))
        loading = bool(re.search(r"loading|checking", status, re.I))
        data_loaded = bool(value) and value not in {"—", "-", "— USD"} and positions.isdigit() and not loading
        return {
            "name": name,
            "blocked": blocked,
            "timed_out": False,
            "exit_code": process.returncode,
            "elapsed_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
            "status": status,
            "portfolio_value": value,
            "positions": positions,
            "data_loaded": data_loaded,
            "tabs_present": 'class="i10k-tabs"' in dom or "class='i10k-tabs'" in dom,
            "stderr_tail": process.stderr[-2000:],
            "responsive": process.returncode == 0 and bool(dom),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "blocked": blocked,
            "timed_out": True,
            "exit_code": None,
            "elapsed_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
            "status": "",
            "portfolio_value": "",
            "positions": "",
            "data_loaded": False,
            "tabs_present": False,
            "stderr_tail": str(exc),
            "responsive": False,
        }


def main() -> int:
    source = SOURCE.read_text(encoding="utf-8")
    scripts = SCRIPT_RE.findall(source)
    basenames = [Path(item.split("?", 1)[0]).name for item in scripts]
    groups = {
        "baseline": [],
        "without-en-specific": [item for item in scripts if Path(item.split("?", 1)[0]).name in {
            "portfolio-10k-usd-source.js", "portfolio-10k-dashboard-en.js", "portfolio-10k-en-recovery.js"
        }],
        "without-post-dashboard": [item for item in scripts if Path(item.split("?", 1)[0]).name in {
            "portfolio-10k-analytics-enhanced.js", "portfolio-10k-capital-summary.js", "portfolio-10k-explainers.js",
            "portfolio-10k-verified-material-loader.js", "portfolio-10k-decision-overlay.js",
            "portfolio-10k-execution-finalizer.js", "ai-tournament-public.js", "ai-tournament-readiness.js",
            "ai-tournament-company-profiles.js", "ai-tournament-summary.js", "investment-room-nav-order.js",
            "portfolio-10k-en-recovery.js"
        }],
        "without-tournament": [item for item in scripts if Path(item.split("?", 1)[0]).name.startswith("ai-tournament-")],
        "without-en-recovery": [item for item in scripts if Path(item.split("?", 1)[0]).name == "portfolio-10k-en-recovery.js"],
        "without-dashboard-en": [item for item in scripts if Path(item.split("?", 1)[0]).name == "portfolio-10k-dashboard-en.js"],
        "without-usd-source": [item for item in scripts if Path(item.split("?", 1)[0]).name == "portfolio-10k-usd-source.js"],
    }
    for src in scripts:
        groups[f"without-{Path(src.split('?', 1)[0]).name}"] = [src]

    VARIANTS.mkdir(parents=True, exist_ok=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        executable = playwright.chromium.executable_path

    results = []
    for name, blocked in groups.items():
        result = run_variant(executable, name, blocked)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    baseline = next(item for item in results if item["name"] == "baseline")
    recoveries = [
        item for item in results
        if item["name"] != "baseline"
        and item["responsive"]
        and (item["data_loaded"] or (not baseline["responsive"] and item["tabs_present"]))
    ]
    report = {
        "schema_version": "investment-room-en-isolation-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scripts": basenames,
        "baseline": baseline,
        "recoveries": recoveries,
        "results": results,
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if recoveries else 1


if __name__ == "__main__":
    raise SystemExit(main())
