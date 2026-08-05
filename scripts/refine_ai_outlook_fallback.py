#!/usr/bin/env python3
"""Polish deterministic AI Outlook fallback copy without changing its evidence."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import update_ai_outlook as legacy  # noqa: E402
import update_ai_outlook_v3 as v3  # noqa: E402

REFINER_VERSION = "fallback-copy-v1"

TITLE_PREFIX = {
    "pl": {
        "economy": "Dalsze decyzje rynkowe",
        "geopolitics": "Kolejne oficjalne decyzje",
        "health": "Pojawią się nowe dane medyczne",
        "science": "Pojawią się nowe wyniki badań",
    },
    "en": {
        "economy": "Further market decisions",
        "geopolitics": "Further official decisions",
        "health": "Further medical evidence",
        "science": "Further research results",
    },
}


def compact_words(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;!?–—-\"'„”")
    text = re.sub(r"^(?:history suggests|analysis|explainer)\s+", "", text, flags=re.I)
    if len(text) <= limit:
        return text
    clipped = text[: limit + 1]
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return clipped.rstrip(" .,:;!?–—-") + "…"


def topic_from_edition(edition: dict[str, Any]) -> str:
    candidates = ((edition.get("engine") or {}).get("top_candidates") or [])
    raw = ""
    if candidates and isinstance(candidates[0], dict):
        raw = str(candidates[0].get("title") or "")
    if not raw:
        raw = str(edition.get("title") or "")

    # Headlines often use a short second sentence as a cause or teaser. The
    # first complete clause normally carries the actual event and must not be
    # replaced by fragments such as "Powodem ataki dronów".
    clauses = [part.strip() for part in re.split(r"(?<=[.!?])\s+|:\s+", raw) if part.strip()]
    useful = [part for part in clauses if len(part) >= 18]
    chosen = useful[0] if useful else (clauses[0] if clauses else raw)
    return compact_words(chosen, 58)


def refine_edition(language: str, edition: dict[str, Any]) -> None:
    engine = edition.get("engine") or {}
    area = str(engine.get("selected_area") or "economy")
    topic = topic_from_edition(edition)
    prefix = TITLE_PREFIX[language].get(area, TITLE_PREFIX[language]["economy"])
    edition["title"] = compact_words(f"{prefix} — {topic}", 100)

    if language == "pl":
        edition["thesis"] = legacy.compact(
            f"W ciągu 3–6 miesięcy temat „{topic}” powinien przynieść co najmniej dwa nowe, "
            "publiczne komunikaty, decyzje albo odczyty danych. Pozwoli to sprawdzić, czy "
            "kierunek widoczny w dzisiejszym źródle rzeczywiście się utrzymuje.",
            520,
        )
        edition["rationale"] = legacy.compact(
            f"Dzisiejsze źródło opisuje rozwijający się temat: „{topic}”. Został wybrany, "
            "ponieważ ma wyraźny dalszy ciąg i można go później zweryfikować w publicznych "
            "komunikatach, decyzjach lub danych.",
            480,
        )
    else:
        edition["thesis"] = legacy.compact(
            f"Within 3–6 months, the issue “{topic}” should produce at least two new public "
            "updates, decisions or data releases. That will allow the direction visible in "
            "today's source to be tested rather than simply repeated.",
            520,
        )
        edition["rationale"] = legacy.compact(
            f"Today's source describes a developing issue: “{topic}”. It was selected because "
            "it has a clear follow-up path and can later be checked against public updates, "
            "decisions or data.",
            480,
        )

    engine["fallback_copy_version"] = REFINER_VERSION
    edition["engine"] = engine


def refine(payload: dict[str, Any]) -> bool:
    if payload.get("generation_mode") != "deterministic_daily_fallback":
        return False
    for language in ("pl", "en"):
        edition = payload.get(language)
        if not isinstance(edition, dict):
            raise RuntimeError(f"missing {language} fallback edition")
        refine_edition(language, edition)
    payload["fallback_copy_version"] = REFINER_VERSION
    v3.validate_payload(payload)
    return True


def write(payload: dict[str, Any]) -> None:
    v3.write_json(v3.OUT, payload)
    v3.write_json(v3.HISTORY_DIR / f"{payload['date']}.json", payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    payload = v3.load_json(v3.OUT, {})
    changed = refine(payload)
    if args.validate_only:
        v3.validate_payload(payload)
        if payload.get("generation_mode") == "deterministic_daily_fallback":
            for language in ("pl", "en"):
                if (payload[language].get("engine") or {}).get("fallback_copy_version") != REFINER_VERSION:
                    raise RuntimeError(f"missing refined fallback copy for {language}")
        print("AI Outlook fallback copy is valid.")
        return 0

    if changed:
        write(payload)
        print("Refined deterministic PL and EN AI Outlook fallback copy.")
    else:
        print("Primary AI Outlook edition does not require fallback copy refinement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
