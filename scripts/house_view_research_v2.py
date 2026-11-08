#!/usr/bin/env python3
"""House View v2: evidence-bound bilingual research synthesis, never a trading controller.

Consumes the existing monthly macro snapshot and read-only public BRs projections.
No language-model text is generated at publication time: every published conclusion
is deterministic, traceable to an observed input, and explicit about missing data.
Private Belief bridges, FSE hypotheses and BRACE challengers cannot vote as an
authorized directional forecast. This module NEVER writes to engine state.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time, timezone
from pathlib import Path
from urllib.parse import urlparse

ENGINE_FILES = {
    "GSE": "data/gse/gse_v2_lab_public.json",
    "FSE": "data/investments/fse_public.json",
    "BRACE": "data/public/brace_spx_platform_public.json",
}
FUNDAMENTALS = "data/investments/house_view_fundamentals.json"
MAX_AGES = {"GSE": 3, "FSE": 7, "BRACE": 14, "FUNDAMENTALS": 45}
LABELS = {
    "pl": {"defensive": "defensywny", "neutral": "neutralny",
           "mild_positive": "lekko pozytywny", "unassessed": "bez oceny"},
    "en": {"defensive": "defensive", "neutral": "neutral",
           "mild_positive": "mildly positive", "unassessed": "unassessed"},
}


def _number(value, minimum=None, maximum=None):
    if isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or (minimum is not None and x < minimum) or (maximum is not None and x > maximum):
        return None
    return x


def _timestamp(value):
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return out.astimezone(timezone.utc) if out.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def _read_public(data_dir: Path, relative: str, today: date, days: int) -> tuple[dict | None, dict]:
    """Record exact source hash and age; stale/malformed files cannot enter synthesis."""
    root = data_dir.parents[1]
    path = root / relative
    lineage = {"id": relative, "source": "/" + relative, "role": "read_only_context",
               "status": "unavailable", "sha256": None, "observed_at": None}
    try:
        raw = path.read_bytes()
        doc = json.loads(raw)
        if not isinstance(doc, dict):
            return None, lineage
        lineage["sha256"] = hashlib.sha256(raw).hexdigest()
        stamp = _timestamp(doc.get("generated_at") or doc.get("source_published_at"))
        if not stamp:
            lineage["status"] = "missing_timestamp"
            return None, lineage
        lineage["observed_at"] = stamp.isoformat()
        age = (datetime.combine(today, time.max, tzinfo=timezone.utc) - stamp).total_seconds()
        if age < 0 or age > (days + 1) * 86400:
            lineage["status"] = "stale_or_future"
            return None, lineage
        lineage["status"] = "fresh"
        return doc, lineage
    except (OSError, ValueError, TypeError):
        lineage["status"] = "missing_or_invalid"
        return None, lineage


def _add_section(sections: list, name: str, pl: str, en: str, refs: list[str]) -> None:
    if not (pl.strip() and en.strip()):
        raise ValueError("Empty bilingual research section: " + name)
    sections.append({"id": name, "pl": pl, "en": en, "source_ids": sorted(set(refs))})


def _engine_context(today: date, data_dir: Path, weekly: dict | None) -> tuple[list, list]:
    """All engine readings are *descriptive*, never averaged into macro probability."""
    engines, evidence = [], []
    for name, relative in ENGINE_FILES.items():
        doc, lineage = _read_public(data_dir, relative, today, MAX_AGES[name])
        lineage["id"] = name
        evidence.append(lineage)
        if doc is None:
            engines.append({"id": name, "status": "unavailable", "as_of": lineage["observed_at"],
                            "source_id": name, "directional_vote": None,
                            "pl": "Brak świeżego, zweryfikowanego odczytu; nie wnosimy kierunku.",
                            "en": "No fresh verified reading; no directional vote."})
            continue
        if name == "GSE":
            gse = (doc.get("spx_long_view") or {}).get("primary_horizon") or {}
            meta = doc.get("spx_long_view") or {}
            forecast_at = _timestamp(gse.get("forecast_at"))
            prob = _number(gse.get("probability"), 0, 1)
            clusters = _number(gse.get("effective_cluster_n"), 0, 100000)
            qualified = (meta.get("asset") == "SPX" and meta.get("research_only") is True
                         and meta.get("decision_influence") is False
                         and gse.get("horizon_hours") == 720 and gse.get("freshness") == "fresh"
                         and forecast_at is not None
                         and 0 <= (datetime.combine(today, time.max, tzinfo=timezone.utc) - forecast_at).total_seconds() <= 4 * 86400
                         and prob is not None and clusters is not None and clusters >= 10
                         and gse.get("direction") in ("UP", "DOWN"))
            if not qualified:
                engines.append({"id": name, "status": "unqualified", "as_of": lineage["observed_at"],
                                "source_id": name, "directional_vote": None,
                                "pl": "Odczyt GSE nie spełnia kontraktu jakości dla SPX/30 dni.",
                                "en": "GSE reading does not meet the SPX/30-day quality contract."})
            else:
                # This is a research risk flag, NOT a calibrated probability or trading vote.
                flag = "geopolitical_caution" if gse["direction"] == "DOWN" and prob >= .60 else None
                engines.append({"id": name, "status": "research_only", "as_of": forecast_at.isoformat(),
                                "source_id": name, "directional_vote": None, "risk_flag": flag,
                                "reported_direction": gse["direction"], "reported_probability": round(prob, 4),
                                "pl": f"GSE: {gse['direction']} w 30 dni, raportowany odczyt {prob * 100:.1f}%; research-only, bez automatycznej interpretacji jako prawdopodobieństwo spadku indeksu.",
                                "en": f"GSE: {gse['direction']} over 30 days, reported reading {prob * 100:.1f}%; research-only, not automatically a calibrated probability of index decline."})
        elif name == "FSE":
            spx = next((x for x in doc.get("instruments", []) if isinstance(x, dict) and x.get("instrument") == "SPX"), None)
            risk = _number((spx or {}).get("risk_score"), 0, 1)
            observed = _timestamp((spx or {}).get("observed_at"))
            acceptable = risk is not None and observed is not None and (
                0 <= (datetime.combine(today, time.max, tzinfo=timezone.utc) - observed).total_seconds() <= 8 * 86400
            ) and (doc.get("authority") or {}).get("trade_execution") is False
            if not acceptable:
                engines.append({"id": name, "status": "unqualified", "as_of": lineage["observed_at"],
                                "source_id": name, "directional_vote": None,
                                "pl": "Brak kwalifikowanego odczytu struktury SPX.", "en": "No qualified SPX structure reading."})
            else:
                regime = str(spx.get("regime") or "UNKNOWN")[:32]
                # FSE directional memory may ABSTAIN; never convert it to a 50/50 opinion.
                memory = spx.get("fractal_memory") or {}
                abstain = memory.get("available") is not True or memory.get("status") == "ABSTAIN"
                caution = risk >= .65 or regime == "TURBULENT"
                engines.append({"id": name, "status": "research_only", "as_of": observed.isoformat(),
                                "source_id": name, "directional_vote": None,
                                "risk_flag": "structural_caution" if caution else None,
                                "risk_score": round(risk, 4), "regime": regime,
                                "directional_memory": "ABSTAIN" if abstain else "research_only",
                                "pl": f"FSE: reżim {regime}, ryzyko struktury {risk:.2f}/1; {'pamięć kierunkowa ABSTAIN' if abstain else 'pamięć kierunkowa tylko badawcza'}. Nie jest to prognoza 6M/12M.",
                                "en": f"FSE: {regime} regime, structural risk {risk:.2f}/1; {'directional memory ABSTAIN' if abstain else 'directional memory is research-only'}. Not a 6M/12M forecast."})
        else:
            frozen = doc.get("frozen_track") or {}
            gate = doc.get("promotion_gate") or {}
            allowed = bool(frozen.get("single_champion_authorized") is True and
                           frozen.get("strict_gate_passed") is True)
            label = str(gate.get("status") or "UNKNOWN")[:40]
            engines.append({"id": name, "status": "research_only", "as_of": lineage["observed_at"],
                            "source_id": name, "directional_vote": None,
                            "champion_authorized": allowed, "promotion_gate": label,
                            "pl": f"BRACE-SPX: bramka {label}; {'status championa wymaga osobnej interpretacji' if allowed else 'brak autoryzowanego pojedynczego kierunku'}. Challengery nie głosują w House View.",
                            "en": f"BRACE-SPX: {label} gate; {'champion status requires separate interpretation' if allowed else 'no authorized single direction'}. Challengers do not vote in House View."})
    wes = {"id": "WES", "status": "unavailable", "as_of": None, "source_id": "WES",
           "directional_vote": None, "pl": "Brak świeżego tygodniowego SPX.",
           "en": "No fresh weekly SPX observation."}
    if weekly:
        relative = weekly.get("source", "").removeprefix("/")
        try:
            raw = (data_dir.parents[1] / relative).read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
        except OSError:
            digest = None
        if digest and relative.startswith("data/investments/weekly/") and relative.endswith(".json"):
            weekly_asof = weekly.get("forecast_frozen_at")
            wes = {"id": "WES", "status": "research_only", "as_of": weekly_asof,
                   "source_id": "WES", "directional_vote": None,
                   "ret20_pct": weekly.get("ret20_pct"),
                   "pl": f"WES: zamrożona zmiana SPX futures za 20 sesji {weekly['ret20_pct']:+.2f}%. To obserwacja momentum, nie transakcyjny kierunek WES ani świeży tick.",
                   "en": f"WES: frozen SPX futures 20-session change {weekly['ret20_pct']:+.2f}%. Momentum observation, not a WES trade direction or live quote."}
            evidence.append({"id": "WES", "source": "/" + relative, "role": "frozen_weekly_context",
                             "status": "fresh", "sha256": digest,
                             "observed_at": weekly_asof})
    if wes["status"] == "unavailable":
        evidence.append({"id": "WES", "source": None, "role": "frozen_weekly_context",
                         "status": "unavailable", "sha256": None, "observed_at": None})
    engines.append(wes)
    engines.append({"id": "BELIEF", "status": "not_publicly_available", "as_of": None,
                    "source_id": None, "directional_vote": None,
                    "pl": "Nie ma kwalifikowanego publicznego snapshotu pięciu przekonań SPX. Prywatne mosty Belief nie są rekonstruowane ani odczytywane bez kontraktu.",
                    "en": "No qualified public five-belief SPX snapshot. Private Belief bridges are neither reconstructed nor consumed without a contract."})
    return engines, evidence


def _fundamental_context(today: date, data_dir: Path) -> tuple[dict, dict]:
    raw, record = _read_public(data_dir, FUNDAMENTALS, today, MAX_AGES["FUNDAMENTALS"])
    record["id"] = "FUNDAMENTALS"
    record["role"] = "optional_curated_primary_document"
    if raw is None:
        return {"status": "unavailable", "forward_pe": None, "forward_eps_growth_pct": None}, record
    url = str(raw.get("source_document_url") or "")
    host = (urlparse(url).hostname or "").lower()
    published = _timestamp(raw.get("source_published_at"))
    pe = _number(raw.get("forward_pe"), 3, 100)
    eps = _number(raw.get("forward_eps_growth_pct"), -80, 100)
    allowed = {"insight.factset.com", "www.spglobal.com", "www.yardeni.com"}
    if (raw.get("schema_version") != "1.0" or not url.startswith("https://")
            or host not in allowed or published is None or pe is None or eps is None):
        record["status"] = "invalid_curated_source"
        return {"status": "unavailable", "forward_pe": None, "forward_eps_growth_pct": None}, record
    observed = date.fromisoformat(published.date().isoformat())
    if not 0 <= (today - observed).days <= 45:
        record["status"] = "stale_or_future"
        return {"status": "unavailable", "forward_pe": None, "forward_eps_growth_pct": None}, record
    record["status"] = "fresh"
    record["document_url"] = url
    return {"status": "curated_not_live", "forward_pe": round(pe, 2),
            "forward_eps_growth_pct": round(eps, 2), "source_document_url": url,
            "source_published_at": published.isoformat()}, record


def enrich_monthly_snapshot(snapshot: dict, today: date, data_dir: Path, previous: dict | None) -> dict:
    """Enrich a checked v1 macro computation with a complete source-limited House View."""
    if snapshot.get("schema_version") != "1.0" or snapshot.get("month") != today.strftime("%Y-%m"):
        raise ValueError("House View v2 requires same-month validated macro baseline")
    inputs = snapshot["inputs"]
    views = snapshot["views"]
    engines, lineage = _engine_context(today, data_dir, snapshot.get("weekly_market_observation"))
    fundamentals, fundamental_lineage = _fundamental_context(today, data_dir)
    lineage.append(fundamental_lineage)
    for row in snapshot["indicators"]:
        lineage.append({"id": row["id"], "source": row["source_url"], "role": "macro_observation",
                        "status": "fresh", "sha256": None,
                        "observed_at": row["observation_date"], "value": row["observed_value"]})
    ids = {r["id"] for r in lineage}
    gdp, core, ten, brent = (inputs[x] for x in
                             ("gdp_qoq_saar_pct", "core_pce_yoy_pct", "us_10y_pct", "brent_usd_bbl"))
    headline = inputs["pce_yoy_pct"]
    bias = lambda horizon, lang: LABELS[lang][views[horizon]["bias"]]
    engine_flags = [x["risk_flag"] for x in engines if x.get("risk_flag")]
    has_caution = bool(engine_flags)
    market = inputs.get("spx_futures_20d_return_pct")
    sections = []
    _add_section(sections, "thesis",
        f"Bieżący bilans ryzyka: 1M {bias('1M', 'pl')}, 6M {bias('6M', 'pl')}, 12M {bias('12M', 'pl')}. Są to odrębne, warunkowe horyzonty. "
        f"Wzrost PKB {gdp:+.2f}% SAAR oraz inflacja bazowa PCE {core:.2f}% r/r wyznaczają tło; "
        + ("świeże silniki sygnalizują dodatkowe ryzyka, ale nie zastępują fundamentów." if has_caution else
           "brak kwalifikowanego silnego alarmu nie oznacza braku ryzyka."),
        f"Current risk balance: 1M {bias('1M', 'en')}, 6M {bias('6M', 'en')}, 12M {bias('12M', 'en')}. These are separate conditional horizons. "
        f"Real GDP {gdp:+.2f}% SAAR and core PCE {core:.2f}% y/y anchor the macro context; "
        + ("fresh models raise contextual risk flags but do not replace fundamentals." if has_caution else
           "lack of a qualified high-risk flag does not imply absence of risk."),
        ["GDPC1", "PCEPILFE"] + [x["id"] for x in engines if x.get("risk_flag")])
    _add_section(sections, "growth_and_inflation",
        f"PKB realny: {gdp:+.2f}% q/q SAAR. Inflacja PCE: {headline:.2f}% r/r, bazowa: {core:.2f}% r/r. "
        + ("Wzrost poniżej 1% ogranicza przestrzeń dla tezy o trwałym przyspieszeniu." if gdp < 1 else
           "Dodatni wzrost nie eliminuje ryzyka rewizji danych i marż."),
        f"Real GDP: {gdp:+.2f}% q/q SAAR. Headline PCE: {headline:.2f}% y/y, core: {core:.2f}% y/y. "
        + ("Growth below 1% weakens the case for durable acceleration." if gdp < 1 else
           "Positive growth does not rule out data revisions or margin pressure."),
        ["GDPC1", "PCEPI", "PCEPILFE"])
    _add_section(sections, "rates_and_energy",
        f"Rentowność amerykańskich obligacji 10Y: {ten:.2f}%; Brent: {brent:.2f} USD/bbl. "
        + ("Droga ropa podnosi ryzyko kosztowe i inflacyjne." if brent >= 110 else
           "Cena ropy pozostaje obserwowanym czynnikiem kosztów i inflacji."),
        f"U.S. 10-year Treasury yield: {ten:.2f}%; Brent: USD {brent:.2f}/bbl. "
        + ("High oil prices raise cost and inflation risks." if brent >= 110 else
           "Oil remains an observable input to costs and inflation."),
        ["DGS10", "DCOILBRENTEU"])
    frefs = ["FUNDAMENTALS"] if fundamentals["status"] == "curated_not_live" else []
    if fundamentals["status"] == "curated_not_live":
        _add_section(sections, "earnings_and_valuation",
            f"Ostatni ręcznie udokumentowany forward P/E: {fundamentals['forward_pe']:.2f}x, prognozowany wzrost EPS: {fundamentals['forward_eps_growth_pct']:+.2f}% "
            f"(data dokumentu {fundamentals['source_published_at'][:10]}). To snapshot źródła, nie automatyczny konsensus rynkowy i nie target S&P 500.",
            f"Last manually sourced forward P/E: {fundamentals['forward_pe']:.2f}x, estimated EPS growth: {fundamentals['forward_eps_growth_pct']:+.2f}% "
            f"(document dated {fundamentals['source_published_at'][:10]}). A curated source snapshot, not a live consensus or S&P 500 price target.", frefs)
    else:
        _add_section(sections, "earnings_and_valuation",
            "Brak aktualnych, potwierdzonych danych indeksowych o forward EPS, rewizjach zysków i forward P/E. "
            "Nie przypisujemy liczbowego targetu ani silnej tezy fundamentalnej na 6M/12M.",
            "Current index-level forward EPS, earnings revisions and forward P/E are not verified. "
            "No numerical price target or strong fundamental 6M/12M claim is published.", frefs)
    momentum_ref = ["WES"] if market is not None and "WES" in ids else []
    _add_section(sections, "tactical_and_models",
        (f"Zamrożone momentum SPX futures 20 sesji: {market:+.2f}%." if market is not None else
         "Brak kwalifikowanego zamrożonego momentum SPX; kierunek 1M pozostaje bez oceny.")
        + " GSE, FSE i BRACE są odczytywane wyłącznie informacyjnie. "
        + ("Zidentyfikowano flagi ryzyka: " + ", ".join(engine_flags) + "." if has_caution
           else "Nie ma dodatkowego autoryzowanego głosu kierunkowego z tych silników."),
        (f"Frozen SPX futures 20-session momentum: {market:+.2f}%." if market is not None else
         "No qualified frozen SPX momentum; the 1M direction remains unassessed.")
        + " GSE, FSE and BRACE are informational only. "
        + ("Contextual risk flags: " + ", ".join(engine_flags) + "." if has_caution
           else "These engines add no authorized directional vote."),
        momentum_ref + [e["id"] for e in engines if e["id"] in ("GSE", "FSE", "BRACE") and e["status"] == "research_only"])
    conflicts = []
    gse = next(e for e in engines if e["id"] == "GSE")
    if gse.get("risk_flag") == "geopolitical_caution" and market is not None and market >= 3:
        conflicts.append("gse_vs_positive_momentum")
    if "structural_caution" in engine_flags and views["6M"]["bias"] == "mild_positive":
        conflicts.append("positive_macro_vs_structural_risk")
    _add_section(sections, "disagreements",
        ("Konflikty: " + ", ".join(conflicts) + ". Nie uśredniamy horyzontów ani skorelowanych źródeł."
         if conflicts else "Brak zidentyfikowanego konfliktu według jawnych progów. Brak konfliktu nie jest potwierdzeniem trafności.")
        + " Stan Belief Core bez odpowiedniego publicznego punktowego kontraktu pozostaje poza oceną.",
        ("Conflicts: " + ", ".join(conflicts) + ". We do not average horizons or correlated sources."
         if conflicts else "No conflict detected under disclosed thresholds. Absence of conflict is not validation.")
        + " Belief Core is excluded without a qualified point-in-time public contract.",
        ["GSE", "FSE", "WES"] if conflicts else [])
    scenarios = [
        {"id": "base", "probability": None,
         "pl": "Scenariusz bazowy: bieżący bilans 6M pozostaje warunkowy; decydują kolejne odczyty inflacji, wzrostu i rentowności.",
         "en": "Base scenario: the 6M balance remains conditional; next inflation, growth and yield releases matter.",
         "watch_ids": ["GDPC1", "PCEPILFE", "DGS10"]},
        {"id": "downside", "probability": None,
         "pl": "Scenariusz spadkowy: utrzymywanie się wysokiej inflacji bazowej, wzrost rentowności lub ponowny szok energetyczny zwiększają presję na wyceny.",
         "en": "Downside scenario: persistent core inflation, higher yields or a renewed energy shock increase valuation pressure.",
         "watch_ids": ["PCEPILFE", "DGS10", "DCOILBRENTEU"]},
        {"id": "upside", "probability": None,
         "pl": "Scenariusz wzrostowy: dezinflacja bez spadku aktywności i stabilizacja kosztu kapitału poprawiłyby warunki; wymaga to potwierdzenia w zyskach spółek.",
         "en": "Upside scenario: disinflation without growth deterioration and stabilizing funding costs would help, subject to earnings confirmation.",
         "watch_ids": ["GDPC1", "PCEPILFE", "DGS10"]},
    ]
    gates = [
        {"id": "core_pce", "source_id": "PCEPILFE", "threshold": 3.5,
         "pl": "Core PCE >=3,5% r/r: presja inflacyjna podnosi defensywność.",
         "en": "Core PCE >=3.5% y/y: inflation pressure raises defensiveness."},
        {"id": "gdp", "source_id": "GDPC1", "threshold": 1.0,
         "pl": "PKB <1% q/q SAAR: rewizja optymizmu wzrostowego.",
         "en": "GDP <1% q/q SAAR: reassess growth optimism."},
        {"id": "brent", "source_id": "DCOILBRENTEU", "threshold": 110.0,
         "pl": "Brent >=110 USD/bbl: ponowna ocena presji kosztowej.",
         "en": "Brent >=USD 110/bbl: reassess cost pressure."},
        {"id": "ten_year", "source_id": "DGS10", "threshold": 5.0,
         "pl": "UST 10Y >=5%: wyższy dyskont dla przyszłych przepływów.",
         "en": "UST 10Y >=5%: higher discounting of future cash flows."},
    ]
    changes = []
    if isinstance(previous, dict) and previous.get("month", "") < snapshot["month"]:
        prior_inputs = previous.get("inputs") or {}
        for code, key in (("GDPC1", "gdp_qoq_saar_pct"), ("PCEPILFE", "core_pce_yoy_pct"),
                          ("DGS10", "us_10y_pct"), ("DCOILBRENTEU", "brent_usd_bbl")):
            was = _number(prior_inputs.get(key))
            now = _number(inputs.get(key))
            if was is not None and now is not None and abs(now - was) > .0001:
                changes.append({"source_id": code, "previous": was, "current": now, "delta": round(now - was, 4)})
    status = ("contextual_risk_flags" if has_caution else "no_strong_qualified_model_flags")
    snapshot["schema_version"] = "2.0"
    snapshot["method"] = "house_view_intelligence_v2_research_only"
    snapshot["scope"] = "sourced_macro_scenarios_and_read_only_engine_context_no_price_target"
    snapshot["research"] = {
        "version": "2.0", "thesis_status": "conditional_research_not_validated_forecast",
        "sections": sections, "scenarios": scenarios, "thesis_change_gates": gates,
        "engine_crosscheck": engines, "engine_conflicts": conflicts,
        "change_in_observed_factors": changes, "fundamentals": fundamentals,
        "coverage": {"macro": "complete", "earnings_valuation": fundamentals["status"],
                     "belief": "not_publicly_available", "engine_flags": status},
        "evidence": lineage,
        "limitations": {"scenario_probabilities": None, "index_price_target": None,
                        "independent_model_votes": False, "unvalidated_heuristic": True,
                        "no_inference_from_abstain": True, "no_private_artifact_access": True},
    }
    snapshot["governance"]["trade_authority"] = False
    snapshot["governance"]["auto_promotion"] = False
    snapshot["governance"]["engine_writeback"] = False
    snapshot["governance"]["earnings_and_valuation_coverage"] = fundamentals["status"] == "curated_not_live"
    validate_research(snapshot)
    return snapshot


def validate_research(snapshot: dict) -> None:
    """Fail closed before the archive and current pointer are touched."""
    if snapshot.get("schema_version") != "2.0" or snapshot.get("governance", {}).get("trade_authority") is not False:
        raise ValueError("invalid House View v2 authority")
    research = snapshot.get("research") or {}
    sections = research.get("sections") or []
    names = {s.get("id") for s in sections}
    required = {"thesis", "growth_and_inflation", "rates_and_energy", "earnings_and_valuation",
                "tactical_and_models", "disagreements"}
    if names != required or len(sections) != len(required):
        raise ValueError("incomplete House View v2 sections")
    ids = {e.get("id") for e in research.get("evidence", [])}
    for section in sections:
        for lang in ("pl", "en"):
            if not isinstance(section.get(lang), str) or len(section[lang].strip()) < 45:
                raise ValueError("missing bilingual explanation: " + str(section.get("id")))
        if not set(section.get("source_ids", [])) <= ids:
            raise ValueError("untraceable section source")
    if {s.get("id") for s in research.get("scenarios", [])} != {"base", "downside", "upside"}:
        raise ValueError("missing scenarios")
    if {e.get("id") for e in research.get("engine_crosscheck", [])} != {"GSE", "FSE", "BRACE", "WES", "BELIEF"}:
        raise ValueError("incomplete engine cross-check")
    if any(e.get("directional_vote") is not None for e in research["engine_crosscheck"]):
        raise ValueError("unauthorized model vote")
    if research.get("limitations", {}).get("index_price_target") is not None:
        raise ValueError("unsupported price target")
