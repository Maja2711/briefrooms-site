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

from comment_quality import QUALITY_STATUS, QUALITY_VERSION
from protect_home_feed import MIN_VISIBLE_ITEMS, valid_card

CONTRACT = Path("data/content_update_contract.json")
HEALTH = Path("data/content_update_health.json")
HOME_PL = Path("pl/home_brief.json")
HOME_EN = Path("en/home_brief.json")
HOT_X = Path("data/hot_tweets.json")
HOME_TEMPLATE = Path("config/workflow_templates/build-home-brief.yml")
HOT_TEMPLATE = Path("config/workflow_templates/hot-x-topics.yml")


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


def home_contract_current(path: Path, lang: str) -> bool:
    data = load(path)
    gate = data.get("comment_quality_gate") or {}
    items = list(data.get("latest") or []) + list(data.get("radar") or [])
    if (
        len(items) < MIN_VISIBLE_ITEMS[lang]
        or data.get("count") != len(items)
        or gate.get("status") != QUALITY_STATUS
        or gate.get("version") != QUALITY_VERSION
    ):
        return False

    return all(valid_card(item, lang) for item in items)


def workflow_has_markers(text: str, markers: list[str]) -> bool:
    return all(marker in text for marker in markers)


def ensure_workflow(path: Path, template: Path, expected_cron: str, markers: list[str]) -> str | None:
    if not template.exists():
        raise SystemExit(f"Missing canonical workflow template: {template}")
    template_text = template.read_text(encoding="utf-8")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template_text, encoding="utf-8", newline="\n")
        return "restored_missing_workflow_from_template"

    text = path.read_text(encoding="utf-8")
    if not workflow_has_markers(text, markers):
        path.write_text(template_text, encoding="utf-8", newline="\n")
        return "restored_removed_protection_from_template"

    if f'cron: "{expected_cron}"' not in text and f"cron: '{expected_cron}'" not in text:
        changed, count = re.subn(r"cron:\s*[\"'][^\"']+[\"']", f'cron: "{expected_cron}"', text, count=1)
        if count != 1:
            path.write_text(template_text, encoding="utf-8", newline="\n")
            return "restored_invalid_schedule_from_template"
        path.write_text(changed, encoding="utf-8", newline="\n")
        return "repaired_schedule"
    return None


def run_protection() -> list[str]:
    notes = []
    for cmd in (
        ["python", "scripts/protect_home_feed.py", "--validate-passive"],
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
    repairs = []

    home_repair = ensure_workflow(
        home_workflow,
        HOME_TEMPLATE,
        str(home.get("required_cron")),
        [
            "protect_home_feed.py --backup",
            "protect_home_feed.py --validate",
            "comment_quality.py",
            "test_comment_quality.py",
            "read_and_summarize_articles.py",
            "continue-on-error: true",
            "git pull --ff-only origin main",
            "Skip stale publish because generator code changed on main.",
        ],
    )
    if home_repair:
        repairs.append({"workflow": str(home_workflow), "action": home_repair})

    hot_repair = ensure_workflow(
        hot_workflow,
        HOT_TEMPLATE,
        str(hot.get("required_cron")),
        ["validate_hot_x_comments.py --backup", "validate_hot_x_comments.py --validate", "update_hot_x_2x_daily.py"],
    )
    if hot_repair:
        repairs.append({"workflow": str(hot_workflow), "action": hot_repair})

    protection_notes = run_protection()
    home_ages = (age_hours(HOME_PL), age_hours(HOME_EN))
    home_age = max(home_ages) if all(x is not None for x in home_ages) else None
    hot_age = age_hours(HOT_X)
    home_contract_ok = home_contract_current(HOME_PL, "pl") and home_contract_current(HOME_EN, "en")
    home_stale = (
        not home_contract_ok
        or home_age is None
        or home_age > float(watch.get("homepage_stale_after_hours", 5))
    )
    hot_stale = hot_age is None or hot_age > float(watch.get("hot_x_stale_after_hours", 13))

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "contract_version": contract.get("contract_version"),
        "workflow_repairs": repairs,
        "homepage_age_hours": None if home_age is None else round(home_age, 3),
        "homepage_contract_current": home_contract_ok,
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
