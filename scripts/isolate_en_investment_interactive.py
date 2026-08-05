#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "en/investing/portfolio-10k.html"
VARIANTS = ROOT / "audit/investment-en-interactive"
RESULTS = ROOT / "audit/investment-en-interactive-results"
OUTPUT = ROOT / "data/portfolio10k/investment_room_en_interactive_isolation.json"
BASE_URL = "http://127.0.0.1:8000"
SCRIPT_RE = re.compile(r'<script\b[^>]*\bsrc=["\']([^"\']+)["\'][^>]*>\s*</script>', re.I)


def script_name(src: str) -> str:
    return Path(src.split("?", 1)[0]).name


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:90]


def guard(blocked_names: Iterable[str]) -> str:
    payload = json.dumps(sorted(set(blocked_names)))
    return f"""<script>
(() => {{
  const blocked = new Set({payload});
  const blockedSrc = value => {{
    try {{ return blocked.has(new URL(String(value || ''), location.href).pathname.split('/').pop()); }}
    catch (_) {{ return false; }}
  }};
  const append = Node.prototype.appendChild;
  Node.prototype.appendChild = function(node) {{
    if (node?.tagName === 'SCRIPT' && blockedSrc(node.src)) return node;
    return append.call(this, node);
  }};
  const insert = Node.prototype.insertBefore;
  Node.prototype.insertBefore = function(node, reference) {{
    if (node?.tagName === 'SCRIPT' && blockedSrc(node.src)) return node;
    return insert.call(this, node, reference);
  }};
}})();
</script>"""


def make_variant(source: str, blocked: list[str]) -> str:
    blocked_names = {script_name(value) for value in blocked}

    def replace(match: re.Match[str]) -> str:
        src = match.group(1)
        if script_name(src) in blocked_names:
            return f"<!-- interactive audit blocked {src} -->"
        return match.group(0)

    html = SCRIPT_RE.sub(replace, source)
    return html.replace("<head>", "<head>" + guard(blocked_names), 1)


def run_variant(name: str, blocked: list[str]) -> dict:
    filename = slug(name)
    html_path = VARIANTS / f"{filename}.html"
    json_path = RESULTS / f"{filename}.json"
    html_path.write_text(make_variant(SOURCE.read_text(encoding="utf-8"), blocked), encoding="utf-8")
    env = os.environ.copy()
    env["PROBE_URL"] = f"{BASE_URL}/{html_path.relative_to(ROOT).as_posix()}?variant={filename}"
    env["PROBE_OUTPUT"] = str(json_path)
    started = datetime.now(timezone.utc)
    try:
        process = subprocess.run(
            [sys.executable, str(ROOT / "scripts/probe_en_investment_variant.py")],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=16,
        )
        if json_path.exists():
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        else:
            payload = {"passed": False, "fatal_error": "probe result missing"}
        payload.update({
            "name": name,
            "blocked": [script_name(value) for value in blocked],
            "process_exit": process.returncode,
            "timed_out": False,
            "elapsed_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
            "stdout_tail": process.stdout[-1500:],
            "stderr_tail": process.stderr[-1500:],
        })
        return payload
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "blocked": [script_name(value) for value in blocked],
            "passed": False,
            "process_exit": None,
            "timed_out": True,
            "elapsed_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
            "fatal_error": str(exc),
        }


def main() -> int:
    source = SOURCE.read_text(encoding="utf-8")
    scripts = SCRIPT_RE.findall(source)
    by_name = {script_name(src): src for src in scripts}

    likely = [
        "portfolio-10k-en-recovery.js",
        "portfolio-10k-dashboard-en.js",
        "portfolio-10k-usd-source.js",
        "portfolio-10k-execution-finalizer.js",
        "portfolio-10k-decision-overlay.js",
        "ai-tournament-public.js",
        "ai-tournament-readiness.js",
        "ai-tournament-company-profiles.js",
        "ai-tournament-summary.js",
        "investment-room-nav-order.js",
    ]
    variants: dict[str, list[str]] = {"baseline": []}
    for name in likely:
        if name in by_name:
            variants[f"without-{name}"] = [by_name[name]]
    tournament = [by_name[name] for name in likely if name.startswith("ai-tournament-") and name in by_name]
    variants["without-all-tournament"] = tournament
    variants["without-en-recovery-and-tournament"] = ([by_name["portfolio-10k-en-recovery.js"]] if "portfolio-10k-en-recovery.js" in by_name else []) + tournament
    variants["without-en-specific"] = [by_name[name] for name in ("portfolio-10k-usd-source.js", "portfolio-10k-dashboard-en.js", "portfolio-10k-en-recovery.js") if name in by_name]
    variants["without-post-dashboard"] = [
        src for src in scripts
        if script_name(src) in {
            "portfolio-10k-analytics-enhanced.js", "portfolio-10k-capital-summary.js",
            "portfolio-10k-explainers.js", "portfolio-10k-verified-material-loader.js",
            "portfolio-10k-decision-overlay.js", "portfolio-10k-execution-finalizer.js",
            "ai-tournament-public.js", "ai-tournament-readiness.js",
            "ai-tournament-company-profiles.js", "ai-tournament-summary.js",
            "investment-room-nav-order.js", "portfolio-10k-en-recovery.js",
        }
    ]

    VARIANTS.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    results = []
    for name, blocked in variants.items():
        result = run_variant(name, blocked)
        results.append(result)
        print(json.dumps({
            "name": name,
            "blocked": result.get("blocked"),
            "passed": result.get("passed"),
            "timed_out": result.get("timed_out"),
            "status": result.get("status"),
            "value": result.get("value"),
            "fatal_error": result.get("fatal_error"),
        }, ensure_ascii=False), flush=True)

    baseline = next(item for item in results if item["name"] == "baseline")
    recoveries = [item for item in results if item["name"] != "baseline" and item.get("passed")]
    report = {
        "schema_version": "investment-room-en-interactive-isolation-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_passed": bool(baseline.get("passed")),
        "recoveries": recoveries,
        "results": results,
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if recoveries or baseline.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
