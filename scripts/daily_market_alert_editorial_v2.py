#!/usr/bin/env python3
"""PL/EN-separated editorial engine and fail-closed quality gate."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import requests

try:
    from comment_quality import get_ai_runtime, request_json_completion
except ImportError:
    from scripts.comment_quality import get_ai_runtime, request_json_completion

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "data" / "investments" / "daily_market_alert_editorial_spec.json"
FIELDS = ("what_changed", "why_it_matters", "base_case")
IDS = ("sp500", "brent", "us10y")


class EditorialQualityError(RuntimeError):
    pass


def compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def load_spec() -> dict[str, Any]:
    try:
        spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise EditorialQualityError(f"Cannot load editorial spec: {exc}") from exc
    if spec.get("spec_version") != "3.0":
        raise EditorialQualityError("Unsupported editorial spec")
    return spec


def prompt(lang: str, spec: dict[str, Any]) -> str:
    cfg = spec["languages"][lang]
    limits = cfg["limits"]
    banned = json.dumps(cfg["banned_phrases"], ensure_ascii=False)
    if lang == "pl":
        return f"""
Jesteś redaktorem BriefRooms wyłącznie dla POLSKIEJ wersji alertu. Nie twórz angielskiego tekstu i nie tłumacz gotowej wersji EN.
Używaj tylko market_data i news_candidates. Nigdy nie wymyślaj wydarzenia, liczby, poziomu, źródła, decyzji instytucji ani pewnego związku przyczynowego. daily_change oznacza zmianę od poprzedniego regularnego zamknięcia.
Dla każdego instrumentu napisz trzy różne funkcjonalnie pola:
- what_changed ({limits['what_changed'][0]}–{limits['what_changed'][1]} znaków): cena i zmiana, położenie wobec wsparcia/oporu oraz tylko bezpośrednio potwierdzony nowy impuls.
- why_it_matters ({limits['why_it_matters'][0]}–{limits['why_it_matters'][1]} znaków): mechanizm właściwy dla klasy aktywów, bez powtarzania poprzedniego pola.
- base_case ({limits['base_case'][0]}–{limits['base_case'][1]} znaków): scenariusz na 1–3 sesje z dokładnym wsparciem i oporem jako warunkami zanegowania/potwierdzenia.
S&P 500: sentyment, rentowności, koszt kapitału i wyceny. Brent: podaż/popyt, premia za ryzyko, zapasy lub struktura ceny, wyłącznie gdy podparte. US 10Y: ścieżka stóp, inflacja, duration i wpływ na wyceny.
Jeżeli brak potwierdzonej wiadomości, opisz sprawdzalny układ ceny, poziomów i relacji między aktywami — bez pustej formuły o braku katalizatora.
source_indexes wybieraj tylko z news_candidates bezpośrednio wspierających zdanie. stance: positive|neutral|negative|mixed. Prawdopodobieństwa są wielokrotnościami 5, każde 10–70, suma 100. driver_keys: 1–4 krótkie klucze po angielsku.
Zakazane ogólniki: {banned}
Zwróć wyłącznie JSON:
{{"market_regime":"konkretna synteza co najmniej dwóch aktywów","summary":"jeden użyteczny wniosek","instruments":[{{"id":"sp500|brent|us10y","what_changed":"...","why_it_matters":"...","base_case":"...","stance":"mixed","driver_keys":["..."],"source_indexes":[],"probabilities":{{"range":45,"continuation":35,"reversal":20}}}}],"preclose_note":""}}
W trybie open preclose_note jest pusty. W preclose to jedno faktograficzne zdanie porównujące opening_context.
""".strip()
    return f"""
You are the BriefRooms editor for the ENGLISH alert only. Do not create Polish copy and do not translate a finished PL version.
Use only market_data and news_candidates. Never invent an event, number, level, source, institutional action or proven causal link. daily_change is always versus the previous regular-session close.
For each instrument produce three functionally distinct fields:
- what_changed ({limits['what_changed'][0]}–{limits['what_changed'][1]} characters): price/change, location versus support/resistance, and only a directly supported fresh driver.
- why_it_matters ({limits['why_it_matters'][0]}–{limits['why_it_matters'][1]} characters): asset-specific transmission mechanism, without repeating what_changed.
- base_case ({limits['base_case'][0]}–{limits['base_case'][1]} characters): 1–3 session base case using exact support and resistance as confirmation/invalidation conditions.
S&P 500: risk appetite, yields, cost of capital and valuations. Brent: supply/demand, risk premium, inventories or price structure only when supported. US 10Y: the rate path, inflation, duration and valuation transmission.
When no headline is verified, describe the observable price/level/cross-asset configuration without a hollow no-catalyst formula.
source_indexes may only point to directly supporting news_candidates. stance: positive|neutral|negative|mixed. Probabilities are multiples of 5, each 10–70, sum 100. driver_keys: 1–4 short English keys.
Banned generic phrases: {banned}
Return JSON only:
{{"market_regime":"specific synthesis of at least two assets","summary":"one decision-useful conclusion","instruments":[{{"id":"sp500|brent|us10y","what_changed":"...","why_it_matters":"...","base_case":"...","stance":"mixed","driver_keys":["..."],"source_indexes":[],"probabilities":{{"range":45,"continuation":35,"reversal":20}}}}],"preclose_note":""}}
In open mode preclose_note is empty. In preclose it is one factual sentence comparing opening_context.
""".strip()


def review_prompt(lang: str, spec: dict[str, Any]) -> str:
    banned = json.dumps(spec["languages"][lang]["banned_phrases"], ensure_ascii=False)
    return (
        "Jesteś niezależnym kontrolerem jakości polskiego alertu. " if lang == "pl" else
        "You are the independent quality reviewer for the English alert. "
    ) + (
        "Popraw draft wyłącznie na podstawie evidence. Usuń zmyślone fakty, niepodpartą przyczynowość, stare twierdzenia, powtórzenia, porady i ogólniki. "
        "Każde base_case musi zawierać dokładne wsparcie i opór z market_data, a why_it_matters mechanizm właściwy dla aktywa. "
        if lang == "pl" else
        "Correct the draft using evidence only. Remove invented facts, unsupported causality, stale claims, repetition, advice and filler. "
        "Every base_case must contain exact support and resistance from market_data, and why_it_matters must contain an asset-specific mechanism. "
    ) + f"Banned phrases: {banned}. Return the identical JSON structure and no commentary."


def generate_language(alert_module: Any, snapshots: list[Any], candidates: list[dict[str, Any]], mode: str, previous: dict[str, Any], lang: str, spec: dict[str, Any]) -> dict[str, Any]:
    runtime = get_ai_runtime()
    if not runtime.available:
        raise RuntimeError("No AI runtime available")
    evidence = alert_module.prompt_payload(snapshots, candidates, mode, previous)
    evidence["language"] = lang
    evidence["contract"] = {"data_read_only": True, "narrative_fields": list(FIELDS)}
    draft = request_json_completion(
        post=requests.post,
        runtime=runtime,
        messages=[{"role": "system", "content": prompt(lang, spec)}, {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)}],
        max_tokens=2200,
        temperature=0.12,
        timeout=60,
    )
    return request_json_completion(
        post=requests.post,
        runtime=runtime,
        messages=[{"role": "system", "content": review_prompt(lang, spec)}, {"role": "user", "content": json.dumps({"evidence": evidence, "draft": draft}, ensure_ascii=False)}],
        max_tokens=2200,
        temperature=0.0,
        review=True,
        timeout=60,
    )


def normalize_probabilities(value: Any) -> dict[str, int]:
    raw = value if isinstance(value, dict) else {}
    out: dict[str, int] = {}
    for key in ("range", "continuation", "reversal"):
        try:
            number = int(round(float(raw.get(key, 0)) / 5) * 5)
        except (TypeError, ValueError):
            number = 0
        out[key] = min(70, max(10, number))
    while sum(out.values()) < 100:
        out[min(out, key=out.get)] += 5
    while sum(out.values()) > 100:
        out[max(out, key=out.get)] -= 5
    return out


def rows_by_id(value: Any) -> dict[str, dict[str, Any]]:
    rows = value.get("instruments", []) if isinstance(value, dict) else []
    return {compact(row.get("id")): row for row in rows if isinstance(row, dict)}


def merge_languages(pl: dict[str, Any], en: dict[str, Any]) -> dict[str, Any]:
    pl_map, en_map = rows_by_id(pl), rows_by_id(en)
    if set(pl_map) != set(IDS) or set(en_map) != set(IDS):
        raise EditorialQualityError("Each language must contain exactly three instruments")
    merged = []
    for instrument_id in IDS:
        p, e = pl_map[instrument_id], en_map[instrument_id]
        stance = compact(p.get("stance")).lower()
        if stance != compact(e.get("stance")).lower():
            raise EditorialQualityError(f"PL/EN stance mismatch for {instrument_id}")
        narrative = {
            "pl": {field: compact(p.get(field)) for field in FIELDS},
            "en": {field: compact(e.get(field)) for field in FIELDS},
        }
        drivers: list[str] = []
        for value in list(p.get("driver_keys") or []) + list(e.get("driver_keys") or []):
            key = re.sub(r"[^a-z0-9-]+", "-", compact(value).lower()).strip("-")
            if key and key not in drivers:
                drivers.append(key)
        sources: list[int] = []
        for value in list(p.get("source_indexes") or []) + list(e.get("source_indexes") or []):
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if index not in sources:
                sources.append(index)
        avg = {
            key: (float((p.get("probabilities") or {}).get(key, 0)) + float((e.get("probabilities") or {}).get(key, 0))) / 2
            for key in ("range", "continuation", "reversal")
        }
        merged.append({
            "id": instrument_id,
            "narrative": narrative,
            "reason": {lang: " ".join(narrative[lang][field] for field in FIELDS) for lang in ("pl", "en")},
            "stance": stance,
            "driver_keys": drivers[:4],
            "source_indexes": sorted(sources),
            "probabilities": normalize_probabilities(avg),
        })
    return {
        "market_regime": {"pl": compact(pl.get("market_regime")), "en": compact(en.get("market_regime"))},
        "summary": {"pl": compact(pl.get("summary")), "en": compact(en.get("summary"))},
        "instruments": merged,
        "preclose_note": {"pl": compact(pl.get("preclose_note")), "en": compact(en.get("preclose_note"))},
    }


def word_set(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-ząćęłńóśźż0-9]+", text.lower()) if len(token) >= 4}


def similarity(a: str, b: str) -> float:
    left, right = word_set(a), word_set(b)
    return len(left & right) / max(1, len(left | right))


def validate_copy(editorial: dict[str, Any], snapshots: list[Any], candidates: list[dict[str, Any]], spec: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    snap = {item.instrument_id: item for item in snapshots}
    candidate_map = {int(item["index"]): item for item in candidates}
    for lang in ("pl", "en"):
        cfg = spec["languages"][lang]
        regime = compact((editorial.get("market_regime") or {}).get(lang))
        lo, hi = cfg["limits"]["market_regime"]
        if not lo <= len(regime) <= hi:
            issues.append(f"{lang}:market_regime_length")
        if sum(name.lower() in regime.lower() for name in ("S&P", "Brent", "US 10Y")) < 2 and len(re.findall(r"\d", regime)) < 2:
            issues.append(f"{lang}:market_regime_not_cross_asset")
        texts: list[tuple[str, str]] = []
        for row in editorial.get("instruments", []):
            instrument_id = row["id"]
            narrative = row["narrative"][lang]
            combined = " ".join(narrative[field] for field in FIELDS)
            texts.append((instrument_id, combined))
            for field in FIELDS:
                text = compact(narrative.get(field))
                lower, upper = cfg["limits"][field]
                if not lower <= len(text) <= upper:
                    issues.append(f"{lang}:{instrument_id}:{field}_length")
                if any(phrase.lower() in text.lower() for phrase in cfg["banned_phrases"]):
                    issues.append(f"{lang}:{instrument_id}:{field}_generic")
            if not re.search(r"\d", narrative["what_changed"]):
                issues.append(f"{lang}:{instrument_id}:what_changed_without_number")
            base_digits = re.sub(r"[^0-9]", "", narrative["base_case"])
            for level in (snap[instrument_id].support_text, snap[instrument_id].resistance_text):
                if re.sub(r"[^0-9]", "", level) not in base_digits:
                    issues.append(f"{lang}:{instrument_id}:base_case_missing_exact_levels")
                    break
            vocabulary = [value.lower() for value in cfg["asset_vocabulary"][instrument_id]]
            if not any(value in narrative["why_it_matters"].lower() for value in vocabulary):
                issues.append(f"{lang}:{instrument_id}:missing_asset_mechanism")
            indexes = row.get("source_indexes", [])
            if any(index not in candidate_map for index in indexes):
                issues.append(f"{lang}:{instrument_id}:invalid_source_index")
            if any(instrument_id not in (candidate_map.get(index, {}).get("instrument_ids") or []) for index in indexes):
                issues.append(f"{lang}:{instrument_id}:irrelevant_source_index")
            if not indexes and any(term.lower() in combined.lower() for term in cfg["unsupported_without_source"]):
                issues.append(f"{lang}:{instrument_id}:unsupported_named_catalyst")
            if row.get("stance") not in {"positive", "neutral", "negative", "mixed"}:
                issues.append(f"{lang}:{instrument_id}:invalid_stance")
            if not row.get("driver_keys"):
                issues.append(f"{lang}:{instrument_id}:missing_driver_keys")
        for index, (left_id, left) in enumerate(texts):
            for right_id, right in texts[index + 1:]:
                if similarity(left, right) > spec["max_cross_instrument_similarity"]:
                    issues.append(f"{lang}:{left_id}:{right_id}:template_similarity")
    return sorted(set(issues))


def report(editorial: dict[str, Any], snapshots: list[Any], candidates: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    issues = validate_copy(editorial, snapshots, candidates, spec)
    score = max(0, 100 - 8 * len(issues))
    return {
        "spec_version": spec["spec_version"],
        "prompt_version": spec["prompt_version"],
        "separate_language_generation": True,
        "passed": not issues and score >= spec["minimum_score"],
        "score": score,
        "issues": issues,
    }


def generate_editorial(alert_module: Any, snapshots: list[Any], candidates: list[dict[str, Any]], mode: str, previous: dict[str, Any]) -> dict[str, Any]:
    spec = load_spec()
    merged = merge_languages(
        generate_language(alert_module, snapshots, candidates, mode, previous, "pl", spec),
        generate_language(alert_module, snapshots, candidates, mode, previous, "en", spec),
    )
    quality = report(merged, snapshots, candidates, spec)
    if not quality["passed"]:
        raise EditorialQualityError("AI editorial blocked: " + ", ".join(quality["issues"]))
    merged["quality_report"] = quality
    return merged


def direction_word(lang: str, direction: str) -> str:
    return {
        "pl": {"up": "rośnie", "down": "spada", "flat": "jest stabilny"},
        "en": {"up": "is higher", "down": "is lower", "flat": "is broadly unchanged"},
    }[lang][direction]


def deterministic_regime(snapshots: list[Any]) -> dict[str, str]:
    by_id = {item.instrument_id: item for item in snapshots}
    sp, oil, rates = by_id["sp500"], by_id["brent"], by_id["us10y"]
    if sp.direction == "up" and rates.direction == "up":
        return {
            "pl": f"S&P 500 rośnie, lecz wzrost US 10Y do {rates.price_text} ogranicza siłę sygnału risk-on; Brent {direction_word('pl', oil.direction)}.",
            "en": f"The S&P 500 is higher, but a rise in US 10Y to {rates.price_text.replace(',', '.')} tempers the risk-on signal; Brent {direction_word('en', oil.direction)}.",
        }
    return {
        "pl": f"S&P 500 {direction_word('pl', sp.direction)}, Brent {direction_word('pl', oil.direction)}, a US 10Y {direction_word('pl', rates.direction)} — obraz międzyrynkowy pozostaje mieszany.",
        "en": f"The S&P 500 {direction_word('en', sp.direction)}, Brent {direction_word('en', oil.direction)} and US 10Y {direction_word('en', rates.direction)}, leaving a mixed cross-asset picture.",
    }


def deterministic_narrative(snapshot: Any, lang: str, snapshots: list[Any]) -> dict[str, str]:
    by_id = {item.instrument_id: item for item in snapshots}
    dot = lambda value: value.replace(",", ".")
    if snapshot.instrument_id == "sp500":
        if lang == "pl":
            return {
                "what_changed": f"S&P 500 jest na {snapshot.price_text}, czyli {snapshot.change_text} od poprzedniego zamknięcia, i handluje między wsparciem {snapshot.support_text} a oporem {snapshot.resistance_text}.",
                "why_it_matters": f"Położenie nad wsparciem podtrzymuje popyt na akcje, lecz rentowność US 10Y na {by_id['us10y'].price_text} wpływa na koszt kapitału i może ograniczać wyceny spółek wzrostowych.",
                "base_case": f"Bazowo zakładamy konsolidację: zamknięcie ponad {snapshot.resistance_text} potwierdzi ruch do {snapshot.next_resistance_text}, a zejście poniżej {snapshot.support_text} zwiększy ryzyko spadku do {snapshot.next_support_text}.",
            }
        return {
            "what_changed": f"The S&P 500 is at {dot(snapshot.price_text)}, {dot(snapshot.change_text)} from the previous close, trading between {dot(snapshot.support_text)} support and {dot(snapshot.resistance_text)} resistance.",
            "why_it_matters": f"Holding above support preserves equity demand, while the US 10Y yield at {dot(by_id['us10y'].price_text)} affects the cost of capital and can restrain growth-stock valuations.",
            "base_case": f"The base case is consolidation: a close above {dot(snapshot.resistance_text)} confirms room to {dot(snapshot.next_resistance_text)}, while a break below {dot(snapshot.support_text)} raises downside risk toward {dot(snapshot.next_support_text)}.",
        }
    if snapshot.instrument_id == "brent":
        if lang == "pl":
            return {
                "what_changed": f"Brent kosztuje {snapshot.price_text} i zmienia się o {snapshot.change_text} od poprzedniego zamknięcia, pozostając w przedziale {snapshot.support_text}–{snapshot.resistance_text}.",
                "why_it_matters": "Bez bezpośrednio potwierdzonej wiadomości przewagę popytu lub podaży najlepiej oceniać przez utrzymanie ceny wobec granic zakresu i zmianę premii za ryzyko w ropie.",
                "base_case": f"Bazowo obowiązuje zakres: wybicie ponad {snapshot.resistance_text} otworzy drogę do {snapshot.next_resistance_text}, a zamknięcie poniżej {snapshot.support_text} skieruje uwagę na {snapshot.next_support_text}.",
            }
        return {
            "what_changed": f"Brent is at {dot(snapshot.price_text)} and {dot(snapshot.change_text)} from the previous close, remaining inside the {dot(snapshot.support_text)}–{dot(snapshot.resistance_text)} range.",
            "why_it_matters": "Without a directly verified headline, the balance of oil demand, supply and risk premium is best judged by whether price holds or breaks the range boundaries.",
            "base_case": f"The base case is range trading: a break above {dot(snapshot.resistance_text)} opens room to {dot(snapshot.next_resistance_text)}, while a close below {dot(snapshot.support_text)} shifts focus to {dot(snapshot.next_support_text)}.",
        }
    if lang == "pl":
        return {
            "what_changed": f"Rentowność US 10Y wynosi {snapshot.price_text}, zmieniając się o {snapshot.change_text} od poprzedniego zamknięcia, i testuje zakres {snapshot.support_text}–{snapshot.resistance_text}.",
            "why_it_matters": "Wyższa rentowność podnosi stopę dyskontową i obciąża wyceny aktywów o długim duration; niższa zmniejsza presję na obligacje oraz segment wzrostowy rynku akcji.",
            "base_case": f"Bazowo trwa test zakresu: wybicie ponad {snapshot.resistance_text} zwiększy presję w kierunku {snapshot.next_resistance_text}, a spadek poniżej {snapshot.support_text} otworzy przestrzeń do {snapshot.next_support_text}.",
        }
    return {
        "what_changed": f"The US 10Y yield is {dot(snapshot.price_text)}, {snapshot.change_text} from the previous close, and is testing the {dot(snapshot.support_text)}–{dot(snapshot.resistance_text)} range.",
        "why_it_matters": "A higher yield lifts the discount rate and pressures long-duration valuations; a lower yield eases the burden on bonds and growth-sensitive equity segments.",
        "base_case": f"The base case is a range test: a break above {dot(snapshot.resistance_text)} increases pressure toward {dot(snapshot.next_resistance_text)}, while a move below {dot(snapshot.support_text)} opens room to {dot(snapshot.next_support_text)}.",
    }


def deterministic_editorial(alert_module: Any, snapshots: list[Any], mode: str) -> dict[str, Any]:
    spec = load_spec()
    rows = []
    for snapshot in snapshots:
        narrative = {lang: deterministic_narrative(snapshot, lang, snapshots) for lang in ("pl", "en")}
        rows.append({
            "id": snapshot.instrument_id,
            "narrative": narrative,
            "reason": {lang: " ".join(narrative[lang][field] for field in FIELDS) for lang in ("pl", "en")},
            "stance": "neutral" if snapshot.direction == "flat" else ("mixed" if snapshot.instrument_id == "us10y" else ("positive" if snapshot.direction == "up" else "negative")),
            "driver_keys": {
                "sp500": ["equity-price-action", "rates-valuation"],
                "brent": ["oil-price-action", "risk-premium"],
                "us10y": ["rates-price-action", "duration-valuation"],
            }[snapshot.instrument_id],
            "source_indexes": [],
            "probabilities": {"range": 60, "continuation": 20, "reversal": 20} if snapshot.direction == "flat" else {"range": 45, "continuation": 35, "reversal": 20},
        })
    regime = deterministic_regime(snapshots)
    editorial = {
        "market_regime": regime,
        "summary": regime,
        "instruments": rows,
        "preclose_note": {"pl": "Aktualizacja przed zamknięciem opiera się wyłącznie na zweryfikowanych notowaniach i zmianie poziomów.", "en": "The pre-close update is based only on validated quotes and level changes."} if mode == "preclose" else {"pl": "", "en": ""},
    }
    quality = report(editorial, snapshots, [], spec)
    if not quality["passed"]:
        raise EditorialQualityError("Deterministic editorial blocked: " + ", ".join(quality["issues"]))
    editorial["quality_report"] = quality
    return editorial


def enrich_payload(payload: dict[str, Any], editorial: dict[str, Any]) -> dict[str, Any]:
    by_id = {row["id"]: row for row in editorial.get("instruments", [])}
    for item in payload.get("instruments", []):
        row = by_id.get(item.get("id"), {})
        item["narrative"] = row.get("narrative", {})
        item["stance"] = row.get("stance")
    payload["editorial_quality"] = editorial.get("quality_report", {})
    payload["editorial_contract"] = {
        "data_layer": "validated_market_snapshot",
        "narrative_layer": "language_separated_editorial_v3",
        "pl_generated_independently": True,
        "en_generated_independently": True,
    }
    return payload


def validate_published_payload(payload: dict[str, Any]) -> None:
    spec = load_spec()
    quality = payload.get("editorial_quality") or {}
    if quality.get("spec_version") != spec["spec_version"] or quality.get("prompt_version") != spec["prompt_version"]:
        raise ValueError("Missing current editorial quality contract")
    if not quality.get("passed") or int(quality.get("score", 0)) < spec["minimum_score"] or quality.get("issues"):
        raise ValueError("Editorial quality gate did not pass")
    contract = payload.get("editorial_contract") or {}
    if not contract.get("pl_generated_independently") or not contract.get("en_generated_independently"):
        raise ValueError("PL and EN must be generated independently")
    for item in payload.get("instruments", []):
        for lang in ("pl", "en"):
            narrative = (item.get("narrative") or {}).get(lang) or {}
            for field in FIELDS:
                text = compact(narrative.get(field))
                if not text:
                    raise ValueError(f"Missing {lang} {field} for {item.get('id')}")
                if any(phrase.lower() in text.lower() for phrase in spec["languages"][lang]["banned_phrases"]):
                    raise ValueError(f"Generic {lang} copy blocked for {item.get('id')}")
