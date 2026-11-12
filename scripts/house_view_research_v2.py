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



def _investment_editorial_sections(inputs: dict, views: dict, engines: list,
                                   fundamentals: dict, conflicts: list) -> list:
    """Source-bounded market implications in plain PL/EN; zero new model authority.

    This is an *interpretation* of frozen macro measurements and model-risk flags.
    No Fed path, earnings beat, S&P price level, forecast probability or verified
    return is inferred from missing sources.
    """
    gdp = float(inputs["gdp_qoq_saar_pct"])
    core = float(inputs["core_pce_yoy_pct"])
    pce = float(inputs["pce_yoy_pct"])
    ten = float(inputs["us_10y_pct"])
    oil = float(inputs["brent_usd_bbl"])
    momentum = inputs.get("spx_futures_20d_return_pct")
    one = views["1M"]["bias"]
    six = views["6M"]["bias"]
    twelve = views["12M"]["bias"]
    flags = {e.get("risk_flag") for e in engines if e.get("status") == "research_only"}
    sections = []

    lead_pl = {
        "defensive": "S&P 500: na najbliższy miesiąc przeważa ryzyko korekty lub słabej konsolidacji; nie ma jeszcze podstaw, by zakładać trwałe odbicie.",
        "neutral": "S&P 500: w najbliższym miesiącu brak wyraźnej przewagi kierunkowej; bardziej prawdopodobny do rozważenia jest rynek wahliwy niż jednostajny trend.",
        "mild_positive": "S&P 500: na najbliższy miesiąc bilans czynników sprzyja umiarkowanemu odbiciu, ale nie jest to sygnał silnego trendu wzrostowego.",
        "unassessed": "S&P 500: nie określamy kierunku na miesiąc bez wiarygodnego krótkoterminowego odczytu rynku."
    }[one]
    lead_en = {
        "defensive": "S&P 500: over the next month, correction or weak consolidation risk dominates; there is insufficient evidence yet for a sustained rebound.",
        "neutral": "S&P 500: no clear directional advantage over the next month; a choppy market is a more defensible scenario than a persistent trend.",
        "mild_positive": "S&P 500: the next-month balance favors a modest rebound, not a strong confirmed bull trend.",
        "unassessed": "S&P 500: no one-month directional call without a qualified short-term market observation."
    }[one]
    medium_pl = {
        "defensive": "Na 6 miesięcy utrzymujemy podejście defensywne",
        "neutral": "Na 6 miesięcy pozostajemy neutralni",
        "mild_positive": "Na 6 miesięcy widzimy umiarkowaną przewagę argumentów za wzrostem",
        "unassessed": "Nie wyznaczamy oceny na 6 miesięcy"
    }[six]
    medium_en = {
        "defensive": "We retain a defensive six-month view",
        "neutral": "We stay neutral over six months",
        "mild_positive": "The six-month balance modestly favors gains",
        "unassessed": "No six-month view is assigned"
    }[six]
    long_pl = {
        "defensive": "12M defensywnie",
        "neutral": "12M neutralnie",
        "mild_positive": "12M lekko pozytywnie",
        "unassessed": "12M bez oceny"
    }[twelve]
    long_en = {
        "defensive": "12M defensive",
        "neutral": "12M neutral",
        "mild_positive": "12M mildly positive",
        "unassessed": "12M unassessed"
    }[twelve]
    _add_section(sections, "thesis",
        f"{lead_pl} {medium_pl}; horyzont 12M: {long_pl}. "
        f"Wzrost gospodarczy {gdp:+.2f}% nie wskazuje sam w sobie załamania aktywności, "
        f"ale inflacja bazowa {core:.2f}% i rentowność obligacji 10Y {ten:.2f}% utrudniają ekspansję wycen akcji. "
        "Silniejszy scenariusz wzrostowy wymagałby spadku presji inflacyjnej lub potwierdzonej poprawy zysków spółek.",
        f"{lead_en} {medium_en}; 12-month stance: {long_en}. "
        f"Economic growth of {gdp:+.2f}% does not by itself indicate an activity collapse, "
        f"but core inflation at {core:.2f}% and the 10-year yield at {ten:.2f}% constrain equity valuation multiples. "
        "A stronger upside case would require less inflation pressure or verified earnings improvement.",
        ["GDPC1", "PCEPILFE", "DGS10"] + (["WES"] if momentum is not None else []))

    if gdp >= 2 and core >= 2.7:
        growth_pl = ("Gospodarka rośnie na tyle szybko, że wspiera potencjał przychodów spółek, "
                     "jednak uporczywa inflacja może utrzymywać koszt finansowania i presję na marże. "
                     "Dla S&P 500 oznacza to raczej wsparcie z aktywności niż argument za szybkim wzrostem mnożników P/E.")
        growth_en = ("Economic activity supports companies' revenue potential, "
                     "but persistent inflation may keep funding costs and margin pressure elevated. "
                     "For the S&P 500 this is growth support, not an automatic reason for higher P/E multiples.")
    elif gdp < 1:
        growth_pl = ("Słabsza aktywność zwiększa ryzyko rozczarowań przychodami i zyskami. "
                     "Dla S&P 500 jest to argument za ostrożnością, szczególnie jeśli inflacja nie pozwala na łatwiejsze warunki finansowe.")
        growth_en = ("Weak activity raises the risk of revenue and earnings disappointment. "
                     "This warrants caution on the S&P 500, especially if inflation prevents easier financial conditions.")
    elif core <= 2.3 and gdp >= 1:
        growth_pl = ("Dodatni wzrost przy niższej inflacji bazowej tworzy korzystniejsze otoczenie dla akcji. "
                     "S&P 500 mógłby skorzystać z mniejszej presji kosztowej i dyskontowej, jeżeli trend utrzymają kolejne publikacje.")
        growth_en = ("Positive growth with lower core inflation is more supportive of equities. "
                     "The S&P 500 could benefit from reduced cost and discount-rate pressure if subsequent releases confirm the trend.")
    else:
        growth_pl = ("Wzrost pozostaje dodatni, lecz połączenie aktywności i inflacji nie przesądza o wzroście S&P 500. "
                     "Kluczowe będzie, czy spółki przełożą popyt na zyski bez dalszej presji kosztowej.")
        growth_en = ("Growth is positive but the growth/inflation mix does not by itself imply S&P 500 gains. "
                     "What matters is whether companies convert demand into profits without renewed cost pressure.")
    _add_section(sections, "growth_and_inflation",
        f"PKB USA {gdp:+.2f}% q/q SAAR, PCE {pce:.2f}% r/r, bazowy PCE {core:.2f}% r/r. {growth_pl}",
        f"U.S. GDP {gdp:+.2f}% q/q SAAR, PCE {pce:.2f}% y/y, core PCE {core:.2f}% y/y. {growth_en}",
        ["GDPC1", "PCEPI", "PCEPILFE"])

    if ten >= 4.5:
        rates_pl = ("Rentowność 10Y na tym poziomie podnosi stopę dyskontową dla przyszłych zysków "
                    "i zwiększa atrakcyjność obligacji względem akcji. Nasz scenariusz bazowy nie zakłada "
                    "automatycznej ekspansji P/E S&P 500; aby zmienić to stanowisko, "
                    "potrzebne byłyby niższe rentowności bez pogorszenia koniunktury albo wyraźnie lepszy EPS.")
        rates_en = ("The 10-year yield at this level raises the discount rate on future earnings "
                    "and makes bonds more competitive with stocks. Our base case does not assume "
                    "automatic S&P 500 P/E expansion; lower yields without weaker activity "
                    "or demonstrably stronger EPS would improve the case.")
    elif ten <= 3.5:
        rates_pl = ("Niższa rentowność zmniejsza presję dyskontową na wyceny akcji i może wspierać S&P 500, "
                    "o ile nie wynika przede wszystkim z gwałtownego pogorszenia perspektyw gospodarki.")
        rates_en = ("Lower yields ease equity discount-rate pressure and could support the S&P 500, "
                    "provided they are not primarily driven by a sharp deterioration in growth expectations.")
    else:
        rates_pl = ("Koszt kapitału nadal ogranicza wyceny, choć nie stanowi samodzielnego sygnału spadkowego. "
                    "Dla S&P 500 kierunek rentowności będzie ważniejszy niż ich sama wartość: spadek bez recesji sprzyjałby P/E.")
        rates_en = ("Funding costs continue to limit valuations, although yield levels alone do not predict a decline. "
                    "For the S&P 500 the direction of yields matters: easing without recession would be more supportive of P/E.")
    oil_pl = ("Ropa powyżej 110 USD/bbl zwiększa ryzyko wzrostu kosztów i presji inflacyjnej, co może pogarszać marże i wyceny."
              if oil >= 110 else
              "Ropa blisko lub powyżej 100 USD/bbl pozostaje potencjalnym obciążeniem marż i dezinflacji."
              if oil >= 100 else
              "Niższa cena ropy ogranicza część presji kosztowej, lecz nie rozstrzyga sama o trendzie S&P 500.")
    oil_en = ("Oil above USD 110/bbl raises cost and inflation risks, potentially pressuring margins and valuations."
              if oil >= 110 else
              "Oil near or above USD 100/bbl remains a potential drag on margins and disinflation."
              if oil >= 100 else
              "Lower oil prices ease some cost pressure, but do not establish an S&P 500 trend on their own.")
    _add_section(sections, "rates_and_energy",
        f"Rentowność US 10Y: {ten:.2f}%; Brent: {oil:.2f} USD/bbl. {rates_pl} {oil_pl} "
        "Nie prognozujemy automatycznie kolejnej decyzji Fed z samego poziomu rentowności.",
        f"U.S. 10-year yield: {ten:.2f}%; Brent: USD {oil:.2f}/bbl. {rates_en} {oil_en} "
        "The yield level alone does not justify a specific forecast for the next Fed decision.",
        ["DGS10", "DCOILBRENTEU", "PCEPILFE"])

    if fundamentals["status"] == "curated_not_live":
        pe = fundamentals["forward_pe"]
        eps = fundamentals["forward_eps_growth_pct"]
        fpl = (f"Źródłowy forward P/E wynosi {pe:.2f}x, a prognozowany wzrost EPS {eps:+.2f}% "
               f"(publikacja {fundamentals['source_published_at'][:10]}). "
               "Dla S&P 500 wzrost zysków mógłby podtrzymać indeks pomimo wysokich rentowności, "
               "ale wzrost cen bez poprawy EPS zwiększałby ryzyko droższej wyceny. "
               "Bez niezależnych danych o rewizjach EPS nie traktujemy tego jako potwierdzonej ścieżki indeksu.")
        fen = (f"Sourced forward P/E is {pe:.2f}x and projected EPS growth is {eps:+.2f}% "
               f"(document dated {fundamentals['source_published_at'][:10]}). "
               "Earnings growth could support the S&P 500 despite high yields, "
               "but price gains without EPS gains would increase valuation risk. "
               "Without independent EPS revision data this is not a verified index path.")
        frefs = ["FUNDAMENTALS", "DGS10"]
    else:
        fpl = ("W średnim i długim terminie o trwałości wzrostu S&P 500 zdecydują przede wszystkim zyski spółek "
               "oraz cena, jaką rynek płaci za te zyski. Nie mamy aktualnego, potwierdzonego konsensusu "
               "EPS, rewizji prognoz ani forward P/E, dlatego nie zakładamy dalszego wzrostu indeksu tylko "
               "dzięki droższym wycenom. Dodatni PKB wspiera potencjał sprzedaży, ale nie dowodzi wzrostu "
               "EPS. Bez takiego potwierdzenia nie podnosimy oceny 6M/12M i nie publikujemy targetu punktowego.")
        fen = ("Over six to twelve months the S&P 500 depends heavily on company earnings and the multiple "
               "investors pay for them. Current index-level EPS consensus, revisions and forward P/E "
               "are not verified, so we do not assume the index will rise simply through more expensive valuations. "
               "Positive GDP supports demand but does not establish EPS growth. Without that evidence "
               "we do not upgrade the 6M/12M stance or publish a numerical price target.")
        frefs = ["GDPC1", "DGS10"]
    _add_section(sections, "earnings_and_valuation", fpl, fen, frefs)

    if momentum is None:
        tech_pl = ("Nie ma wiarygodnego odczytu momentum dla ostatnich 20 sesji, "
                   "więc nie formułujemy nowego kierunku na 1M. Ocenę ryzyka dla inwestora "
                   "opieramy jedynie na potwierdzonych danych makro.")
        tech_en = ("No qualified 20-session momentum observation is available, "
                   "so there is no new one-month directional call. The investor risk view "
                   "is limited to verified macro data.")
    elif momentum <= -3:
        tech_pl = (f"S&P 500 futures straciły {abs(momentum):.2f}% w 20 sesjach. "
                   "To sygnał osłabienia rynku i ryzyka dalszych przecen w 1M; "
                   "sam spadek nie pozwala jednak wskazać dna ani ceny odbicia. "
                   "Poprawa impetu byłaby pierwszym warunkiem ograniczenia tej ostrożności.")
        tech_en = (f"S&P 500 futures fell {abs(momentum):.2f}% over 20 sessions. "
                   "This indicates a weaker market and further near-term drawdown risk; "
                   "it does not identify a bottom or rebound price. "
                   "Improving momentum would be the first condition for reducing caution.")
    elif momentum >= 3:
        tech_pl = (f"S&P 500 futures wzrosły {momentum:.2f}% w 20 sesjach. "
                   "Rynek ma dodatni impuls krótkoterminowy, co poprawia szanse odbicia, "
                   "lecz przy wysokim koszcie kapitału nie oznacza automatycznie trwałego trendu 6M/12M.")
        tech_en = (f"S&P 500 futures gained {momentum:.2f}% over 20 sessions. "
                   "Positive short-term momentum improves rebound conditions, "
                   "but at elevated funding costs it does not establish a durable 6M/12M bull market.")
    else:
        tech_pl = (f"Zmiana S&P 500 futures w 20 sesjach wynosi {momentum:+.2f}%. "
                   "Impuls cenowy nie daje jednoznacznego przełomu. "
                   "Dopiero trwalsza poprawa cen i otoczenia inflacyjnego zwiększyłaby wiarygodność wzrostowego scenariusza.")
        tech_en = (f"S&P 500 futures changed {momentum:+.2f}% over 20 sessions. "
                   "Price momentum does not signal a clear breakout. "
                   "Sustained price improvement alongside easier inflation would strengthen the upside case.")
    refs = ["WES"] if momentum is not None else []
    if "structural_caution" in flags:
        tech_pl += (" Dane struktury rynku wskazują dodatkowo na podwyższone ryzyko gwałtownych wahań; "
                    "wzmacnia to potrzebę ostrożnej interpretacji krótkiego terminu, ale nie jest osobną prognozą spadku.")
        tech_en += (" Market-structure evidence also indicates greater risk of abrupt swings; "
                    "this reinforces near-term caution but is not a separate downside forecast.")
        refs.append("FSE")
    if "geopolitical_caution" in flags:
        tech_pl += (" Ryzyko geopolityczne może dodatkowo zwiększać premię za ryzyko na rynku akcji.")
        tech_en += (" Geopolitical stress could add to the risk premium investors demand from equities.")
        refs.append("GSE")
    _add_section(sections, "tactical_and_models", tech_pl, tech_en, refs)

    if conflicts:
        disagree_pl = ("Sygnały krótkoterminowe nie są zgodne: wzrost cen i ryzyka geopolityczne/strukturalne "
                       "wskazują różne kierunki nacisku. Dla S&P 500 nie przesądzamy, który przeważy; "
                       "sprawdzeniem będzie trwałość ruchu cen, inflacja i rentowności. "
                       "Nie sumujemy modeli jako niezależnych głosów.")
        disagree_en = ("Near-term evidence diverges: price action and geopolitical/structural risk "
                       "point to different pressures. We do not claim one will prevail for the S&P 500; "
                       "price persistence, inflation and yields are the relevant checks. "
                       "Overlapping models are not treated as independent votes.")
        refs = ["GSE", "FSE", "WES"]
    elif one == "defensive":
        disagree_pl = ("Co zaneguje ostrożną tezę 1M? Trwała poprawa momentum S&P 500, "
                       "słabnięcie inflacji bazowej i spadek rentowności bez pogorszenia wzrostu. "
                       "Obecnie nie ma wystarczającego dowodu na pełny zwrot wzrostowy; "
                       "dodatni PKB jest przeciwwagą dla pesymizmu, nie dowodem na osiągnięcie dna.")
        disagree_en = ("What would overturn the cautious 1M view? Sustained improvement in S&P 500 momentum, "
                       "easing core inflation and lower yields without weakening growth. "
                       "There is not enough evidence yet for a durable reversal; "
                       "positive GDP offsets the bearish case but does not establish a market bottom.")
        refs = ["GDPC1", "PCEPILFE", "DGS10"] + (["WES"] if momentum is not None else [])
    else:
        disagree_pl = ("Warunki zmiany opinii: utrzymująca się presja inflacyjna i wzrost rentowności "
                       "przemawiają za większą ostrożnością; równoczesne schłodzenie inflacji, "
                       "stabilny wzrost i poprawa momentum — za silniejszą tezą wzrostową. "
                       "Nie traktujemy braku konfliktu modeli jako potwierdzenia prognozy.")
        disagree_en = ("Revision conditions: persistent inflation pressure and higher yields "
                       "would argue for more caution; easing inflation with steady growth "
                       "and better momentum would strengthen the upside case. "
                       "Lack of a model disagreement does not validate a forecast.")
        refs = ["GDPC1", "PCEPILFE", "DGS10"] + (["WES"] if momentum is not None else [])
    _add_section(sections, "disagreements", disagree_pl, disagree_en, refs)
    return sections


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
    conflicts = []
    gse = next(e for e in engines if e["id"] == "GSE")
    if gse.get("risk_flag") == "geopolitical_caution" and market is not None and market >= 3:
        conflicts.append("gse_vs_positive_momentum")
    if "structural_caution" in engine_flags and views["6M"]["bias"] == "mild_positive":
        conflicts.append("positive_macro_vs_structural_risk")
    sections = _investment_editorial_sections(inputs, views, engines, fundamentals, conflicts)
    scenarios = [
        {"id": "base", "probability": None,
         "pl": f"Scenariusz bazowy dla S&P 500: 1M {LABELS['pl'][views['1M']['bias']]}, 6M {LABELS['pl'][views['6M']['bias']]}, 12M {LABELS['pl'][views['12M']['bias']]}. "
               "Odbicie cen bez spadku presji dyskontowej lub poprawy zysków może być nietrwałe. Kolejne odczyty PKB, inflacji i rentowności zweryfikują tezę.",
         "en": f"S&P 500 base case: 1M {LABELS['en'][views['1M']['bias']]}, 6M {LABELS['en'][views['6M']['bias']]}, 12M {LABELS['en'][views['12M']['bias']]}. "
               "A price rebound without easing discount-rate pressure or improving earnings may be fragile. Subsequent GDP, inflation and yield data will test the thesis.",
         "watch_ids": ["GDPC1", "PCEPILFE", "DGS10"]},
        {"id": "downside", "probability": None,
         "pl": "Scenariusz pogorszenia: wysoka inflacja bazowa i rosnące rentowności ograniczają mnożniki P/E, "
               "a słabszy popyt lub droższa energia uderzają w zyski. S&P 500 byłby wówczas podatniejszy na dalszą korektę.",
         "en": "Downside: persistent core inflation and rising yields constrain P/E multiples, "
               "while weaker demand or higher energy costs pressure earnings. The S&P 500 would become more exposed to further correction.",
         "watch_ids": ["PCEPILFE", "DGS10", "DCOILBRENTEU", "GDPC1"]},
        {"id": "upside", "probability": None,
         "pl": "Scenariusz poprawy: dezinflacja przy utrzymaniu dodatniego wzrostu umożliwia spadek rentowności; "
               "wraz z poprawą momentum i potwierdzonym wzrostem EPS zwiększałoby to szanse na trwalszy wzrost S&P 500.",
         "en": "Upside: disinflation alongside positive growth allows yields to ease; "
               "with improving momentum and verified EPS growth, this would strengthen prospects for a sustained S&P 500 advance.",
         "watch_ids": ["GDPC1", "PCEPILFE", "DGS10"]},
    ]
    gates = [
        {"id": "core_pce", "source_id": "PCEPILFE", "threshold": 2.7,
         "pl": "Core PCE poniżej 2,7% r/r: mniejsza presja inflacyjna wspiera scenariusz poprawy wycen S&P 500, o ile wzrost pozostaje dodatni.",
         "en": "Core PCE below 2.7% y/y: less inflation pressure improves the S&P 500 valuation case, provided growth stays positive."},
        {"id": "gdp", "source_id": "GDPC1", "threshold": 1.0,
         "pl": "PKB poniżej 1% q/q SAAR: zwiększa się ryzyko rozczarowania przychodami i EPS, a więc argument za obniżeniem oceny 6M/12M.",
         "en": "GDP below 1% q/q SAAR: raises revenue/EPS disappointment risk and argues for reassessing the 6M/12M view."},
        {"id": "brent", "source_id": "DCOILBRENTEU", "threshold": 110.0,
         "pl": "Brent powyżej 110 USD/bbl: możliwa presja na marże i inflację, co zwiększa ryzyko korekty S&P 500.",
         "en": "Brent above USD 110/bbl: possible margin and inflation pressure increases S&P 500 correction risk."},
        {"id": "ten_year", "source_id": "DGS10", "threshold": 4.5,
         "pl": "UST 10Y trwale poniżej 4,5% bez osłabienia PKB: korzystniejszy koszt kapitału mógłby pomóc mnożnikom P/E.",
         "en": "10-year yield sustainably below 4.5% without weaker GDP: a better cost of capital could support P/E multiples."},
        {"id": "momentum", "source_id": "WES", "threshold": 3.0,
         "pl": "Momentum S&P 500 futures powyżej +3% za 20 sesji: mocniejszy argument za odzyskiwaniem trendu wzrostowego w 1M.",
         "en": "S&P 500 futures 20-session momentum above +3%: stronger evidence for renewed near-term upward trend."},
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
