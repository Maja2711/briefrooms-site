"""Source-backed central-bank facts. No directional or execution authority.

Only a fetched, same-bank official document can verify a rate decision. RSS
headlines and third-party summaries never stand in for a live transcript.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import urlsplit


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def document_text(value: str) -> str:
    parser = _Text()
    parser.feed(value[:600_000])
    return " ".join(html.unescape(" ".join(parser.parts)).split())[:120_000]


def classify(headline: str, bank: str) -> str:
    lower = headline.lower()
    if "minutes" in lower:
        return "MINUTES"
    if "press conference" in lower or "monetary policy statement" in lower and bank == "ECB":
        return "PRESS_CONFERENCE"
    if bank == "FED" and ("fomc statement" in lower or "federal reserve issues" in lower and "statement" in lower):
        return "FOMC_DECISION"
    if bank == "ECB" and ("monetary policy decisions" in lower or "interest rate decision" in lower):
        return "ECB_DECISION"
    if "speech" in lower or "remarks" in lower or "testimony" in lower:
        return "SPEECH"
    if "interview" in lower:
        return "INTERVIEW"
    return "OTHER"


def _number(value: str) -> float | None:
    value = value.strip().replace("–", "-").replace("¼", ".25").replace("½", ".5").replace("¾", ".75")
    match = re.fullmatch(r"(\d+)(?:[ -](\d)/(\d))?", value)
    if match:
        return float(match[1]) + (int(match[2]) / int(match[3]) if match[2] else 0)
    try:
        number = float(value)
        return number if 0 <= number <= 30 else None
    except (ValueError, ZeroDivisionError):
        return None


_RATE = r"(\d+(?:\.\d+)?(?:[ -]\d/\d)?)"


def interpret_document(*, bank: str, headline: str, source_url: str,
                       document: str | None, source_class: str,
                       expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Conservative facts with exact document evidence; unknown stays unknown."""
    event_type = classify(headline, bank)
    host = (urlsplit(source_url).hostname or "").lower()
    official = ((bank == "FED" and host in {"federalreserve.gov", "www.federalreserve.gov"}) or
                (bank == "ECB" and host in {"ecb.europa.eu", "www.ecb.europa.eu"}))
    text = document_text(document or "")
    facts: dict[str, Any] = {"decision": None, "rate_previous": None, "rate_actual": None,
                             "rate_expected": None, "units": "percent", "forward_guidance": None,
                             "expectations_status": "MISSING", "surprise_bps": None}
    verified = False
    evidence: list[dict[str, str]] = []
    if official and text and source_class != "SECONDARY_HEADLINE":
        if bank == "FED" and event_type == "FOMC_DECISION":
            match = re.search(r"(?:decided|voted) to (maintain|lower|raise|increase|reduce) (?:the )?target range for the federal funds rate (?:at|to) " + _RATE + r" to " + _RATE + r" (?:percent|%)", text, re.I)
            if match:
                low, high = _number(match[2]), _number(match[3])
                if low is not None and high is not None and 0 <= high - low <= 1:
                    facts.update(decision=match[1].upper(), rate_actual={"lower": low, "upper": high}, rate_metric="federal_funds_target_range")
                    evidence.append({"field": "rate_actual", "source_url": source_url, "excerpt": match[0][:240]})
                    verified = True
        elif bank == "ECB" and event_type == "ECB_DECISION":
            # The three rates are not interchangeable: preserve their labels.
            match = re.search(r"interest rates on the deposit facility, the main refinancing operations and the marginal lending facility.{0,140}?(?:at|to|be) " + _RATE + r"\s*%[,;]?\s*(?:and\s*)?" + _RATE + r"\s*%\s*(?:and|,)\s*" + _RATE + r"\s*%", text, re.I)
            if match:
                rates = [_number(match[i]) for i in (1, 2, 3)]
                if all(rate is not None for rate in rates):
                    facts.update(rate_actual=dict(zip(("deposit_facility", "main_refinancing", "marginal_lending"), rates)), rate_metric="ecb_policy_rates")
                    evidence.append({"field": "rate_actual", "source_url": source_url, "excerpt": match[0][:240]})
                    verified = True
            if verified:
                change = re.search(r"(?:decided to|decision to) (keep|lower|raise|increase|reduce|cut)\b", text, re.I)
                facts["decision"] = change[1].upper() if change else "PUBLISHED_RATES"
        # A speech can be verified as a document, never as instantaneous speech.
        # Explicit speaker attribution comes from the document, never a roster.
    speaker_name, speaker_role = None, "UNKNOWN"
    if official and text and source_class != "SECONDARY_HEADLINE":
        for pattern, role in ((r"(?:Chair|Chairman|Chairwoman)\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z]+){1,3})", "FED_CHAIR"),
                              (r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}),\s+President of the (?:ECB|European Central Bank)", "ECB_PRESIDENT")):
            found = re.search(pattern, text)
            if found and ((bank == "FED" and role == "FED_CHAIR") or (bank == "ECB" and role == "ECB_PRESIDENT")):
                speaker_name, speaker_role = found[1], role
                break
    # Expectations require the caller's separately validated, contemporaneous
    # bundle; a headline's "expected" is not consensus. Wire never manufactures it.
    return {"event_type": event_type, "statement_verified": verified,
            "speaker_role": speaker_role, "speaker_name": speaker_name,
            "speaker_verified": speaker_name is not None,
            "facts": facts, "fact_evidence": evidence,
            "content_coverage": "OFFICIAL_DOCUMENT" if official and text else "HEADLINE_ONLY",
            "live_speech_status": "LIVE_SPEECH_UNVERIFIED",
            "risk_information_quality": "VERIFIED" if verified else "PARTIAL",
            "interpretation": {"usd_eur_bias": "UNKNOWN", "confidence": 0.0,
                               "evidence_urls": [source_url] if evidence else [], "authority": "CONTEXT_ONLY"}}
