#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PL_MAP = ROOT / "docs" / "ARCHITECTURE_MAP_PL.md"
EN_MAP = ROOT / "docs" / "ARCHITECTURE_MAP_EN.md"
PL_POLICY = ROOT / "docs" / "ARCHITECTURE_DOCUMENTATION_POLICY_PL.md"
EN_POLICY = ROOT / "docs" / "ARCHITECTURE_DOCUMENTATION_POLICY_EN.md"
AGENTS = ROOT / "AGENTS.md"
README = ROOT / "README.md"

errors: list[str] = []

for path in (AGENTS, README, PL_MAP, EN_MAP, PL_POLICY, EN_POLICY):
    if not path.is_file():
        errors.append(f"missing required architecture-bootstrap file: {path.relative_to(ROOT)}")

if errors:
    print("\n".join(errors), file=sys.stderr)
    raise SystemExit(1)

agents = AGENTS.read_text(encoding="utf-8")
readme = README.read_text(encoding="utf-8")
pl_map = PL_MAP.read_text(encoding="utf-8")
en_map = EN_MAP.read_text(encoding="utf-8")
pl_policy = PL_POLICY.read_text(encoding="utf-8")
en_policy = EN_POLICY.read_text(encoding="utf-8")

for required in ("docs/ARCHITECTURE_MAP_PL.md", "docs/ARCHITECTURE_MAP_EN.md"):
    if required not in agents:
        errors.append(f"AGENTS.md must reference {required}")

if "AGENTS.md" not in readme or "docs/ARCHITECTURE_MAP_PL.md" not in readme:
    errors.append("README.md must point agents/developers to AGENTS.md and the canonical PL Architecture Map")

if "AGENTS.md" not in pl_policy:
    errors.append("PL architecture documentation policy must define the mandatory AGENTS.md bootstrap")
if "AGENTS.md" not in en_policy:
    errors.append("EN architecture documentation policy must define the mandatory AGENTS.md bootstrap")

if "AGENTS.md" not in pl_map:
    errors.append("PL Architecture Map must reference the mandatory AGENTS.md bootstrap")
if "AGENTS.md" not in en_map:
    errors.append("EN Architecture Map must reference the mandatory AGENTS.md bootstrap")

pl_match = re.search(r"\*\*Wersja mapy:\*\*\s*([^\s]+)", pl_map)
en_match = re.search(r"\*\*Map version:\*\*\s*([^\s]+)", en_map)

if not pl_match or not en_match:
    errors.append("could not parse PL/EN Architecture Map versions")
elif pl_match.group(1) != en_match.group(1):
    errors.append(
        f"Architecture Map version drift: PL={pl_match.group(1)} EN={en_match.group(1)}"
    )

if errors:
    print("Architecture Bootstrap Guard FAILED:", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    raise SystemExit(1)

print(f"Architecture Bootstrap Guard passed (map version {pl_match.group(1)}).")
