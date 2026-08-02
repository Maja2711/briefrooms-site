#!/usr/bin/env python3
"""Load the verified AI Tournament engine implementation from immutable parts."""
from __future__ import annotations

import hashlib
from pathlib import Path

_PARTS_DIR = Path(__file__).with_name("ai_tournament_parts")
_PARTS = sorted(_PARTS_DIR.glob("engine.part*"))
_EXPECTED_COUNT = 7
_EXPECTED_SHA256 = "87485c5666286f0cc559aa39131e5869ebc6b76e65764ca41f5e358f09815310"

if len(_PARTS) != _EXPECTED_COUNT:
    raise RuntimeError(f"AI Tournament engine requires {_EXPECTED_COUNT} verified parts; found {len(_PARTS)}")

_SOURCE = "".join(path.read_text(encoding="utf-8") for path in _PARTS)
_ACTUAL_SHA256 = hashlib.sha256(_SOURCE.encode("utf-8")).hexdigest()
if _ACTUAL_SHA256 != _EXPECTED_SHA256:
    raise RuntimeError("AI Tournament engine integrity check failed")

exec(compile(_SOURCE, str(_PARTS_DIR / "assembled_engine.py"), "exec"), globals(), globals())
