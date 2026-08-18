#!/usr/bin/env python3
"""Run the YouTube thumbnail UI patcher with support for both triple-quote styles."""
from pathlib import Path
import importlib.util
import re

ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / "scripts" / "apply_youtube_thumbnail_ui.py"

spec = importlib.util.spec_from_file_location("briefrooms_youtube_thumbnail_ui", PATCHER)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def replace_triple_assignment(text: str, name: str, value: str) -> str:
    pattern = re.compile(
        rf"{re.escape(name)}\s*=\s*(?:'''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\")",
        re.S,
    )
    if not pattern.search(text):
        raise RuntimeError(f"Missing assignment {name}")
    return pattern.sub(lambda _: f"{name}='''{value}'''", text, count=1)


module.replace_triple_assignment = replace_triple_assignment
module.main()
