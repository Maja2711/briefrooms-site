#!/usr/bin/env python3
"""Retired one-off W31 migration.

The former implementation mutated the current per-instrument projection every
15 minutes. After a same-week re-entry, that reapplied a historical stop event
to a different execution leg. The workflow no longer calls this file; it remains
as an explicit tombstone so the unsafe migration cannot be reintroduced by
accident or run manually.
"""
from __future__ import annotations

import json


def main() -> None:
    print(json.dumps({
        "status": "retired",
        "changed": False,
        "reason": "one_off_W31_correction_must_not_mutate_current_projection",
    }))


if __name__ == "__main__":
    main()
