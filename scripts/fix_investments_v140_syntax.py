#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEEKLY = ROOT / "scripts" / "investments_weekly.py"
PATCHER = ROOT / "scripts" / "apply_investments_v140.py"


def fix(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    before = text
    text = text.replace('entry_thresholds(inst["id"], method)', "entry_thresholds(inst['id'], method)")
    if text != before:
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"fixed {path}")
    else:
        print(f"no syntax fix needed {path}")


def main() -> None:
    fix(WEEKLY)
    fix(PATCHER)


if __name__ == "__main__":
    main()
