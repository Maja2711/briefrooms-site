#!/usr/bin/env python3
"""Expose BRACE historical-training state in the public challenger snapshot."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "data" / "investments" / "portfolio_10k_brace.json"
MEMORY_PATH = ROOT / "data" / "investments" / "portfolio_10k_brace_memory.json"


def read(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_PATH)
    parser.add_argument("--memory", type=Path, default=MEMORY_PATH)
    args = parser.parse_args()

    snapshot = read(args.snapshot)
    memory = read(args.memory)
    historical = memory.get("historical_training") or {}
    learning = snapshot.setdefault("learning", {})
    learning["historical_training"] = historical
    learning["historical_lessons"] = int(historical.get("lessons_total") or 0)
    learning["historical_training_status"] = historical.get("status") or "not_run"
    learning["historical_training_activated"] = bool(historical.get("activated"))

    note_pl = (
        "Historyczne uczenie walk-forward wykorzystuje wyłącznie odtwarzalne dane cenowe, "
        "a korekty są aktywowane dopiero po postępie na niewidzianym okresie testowym."
    )
    note_en = (
        "Historical walk-forward learning uses only reproducible price data and activates "
        "adjustments only after progress on an unseen test period."
    )
    if note_pl not in snapshot.setdefault("limitations_pl", []):
        snapshot["limitations_pl"].append(note_pl)
    if note_en not in snapshot.setdefault("limitations_en", []):
        snapshot["limitations_en"].append(note_en)

    args.snapshot.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "BRACE historical state published: "
        f"status={learning['historical_training_status']}, lessons={learning['historical_lessons']}"
    )


if __name__ == "__main__":
    main()
