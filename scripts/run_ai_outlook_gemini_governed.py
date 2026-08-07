#!/usr/bin/env python3
"""Launch the canonical Gemini publisher with the strict PL candidate contract."""
from __future__ import annotations

import sys

import sitecustomize  # noqa: F401 - activates Gemini transport adapter
from ai_outlook_candidate_contract_patch import install

install()

import publish_ai_outlook_gemini as publisher  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(publisher.main())
