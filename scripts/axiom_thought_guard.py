#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

REQUIRED_CURRENT_KEYS = {"date", "author", "brand", "pl", "en"}
REVIEW_REQUIRED_FROM = "2026-10-12"  # Historical records remain append-only.
REVIEW_KEYS = {"model", "verdict", "closest_ref", "shared_claim", "material_difference"}


def review_digest(candidate: dict[str, Any]) -> str:
    # Bind both semantic approvals to the EXACT text and concept that ships.
    payload = {key: candidate.get(key) for key in ("theme", "pl", "en")}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def review_evidence_errors(record: dict[str, Any], earlier_dates: set[str]) -> list[str]:
    result: list[str] = []
    receipts = record.get("semantic_reviews")
    if not isinstance(receipts, list) or len(receipts) != 2:
        return ["publication requires two independent semantic review receipts"]
    models = set()
    for index, receipt in enumerate(receipts, start=1):
        if not isinstance(receipt, dict) or set(receipt) != REVIEW_KEYS:
            result.append(f"review {index} has invalid evidence schema")
            continue
        model = receipt["model"]
        if not isinstance(model, str) or not model.strip() or model in models:
            result.append(f"review {index} must use a distinct model")
        else:
            models.add(model)
        if receipt["verdict"] != "UNIQUE":
            result.append(f"review {index} did not approve originality")
        if receipt["closest_ref"] not in earlier_dates:
            result.append(f"review {index} does not cite a previous published thought")
        for key in ("shared_claim", "material_difference"):
            if not isinstance(receipt[key], str) or len(receipt[key].strip()) < 30:
                result.append(f"review {index} is missing substantive {key}")
    if record.get("semantic_review_digest") != review_digest(record):
        result.append("semantic review evidence is not bound to the exact published thought")
    return result

HISTORY_RETENTION_DAYS = 365
MAX_SEQUENCE_SIMILARITY = 0.72
MAX_TOKEN_JACCARD = 0.55

# New editorial rule applies prospectively: never retroactively invalidate
# published thoughts or rewrite the append-only historical record.
RHETORICAL_GUARD_FROM = "2026-10-13"
RHETORICAL_STYLE_WINDOWS = {
    "rarely-frame": 30,
    "rarely-more-often": 30,
    "not-when-but": 21,
    "not-but-contrast": 14,
    "superlative-opening": 14,
}

BANNED_PHRASES_PL = (
    "uwierz w siebie",
    "nigdy się nie poddawaj",
    "nie poddawaj się",
    "każdy dzień to nowa szansa",
    "każdy dzień jest nową szansą",
    "najlepszą wersją siebie",
    "najlepsza wersja siebie",
    "wszystko jest możliwe",
    "marzenia się spełniają",
    "sukces to",
    "droga do sukcesu",
    "myśl pozytywnie",
)

BANNED_PHRASES_EN = (
    "believe in yourself",
    "never give up",
    "every day is a new opportunity",
    "every day is a new chance",
    "best version of yourself",
    "anything is possible",
    "dreams come true",
    "the road to success",
    "success is",
    "think positive",
)

@dataclass(frozen=True)
class Similarity:
    sequence: float
    jaccard: float

    @property
    def violates(self) -> bool:
        return self.sequence >= MAX_SEQUENCE_SIMILARITY or self.jaccard >= MAX_TOKEN_JACCARD


def _deaccent(text: str) -> str:
    # NFKD does not decompose Polish ł/Ł; normalize them explicitly.
    normalized = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_text(text: str) -> str:
    text = _deaccent(text.lower())
    text = text.replace("—", "-").replace("–", "-")
    text = re.sub(r"[^a-z0-9\s-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def token_set(text: str) -> set[str]:
    return {tok for tok in normalize_text(text).split() if len(tok) > 2}


def similarity(a: str, b: str) -> Similarity:
    na, nb = normalize_text(a), normalize_text(b)
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = token_set(a), token_set(b)
    union = ta | tb
    jac = len(ta & tb) / len(union) if union else 1.0
    return Similarity(sequence=seq, jaccard=jac)


def rhetorical_templates(polish: str) -> set[str]:
    """Deterministic, theme-agnostic Polish rhetoric signatures.

    Distinguish repeated *sentence architecture* from repeated words or ideas.
    Semantic uniqueness is still checked separately by two independent models.
    """
    if not isinstance(polish, str):
        return set()
    words = normalize_text(polish).split()
    if not words:
        return set()
    text = " ".join(words)
    result: set[str] = set()
    if "rzadko" in words:
        result.add("rarely-frame")
        if "czesciej" in words:
            result.add("rarely-more-often")
    if re.search(r"\bnie wtedy\b", text) and re.search(r"\b(lecz|ale|gdy|kiedy)\b", text):
        result.add("not-when-but")
    if re.search(r"\bnie\b.{0,170}\b(lecz|ale)\b", text):
        result.add("not-but-contrast")
    if words[0].startswith("naj") and len(words[0]) >= 6:
        result.add("superlative-opening")
    return result


def rhetorical_style_issues(candidate_pl: str, previous: list[dict[str, Any]], publication_date: str) -> list[str]:
    """Block an already-used stylistic template inside its rolling lookback.

    All earlier published thoughts remain valid, including the 12 Oct motto
    that motivated this change. Invalid dates are reported by the main guard.
    """
    if not _valid_iso_date(publication_date) or publication_date < RHETORICAL_GUARD_FROM:
        return []
    stamp = date.fromisoformat(publication_date)
    current_patterns = rhetorical_templates(candidate_pl)
    findings: list[str] = []
    for prior in previous:
        prior_date = prior.get("date")
        if not _valid_iso_date(prior_date):
            continue
        elapsed = (stamp - date.fromisoformat(prior_date)).days
        if elapsed <= 0:
            continue
        for name in sorted(current_patterns & rhetorical_templates(prior.get("pl", ""))):
            if elapsed <= RHETORICAL_STYLE_WINDOWS[name]:
                findings.append(
                    f"rhetorical-style duplicate: {name} (previous={prior_date}, "
                    f"lookback={RHETORICAL_STYLE_WINDOWS[name]}d)"
                )
    return findings


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{lineno}: each line must be a JSON object")
        rows.append(row)
    return rows


def _valid_iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _score(record: dict[str, Any], key: str) -> int | None:
    scores = record.get("scores")
    if not isinstance(scores, dict):
        return None
    value = scores.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _contains_banned(text: str, phrases: Iterable[str]) -> str | None:
    norm = normalize_text(text)
    for phrase in phrases:
        if normalize_text(phrase) in norm:
            return phrase
    return None


def validate(current: dict[str, Any], history: list[dict[str, Any]], *, require_review_evidence: bool = True) -> list[str]:
    errors: list[str] = []

    if set(current.keys()) != REQUIRED_CURRENT_KEYS:
        errors.append(
            "current thought must use exactly these keys: "
            + ", ".join(sorted(REQUIRED_CURRENT_KEYS))
        )

    if current.get("author") != "AXIOM":
        errors.append("author must be AXIOM")
    if current.get("brand") != "BriefRooms":
        errors.append("brand must be BriefRooms")
    if not _valid_iso_date(current.get("date")):
        errors.append("current date must be ISO YYYY-MM-DD")

    pl = current.get("pl")
    en = current.get("en")
    if not isinstance(pl, str) or not (pl.startswith("„") and pl.endswith("”")):
        errors.append("Polish thought must be wrapped in Polish quotation marks „…”")
    if not isinstance(en, str) or not (en.startswith("“") and en.endswith("”")):
        errors.append("English thought must be wrapped in English quotation marks “… ” without extra text")

    if isinstance(pl, str) and not (70 <= len(pl) <= 340):
        errors.append("Polish thought should be concise: 70–340 characters including quotes")
    if isinstance(en, str) and not (60 <= len(en) <= 360):
        errors.append("English thought should be concise: 60–360 characters including quotes")

    if isinstance(pl, str):
        banned = _contains_banned(pl, BANNED_PHRASES_PL)
        if banned:
            errors.append(f"banality phrase detected in Polish thought: {banned!r}")
    if isinstance(en, str):
        banned = _contains_banned(en, BANNED_PHRASES_EN)
        if banned:
            errors.append(f"banality phrase detected in English thought: {banned!r}")

    if not history:
        errors.append("history must contain at least one record")
        return errors

    seen_dates: set[str] = set()
    for idx, record in enumerate(history):
        stamp = record.get("date")
        if not _valid_iso_date(stamp):
            errors.append(f"history record {idx + 1} has invalid date")
        elif stamp in seen_dates:
            errors.append(f"history contains duplicate date: {stamp}")
        else:
            seen_dates.add(stamp)

        if not isinstance(record.get("theme"), str) or not record.get("theme", "").strip():
            errors.append(f"history record {idx + 1} must define a non-empty theme")
        if record.get("silence_test") is not True:
            errors.append(f"history record {idx + 1} must pass silence_test")

        for score_name in ("depth", "novelty", "banality_risk"):
            value = _score(record, score_name)
            if value is None or not 0 <= value <= 10:
                errors.append(f"history record {idx + 1} has invalid {score_name} score")

        note = record.get("editor_note")
        if not isinstance(note, str) or len(note.strip()) < 40:
            errors.append(f"history record {idx + 1} needs a substantive editor_note")

        if (require_review_evidence and _valid_iso_date(stamp)
                and stamp >= REVIEW_REQUIRED_FROM):
            prior_dates = {r.get("date") for r in history[:idx]}
            errors.extend(f"history record {idx + 1}: {msg}" for msg in review_evidence_errors(record, prior_dates))

        if not record.get("seed", False):
            candidates = record.get("candidates_considered")
            if isinstance(candidates, bool) or not isinstance(candidates, int) or candidates < 5:
                errors.append(f"history record {idx + 1} must consider at least 5 candidates")
            depth = _score(record, "depth")
            novelty = _score(record, "novelty")
            banality = _score(record, "banality_risk")
            if depth is not None and depth < 8:
                errors.append(f"history record {idx + 1} depth must be >= 8")
            if novelty is not None and novelty < 8:
                errors.append(f"history record {idx + 1} novelty must be >= 8")
            if banality is not None and banality > 2:
                errors.append(f"history record {idx + 1} banality_risk must be <= 2")

    latest = history[-1]
    for key in ("date", "pl", "en"):
        if latest.get(key) != current.get(key):
            errors.append(f"latest history record must match current thought field {key!r}")

    # The first entry is an explicit seed used to bootstrap the memory. No later
    # entry may be marked as a seed.
    for idx, record in enumerate(history[1:], start=2):
        if record.get("seed") is True:
            errors.append(f"history record {idx} cannot be marked as seed")

    if len(history) > 1:
        latest_theme = latest.get("theme")
        latest_date = date.fromisoformat(latest["date"])
        cutoff = latest_date - timedelta(days=HISTORY_RETENTION_DAYS)
        protected_history = [
            r for r in history[:-1]
            if _valid_iso_date(r.get("date")) and date.fromisoformat(r["date"]) >= cutoff
        ]
        # Textual duplication is prohibited against the *entire* append-only
        # archive, including records outside the theme-rotation window.
        all_previous = history[:-1]
        if isinstance(latest.get("pl"), str):
            errors.extend(rhetorical_style_issues(latest["pl"], all_previous, latest["date"]))

        # A conceptual theme is exclusive for the full 365-day memory window.
        # This blocks a generator from rephrasing an old idea under the same theme.
        repeated = [r.get("date") for r in protected_history if r.get("theme") == latest_theme]
        if repeated:
            errors.append(
                f"theme {latest_theme!r} repeats within protected {HISTORY_RETENTION_DAYS}-day history"
            )

        latest_pl = latest.get("pl")
        latest_en = latest.get("en")
        if isinstance(latest_pl, str):
            for previous in all_previous:
                previous_pl = previous.get("pl")
                if not isinstance(previous_pl, str):
                    continue
                sim = similarity(latest_pl, previous_pl)
                if normalize_text(latest_pl) == normalize_text(previous_pl):
                    errors.append(f"exact thought duplicate in full history: {previous.get('date')}")
                elif sim.violates:
                    errors.append(
                        "thought is too similar to full history entry "
                        f"{previous.get('date')}: sequence={sim.sequence:.2f}, "
                        f"jaccard={sim.jaccard:.2f}"
                    )
                previous_en = previous.get("en")
                if isinstance(latest_en, str) and isinstance(previous_en, str):
                    en_sim = similarity(latest_en, previous_en)
                    if normalize_text(latest_en) == normalize_text(previous_en):
                        errors.append(f"exact English thought duplicate in full history: {previous.get('date')}")
                    elif en_sim.violates:
                        errors.append(
                            "English thought is too similar to full history entry "
                            f"{previous.get('date')}: sequence={en_sim.sequence:.2f}, "
                            f"jaccard={en_sim.jaccard:.2f}"
                        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate AXIOM Thought Engine publication")
    parser.add_argument(
        "--current",
        default="data/home/axiom-thought.json",
        type=Path,
        help="current published thought JSON",
    )
    parser.add_argument(
        "--history",
        default="data/home/axiom-thoughts-history.jsonl",
        type=Path,
        help="append-only thought history JSONL",
    )
    args = parser.parse_args()

    try:
        current = read_json(args.current)
        history = read_jsonl(args.history)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"AXIOM Thought Guard: ERROR: {exc}", file=sys.stderr)
        return 2

    errors = validate(current, history)
    if errors:
        print("AXIOM Thought Guard: REJECTED", file=sys.stderr)
        for error in errors:
            print(f" - {error}", file=sys.stderr)
        return 1

    latest = history[-1]
    print(
        "AXIOM Thought Guard: PASSED "
        f"date={latest.get('date')} theme={latest.get('theme')} "
        f"depth={_score(latest, 'depth')} novelty={_score(latest, 'novelty')} "
        f"banality_risk={_score(latest, 'banality_risk')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
