"""Config-is-data grep-lint (design spec §4; mirrors deckhand's own invariant).

``src/`` must never contain a code-evaluating deserialization or template pattern. Formpacks
and pipeline configs are loaded with ``yaml.safe_load`` and never executed; this test FAILS if
``yaml.load(``, ``pickle``, ``eval(`` or ``jinja2`` appears in the shipped source.
"""

from __future__ import annotations

import re
from pathlib import Path

FORBIDDEN = {
    "yaml.load(": re.compile(r"yaml\.load\("),
    "pickle": re.compile(r"pickle"),
    "eval(": re.compile(r"\beval\("),
    "jinja2": re.compile(r"jinja2"),
}

SRC = Path(__file__).resolve().parent.parent / "src"


def _iter_src_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_src_dir_exists() -> None:
    assert SRC.is_dir(), f"expected a src/ tree at {SRC}"
    assert _iter_src_files(), "expected at least one source file to scan"


def test_no_forbidden_patterns_in_src() -> None:
    hits: list[str] = []
    for path in _iter_src_files():
        text = path.read_text(encoding="utf-8")
        for name, pattern in FORBIDDEN.items():
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                hits.append(f"{path.relative_to(SRC)}:{line_no}: forbidden pattern {name!r}")
    assert not hits, "config-is-data grep-lint failed:\n" + "\n".join(hits)


def test_grep_lint_catches_a_planted_pattern(tmp_path: Path) -> None:
    planted = tmp_path / "evil.py"
    samples = {
        "yaml.load(": "data = yaml.load(text)",
        "pickle": "import pickle",
        "eval(": "eval('2+2')",
        "jinja2": "from jinja2 import Template",
    }
    for name, pattern in FORBIDDEN.items():
        planted.write_text(samples[name], encoding="utf-8")
        assert pattern.search(planted.read_text(encoding="utf-8")), (
            f"grep-lint pattern {name!r} failed to match its own sample"
        )
