#!/usr/bin/env python3
"""Independent GPW reviewer with a narrow fail-safe mandate.

The ranking engine chooses the best daily candidate. This reviewer does not
re-rank it and does not require news sources to justify ATR-derived entry/SL/TP
levels. It vetoes only evidence-integrity, direction-conflict, source-integrity
or risk-plan defects. Weak catalyst/conviction is already priced into the
composite score and is not by itself a fatal admission failure.
"""
from __future__ import annotations

import json
from typing import Any

try:
    from scripts import gpw_daily_pick as gpw
except ModuleNotFoundError:
    import gpw_daily_pick as gpw


def review(candidate: dict[str, Any], analysis: dict[str, Any], score: float) -> dict[str, Any]:
    import requests

    runtime = gpw.get_ai_runtime()
    sources = list(candidate.get("sources") or [])
    allowed_ids = {str(source.get("id")) for source in sources if source.get("id")}
    prompt = {
        "task": (
            "Wykonaj niezależny audyt integralności kandydata GPW na paper trade LONG 1-2 sesje. "
            "Nie wybierasz ponownie spółki i nie oceniasz, czy score jest wystarczająco wysoki. "
            "Katalizator może być słaby — to obniża conviction/score, ale samo w sobie NIE jest fatalnym veto."
        ),
        "separation_of_responsibility": [
            "Źródła mają potwierdzać fakty i tezę/katalizator, nie poziomy techniczne.",
            "entry_zone, stop i target są wyliczane z danych rynkowych/ATR/RR; NIE wymagaj, aby artykuł uzasadniał te ceny.",
            "Brak mocnego katalizatora nie jest fatalnym błędem, jeżeli analiza uczciwie to odzwierciedla.",
            "Veto jest dozwolone tylko przy konkretnym błędzie integralności lub bezpieczeństwa wymienionym w fatal_checks.",
        ],
        "fatal_checks": {
            "evidence_supported": (
                "Czy istotne twierdzenia faktyczne w thesis/why_now mają pokrycie w przekazanych źródłach?"
            ),
            "direction_not_contradicted": (
                "Czy źródła nie zawierają bezpośredniego, świeżego faktu, który czyni kierunek LONG wewnętrznie sprzecznym z samą tezą? "
                "Sama niepewność, korekta rynku albo słaby katalizator to NIE jest automatyczna sprzeczność."
            ),
            "source_integrity_ok": (
                "Czy użyte source_ids istnieją w dostarczonej liście i nie ma oczywistego błędu typu/treści źródła, który unieważnia dowód?"
            ),
            "risk_plan_ok": (
                "Czy matematyczny plan ma stop < reference/entry < target, dodatnie ceny i reward_risk >= 1.5? "
                "Nie oceniaj poziomów na podstawie artykułów."
            ),
        },
        "candidate": {
            "symbol": candidate.get("symbol"),
            "name": candidate.get("name"),
            "composite_score": score,
            "reference_price": candidate.get("reference_price"),
            "entry_zone": candidate.get("entry_zone"),
            "stop": candidate.get("stop"),
            "target": candidate.get("target"),
            "reward_risk": candidate.get("reward_risk"),
            "analysis": analysis,
            "sources": sources,
        },
        "output_schema": {
            "evidence_supported": True,
            "direction_not_contradicted": True,
            "source_integrity_ok": True,
            "risk_plan_ok": True,
            "supported_source_ids": ["src-id"],
            "fatal_issues": [],
            "note": "krótkie uzasadnienie bez ponownego rankingu"
        },
    }
    payload = gpw.request_json_completion(
        post=requests.post,
        runtime=runtime,
        messages=[
            {
                "role": "system",
                "content": (
                    "Jesteś audytorem integralności, nie drugim selektorem. "
                    "Nie odrzucaj za niski score, brak silnego katalizatora ani za to, że news nie potwierdza ATR/TP/SL. "
                    "Odrzucaj tylko za konkretny fatalny błąd w jednym z czterech pól."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        max_tokens=900,
        temperature=0,
        review=True,
        timeout=45,
    )

    supported = [
        str(value)
        for value in (payload.get("supported_source_ids") or [])
        if str(value) in allowed_ids
    ]
    checks = {
        "evidence_supported": bool(payload.get("evidence_supported")),
        "direction_not_contradicted": bool(payload.get("direction_not_contradicted")),
        "source_integrity_ok": bool(payload.get("source_integrity_ok")),
        "risk_plan_ok": bool(payload.get("risk_plan_ok")),
    }
    fatal_issues = [
        str(value).strip()[:260]
        for value in (payload.get("fatal_issues") or [])[:4]
        if str(value).strip()
    ]
    approved = all(checks.values()) and bool(supported)
    reason = str(payload.get("note") or "").strip()[:500]
    if not approved and not reason:
        failed = [name for name, ok in checks.items() if not ok]
        reason = "Nieprzejście kontroli: " + ", ".join(failed or ["supported_source_ids"])
    return {
        "approved": approved,
        "reason": reason,
        "supported_source_ids": supported,
        "contradictions": fatal_issues,
        "fatal_checks": checks,
        "fatal_issues": fatal_issues,
        "review_policy": "integrity_v2_not_second_ranking",
        "provider": runtime.provider,
        "model": runtime.review_model,
    }
