"""Regressions for concise, affirmative Chinese learning guidance.

User-facing help starts with the function and the next safe action. These tests
check content consistency and guard against the disfavoured contrast pattern.
"""
from __future__ import annotations

from html import escape
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CATALOG_FOLDER = ROOT / ("studio" if (ROOT / "studio").is_dir() else "rcstudio") / "learning_support"
FORBIDDEN_CONTRAST = re.compile(r"不是|并非|而不是|不等于|不代表|不要")


def _all_text(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _all_text(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_text(child)
    elif isinstance(value, str):
        yield value


def test_learning_copy_uses_direct_explanations():
    """Teaching text should provide the correct action instead of a negative contrast."""
    for name in ("catalog.json", "courses.json"):
        source = json.loads((CATALOG_FOLDER / name).read_text(encoding="utf-8"))
        for sentence in _all_text(source):
            assert not FORBIDDEN_CONTRAST.search(sentence), (name, sentence)


def test_readable_manuals_follow_the_same_rule():
    for path in [
        ROOT / "LEARNING_GUIDE_CN.md",
        ROOT / "docs" / "CONTROL_HELP_CN.md",
        ROOT / "docs" / "Studios-Learning-Guide-CN.html",
        ROOT / "COPYEDIT_NOTES_CN.md",
    ]:
        content = path.read_text(encoding="utf-8")
        assert not FORBIDDEN_CONTRAST.search(content), str(path)


def test_generated_help_documents_match_runtime_catalog():
    entries = json.loads((CATALOG_FOLDER / "catalog.json").read_text(encoding="utf-8"))["entries"]
    manual = (ROOT / "docs" / "CONTROL_HELP_CN.md").read_text(encoding="utf-8")
    webpage = (ROOT / "docs" / "Studios-Learning-Guide-CN.html").read_text(encoding="utf-8")
    fields = ("title", "summary", "explanation", "example", "pitfalls", "risk", "scope", "source")
    for key, entry in entries.items():
        for text in [*(entry.get(f) for f in fields), *entry.get("steps", [])]:
            if text:
                assert text in manual, (key, "manual", text)
                assert escape(text, quote=True) in webpage, (key, "webpage", text)
