#!/usr/bin/env python3
"""Repair only the 2026-08-06 Polish AI Outlook edition.

The English edition is serialized before and after the operation and must remain
byte-for-byte equivalent as JSON data. The script is idempotent and becomes a
no-op once the corrected Polish forecast ID is present.
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import update_ai_outlook_v3 as v3  # noqa: E402
from ai_outlook_pl_quality import QUALITY_GATE_VERSION, validate_pl_edition  # noqa: E402

TARGET_DATE = "2026-08-06"
CORRECTED_FORECAST_ID = "2026-08-06-pl-cash-access-quality-v1"
STATUS_PATH = ROOT / "data" / "ai_outlook_status.json"
AUDIT_PATH = ROOT / "data" / "internal" / "ai_outlook_audit" / f"{TARGET_DATE}.json"


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _cash_source(sources: list[dict[str, Any]]) -> dict[str, Any]:
    for source in sources:
        url = str(source.get("url") or "").lower()
        if "gotowke" in url or "got%C3%B3wk" in url or "wyplat" in url:
            selected = deepcopy(source)
            selected["source_language"] = "pl"
            return selected
    raise RuntimeError("cash-access source was not found in the current Polish edition")


def corrected_polish_edition(current: dict[str, Any]) -> dict[str, Any]:
    sources = current.get("sources")
    if not isinstance(sources, list):
        raise RuntimeError("current Polish edition has no source list")
    source = _cash_source([item for item in sources if isinstance(item, dict)])

    edition = deepcopy(current)
    edition.update(
        {
            "category": "Ekonomia",
            "title": "Dostęp do gotówki: pojawią się kolejne oficjalne wyjaśnienia",
            "thesis": (
                "Do 6 lutego 2027 r. zostaną opublikowane co najmniej 2 oficjalne "
                "komunikaty polskich banków lub instytucji publicznych, które konkretnie "
                "wyjaśnią limity, procedury albo dostępność wypłat gotówki."
            ),
            "horizon": "3–6 miesięcy",
            "rationale": (
                "Źródło opisuje trudności z uzyskaniem większej kwoty gotówki bez "
                "wcześniejszego zgłoszenia. Prognoza dotyczy wyłącznie tego zjawiska i "
                "sprawdza, czy wywoła ono mierzalną reakcję informacyjną banków lub instytucji."
            ),
            "confirmation": (
                "Do 2027-02-06 zostaną opublikowane co najmniej 2 oficjalne komunikaty "
                "dotyczące limitów, procedur albo dostępności wypłat gotówki w Polsce."
            ),
            "invalidation": (
                "Do 2027-02-06 nie zostaną opublikowane co najmniej 2 takie komunikaty."
            ),
            "resolution_summary": (
                "Prognoza jest trafna, jeżeli do 2027-02-06 liczba oficjalnych komunikatów "
                "dotyczących dostępności wypłat gotówki wyniesie co najmniej 2."
            ),
            "date_label": "6 sierpnia 2026",
            "probability": 62,
            "sources": [source],
            "selection_reason": (
                "Wybrano jeden spójny temat: dostępność wypłat gotówki i możliwe oficjalne "
                "wyjaśnienia procedur. Usunięto niezwiązany wątek cen paliw."
            ),
            "resolution_criteria": (
                "Do 2027-02-06 muszą pojawić się co najmniej 2 oficjalne komunikaty "
                "dotyczące dostępności wypłat gotówki."
            ),
            "forecast_id": CORRECTED_FORECAST_ID,
            "source_language": "pl",
            "source_policy": v3.SOURCE_POLICY["pl"],
            "resolution": {
                "schema_version": "resolution-v1",
                "metric": (
                    "Liczba oficjalnych komunikatów dotyczących dostępności wypłat gotówki w Polsce"
                ),
                "comparison_operator": ">=",
                "threshold": 2.0,
                "unit": "komunikaty",
                "baseline_date": TARGET_DATE,
                "baseline_value": 0.0,
                "data_source_for_verification": (
                    "Publiczne komunikaty NBP, ZBP, UOKiK, Rzecznika Finansowego i banków"
                ),
                "verification_url": "https://www.nbp.pl",
                "resolution_date": "2027-02-06",
                "geography": "Polska",
                "status": "open",
            },
            "quality_gate": {
                "version": QUALITY_GATE_VERSION,
                "status": "passed",
                "correction_reason": "mixed_topics_and_ambiguous_metric_removed",
            },
        }
    )

    engine = deepcopy(edition.get("engine") or {})
    engine.update(
        {
            "edition_language": "pl",
            "selected_area": "economy",
            "selection_mode": "manual_semantic_quality_correction",
            "engine_score": 70.0,
            "probability_method": "editorial_quality_correction_not_historically_calibrated",
            "candidate_count": 1,
            "top_candidates": [
                {
                    "candidate_id": "cash-access-quality-v1",
                    "area": "economy",
                    "title": edition["title"],
                    "engine_score": 70.0,
                    "passed_safety_gate": True,
                }
            ],
            "quality_gate_version": QUALITY_GATE_VERSION,
        }
    )
    score_breakdown = deepcopy(engine.get("score_breakdown") or {})
    score_breakdown.update(
        {
            "measurability": 90.0,
            "verifiability": 88.0,
            "causal_strength": 68.0,
            "speculation_risk": 28.0,
            "safety_gate": True,
            "safety_reasons": [],
            "source_count": 1,
            "distinct_hosts": 1,
            "independent_provenance_count": 1,
            "provenance_ids": [source["provenance_id"]],
        }
    )
    engine["score_breakdown"] = score_breakdown
    edition["engine"] = engine
    validate_pl_edition(edition)
    return edition


def main() -> int:
    payload = v3.load_json(v3.OUT, {})
    if payload.get("date") != TARGET_DATE:
        print(f"No-op: current AI Outlook date is {payload.get('date')!r}")
        return 0
    current_pl = payload.get("pl")
    if not isinstance(current_pl, dict):
        raise RuntimeError("missing current Polish edition")
    if current_pl.get("forecast_id") == CORRECTED_FORECAST_ID:
        validate_pl_edition(current_pl)
        print("No-op: corrected Polish AI Outlook is already installed")
        return 0

    original_en = _canonical(payload.get("en"))
    payload["pl"] = corrected_polish_edition(current_pl)
    if _canonical(payload.get("en")) != original_en:
        raise RuntimeError("English AI Outlook changed during Polish-only repair")
    v3.validate_payload(payload)
    validate_pl_edition(payload["pl"])

    _write(v3.OUT, payload)
    _write(v3.HISTORY_DIR / f"{TARGET_DATE}.json", payload)

    status = v3.load_json(STATUS_PATH, {})
    if isinstance(status, dict) and status.get("date") == TARGET_DATE:
        status["pl_forecast_id"] = CORRECTED_FORECAST_ID
        status["pl_quality_gate"] = QUALITY_GATE_VERSION
        _write(STATUS_PATH, status)

    audit = v3.load_json(AUDIT_PATH, {})
    if isinstance(audit, dict):
        editions = audit.setdefault("editions", {})
        pl_audit = editions.setdefault("pl", {})
        pl_audit["manual_quality_correction"] = {
            "forecast_id": CORRECTED_FORECAST_ID,
            "quality_gate": QUALITY_GATE_VERSION,
            "reason": "Removed the unrelated fuel-price thread and replaced the ambiguous metric.",
        }
        _write(AUDIT_PATH, audit)

    print(f"Installed corrected Polish AI Outlook: {CORRECTED_FORECAST_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
