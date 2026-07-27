#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
WEEKLY = ROOT / "data" / "investments" / "weekly"
TZ = ZoneInfo("Europe/Warsaw")


def main() -> None:
    now = datetime.now(TZ)
    iso = now.isocalendar()
    path = WEEKLY / f"{iso.year}-W{iso.week:02d}.json"
    if not path.exists():
        raise SystemExit(f"Missing current weekly file: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for item in data.get("instruments", []):
        opened = (
            item.get("direction") in {"long", "short"}
            and item.get("entry_price") is not None
            and item.get("exit_price") is None
            and item.get("trade_status") == "open"
        )
        if not opened:
            continue

        for key in (
            "pending_entry_decision",
            "no_trade_decision",
            "no_trade_reason",
            "next_entry_reason",
            "validation_gate_reason",
        ):
            if key in item:
                item.pop(key, None)
                changed = True

        if item.get("next_entry_status") != "open":
            item["next_entry_status"] = "open"
            changed = True
        if item.get("entry_quality_status") in {None, "blocked_by_common_validation_gate", "waiting_for_first_completed_5m_bar_after_decision"}:
            item["entry_quality_status"] = "continuous_weekly_paper_position_open"
            changed = True

        direction_pl = "SHORT" if item.get("direction") == "short" else "LONG"
        direction_en = direction_pl
        rationale_pl = [
            f"Pozycja paper-trading {direction_pl} jest otwarta zgodnie z zasadą ciągłej ekspozycji tygodniowej.",
            "Kierunek wybrał turniej metod modelu; pozycja nie jest rzeczywistym zleceniem brokerskim.",
        ]
        rationale_en = [
            f"The {direction_en} paper-trading position is open under the continuous weekly exposure rule.",
            "Direction was selected by the model strategy tournament; this is not a real broker order.",
        ]
        if item.get("rationale_pl") != rationale_pl:
            item["rationale_pl"] = rationale_pl
            changed = True
        if item.get("rationale_en") != rationale_en:
            item["rationale_en"] = rationale_en
            changed = True

    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Normalized open weekly positions: changed={changed}")


if __name__ == "__main__":
    main()
