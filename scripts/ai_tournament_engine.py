#!/usr/bin/env python3
"""Load the versioned AI Tournament implementation from repository parts."""
from __future__ import annotations

import hashlib
from pathlib import Path

_PARTS_DIR = Path(__file__).with_name("ai_tournament_parts")
_PARTS = sorted(_PARTS_DIR.glob("engine.part*"))
_EXPECTED_COUNT = 7

if len(_PARTS) != _EXPECTED_COUNT:
    raise RuntimeError(f"AI Tournament engine requires {_EXPECTED_COUNT} versioned parts; found {len(_PARTS)}")

_SOURCE = "".join(path.read_text(encoding="utf-8") for path in _PARTS)
ENGINE_SOURCE_SHA256 = hashlib.sha256(_SOURCE.encode("utf-8")).hexdigest()

# The assembled legacy source ends with its own __main__ call. Install the
# locked manual-submission adapter before that call so both imports and direct
# CLI execution use the production manual tournament contract.
_MAIN_MARKER = '\nif __name__ == "__main__":\n    raise SystemExit(main())'
_INSTALL = (
    '\nfrom ai_tournament_manual_mode import install as _install_manual_tournament_mode\n'
    '_install_manual_tournament_mode(globals())\n'
)
if _MAIN_MARKER not in _SOURCE:
    raise RuntimeError("AI Tournament engine main marker is missing")
_SOURCE = _SOURCE.replace(_MAIN_MARKER, _INSTALL + _MAIN_MARKER, 1)

exec(compile(_SOURCE, str(_PARTS_DIR / "assembled_engine.py"), "exec"), globals(), globals())
