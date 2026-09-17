#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "data/home/axiom-thought.json"
HISTORY = ROOT / "data/home/axiom-thoughts-history.jsonl"
PRINCIPLES = ROOT / "docs/axiom-thought-principles.md"
GUARD = ROOT / "scripts/axiom_thought_guard.py"
WARSAW = ZoneInfo("Europe/Warsaw")
MODEL = os.getenv("AXIOM_THOUGHT_MODEL", "gemini-3.5-flash")
FALLBACK_MODEL = os.getenv("AXIOM_THOUGHT_FALLBACK_MODEL", "gemini-3.5-flash-lite")


def today() -> str:
    return datetime.now(WARSAW).date().isoformat()


def history_rows() -> list[dict]:
    return [json.loads(line) for line in HISTORY.read_text(encoding="utf-8").splitlines() if line.strip()]


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model returned no JSON object")
    return json.loads(text[start:end + 1])


def call_gemini(prompt: str, model: str) -> dict:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 1.15, "responseMimeType": "application/json"},
    }
    r = requests.post(url, json=payload, timeout=90)
    r.raise_for_status()
    data = r.json()
    return extract_json(data["candidates"][0]["content"]["parts"][0]["text"])


def build_prompt(rows: list[dict]) -> str:
    principles = PRINCIPLES.read_text(encoding="utf-8")
    archive = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    return f"""You are AXIOM, co-author of BriefRooms. Create today's Morning AXIOM thought.
This is NOT motivational quote generation. Intellectual depth and originality are mandatory.

DATE: {today()}

EDITORIAL CONSTITUTION:
{principles}

FULL PUBLISHED ARCHIVE:
{archive}

Internally generate and seriously compare AT LEAST 7 conceptually different candidates from different domains. Reject slogans, corporate wisdom, self-help, pretty obvious truths, paraphrases of famous quotations, and anything semantically close to the archive. The thought need not concern BriefRooms, business, AI or technology. Prefer a precise insight that creates a genuine second thought.

Return ONLY one JSON object with exactly these fields:
{{
  "theme": "short-kebab-case-theme",
  "pl": "„Polish thought”",
  "en": "“faithful English translation”",
  "candidates_considered": 7,
  "scores": {{"depth": 8-10, "novelty": 8-10, "banality_risk": 0-2}},
  "silence_test": true,
  "editor_note": "minimum 80 characters: explain the non-obvious insight, why it survived the other candidates, and why it is not a repetition"
}}
Do not inflate scores to rescue a weak thought. If the best candidate is weak, rethink and generate a stronger candidate before returning JSON."""


def validate_candidate(c: dict, rows: list[dict]) -> None:
    required = {"theme", "pl", "en", "candidates_considered", "scores", "silence_test", "editor_note"}
    if set(c) != required:
        raise ValueError(f"candidate keys mismatch: {set(c)}")
    record = {"date": today(), **c}
    current = {"date": today(), "author": "AXIOM", "brand": "BriefRooms", "pl": c["pl"], "en": c["en"]}
    # Reuse the production guard before touching files.
    sys.path.insert(0, str(ROOT / "scripts"))
    from axiom_thought_guard import validate
    errors = validate(current, rows + [record])
    if errors:
        raise ValueError("; ".join(errors))


def main() -> int:
    rows = history_rows()
    stamp = today()
    if rows and rows[-1].get("date") == stamp:
        print(f"AXIOM Thought: already published for {stamp}")
        return 0

    prompt = build_prompt(rows)
    errors: list[str] = []
    candidate = None
    for attempt in range(1, 5):
        model = MODEL if attempt <= 3 else FALLBACK_MODEL
        try:
            candidate = call_gemini(prompt + f"\n\nAttempt {attempt}: be stricter than before.", model)
            validate_candidate(candidate, rows)
            break
        except Exception as exc:
            errors.append(f"attempt {attempt}/{model}: {exc}")
            candidate = None
            time.sleep(3)

    if candidate is None:
        print("AXIOM Thought: no candidate passed quality gate; publishing nothing.", file=sys.stderr)
        for error in errors:
            print(" - " + error, file=sys.stderr)
        return 1

    record = {"date": stamp, **candidate}
    current = {"date": stamp, "author": "AXIOM", "brand": "BriefRooms", "pl": candidate["pl"], "en": candidate["en"]}
    HISTORY.write_text(HISTORY.read_text(encoding="utf-8").rstrip() + "\n" + json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    CURRENT.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = subprocess.run([sys.executable, str(GUARD)], cwd=ROOT)
    if result.returncode:
        raise RuntimeError("post-write AXIOM Thought Guard failed")
    print(f"AXIOM Thought: published {stamp} theme={candidate['theme']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
