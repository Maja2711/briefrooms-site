#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONTRACT = Path("data/content_update_contract.json")
HEALTH = Path("data/content_update_health.json")
HOME_PL = Path("pl/home_brief.json")
HOME_EN = Path("en/home_brief.json")
HOT_X = Path("data/hot_tweets.json")


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def age_hours(path: Path) -> float | None:
    data = load(path)
    dt = parse_time(data.get("updated_at"))
    if dt is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)


def repair_cron(path: Path, expected: str) -> bool:
    if not path.exists():
        raise SystemExit(f"Required workflow is missing: {path}")
    text = path.read_text(encoding="utf-8")
    if f'cron: "{expected}"' in text or f"cron: '{expected}'" in text:
        return False
    changed, count = re.subn(r"cron:\s*[\"'][^\"']+[\"']", f'cron: "{expected}"', text, count=1)
    if count != 1:
        raise SystemExit(f"Could not repair cron contract in {path}")
    path.write_text(changed, encoding="utf-8", newline="\n")
    return True


def require_markers(path: Path, markers: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    missing = [marker for marker in markers if marker not in text]
    if missing:
        raise SystemExit(f"Content update contract removed from {path}: {', '.join(missing)}")


def run_protection() -> list[str]:
    notes = []
    for cmd in (
        ["python", "scripts/protect_home_feed.py", "--validate"],
        ["python", "scripts/validate_hot_x_comments.py", "--validate"],
    ):
        try:
            result = subprocess.run(cmd, check=True, text=True, capture_output=True)
            notes.append((result.stdout or "").strip())
        except Exception as exc:
            notes.append(f"protection_error: {' '.join(cmd)}: {exc}")
    return notes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-output", default="")
    args = parser.parse_args()

    contract = load(CONTRACT)
    home = contract.get("homepage_and_news") or {}
    hot = contract.get("hot_x") or {}
    watch = contract.get("watchdog") or {}

    home_workflow = Path(str(home.get("workflow")))
    hot_workflow = Path(str(hot.get("workflow")))
    repaired = []
    if repair_cron(home_workflow, str(home.get("required_cron"))):
        repaired.append(str(home_workflow))
    if repair_cron(hot_workflow, str(hot.get("required_cron"))):
        repaired.append(str(hot_workflow))

    require_markers(home_workflow, ["protect_home_feed.py --backup", "protect_home_feed.py --validate", "continue-on-error: true"])
    require_markers(hot_workflow, ["validate_hot_x_comments.py --backup", "validate_hot_x_comments.py --validate", "update_hot_x_2x_daily.py"])

    protection_notes = run_protection()
    home_ages = [x for x in (age_hours(HOME_PL), age_hours(HOME_EN)) if x is not None]
    home_age = max(home_ages) if home_ages else None
    hot_age = age_hours(HOT_X)
    home_stale = home_age is None or home_age > float(watch.get("homepage_stale_after_hours", 5))
    hot_stale = hot_age is None or hot_age > float(watch.get("hot_x_stale_after_hours", 13))

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "contract_version": contract.get("contract_version"),
        "workflow_repairs": repaired,
        "homepage_age_hours": None if home_age is None else round(home_age, 3),
        "homepage_stale": home_stale,
        "hot_x_age_hours": None if hot_age is None else round(hot_age, 3),
        "hot_x_stale": hot_stale,
        "protection_notes": protection_notes,
        "status": "recovery_needed" if home_stale or hot_stale else "healthy",
    }
    save(HEALTH, report)

    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as fh:
            fh.write(f"home_stale={'true' if home_stale else 'false'}\n")
            fh.write(f"hot_x_stale={'true' if hot_stale else 'false'}\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
