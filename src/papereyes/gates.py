"""Repo hygiene gates: the client-leakage deny-list and the honesty grep-lint.

Two mechanical guards, both driven by pattern files under ``ci/`` (design spec §6 Stage 0,
master plan §7):

- **leakage** — fails on any engagement-derived token (from the reference client pipeline)
  appearing in any scanned tree (``src/``, ``formpacks/``, ``docs/``, ``tests/``, ``examples/``,
  ``demo/``). Every shipped calibration value must be derived fresh on public forms; this catches
  a paste or transliteration — including into the shipped example agent and the demo scripts.
- **honesty** — fails on the banned overclaim phrases enumerated in
  ``ci/honesty_denylist.txt`` (over-strong safety and correctness assertions the project
  has committed never to make).

The pattern files live under ``ci/`` (never a scanned root) so the guard can name what it
bans without the tokens appearing in scanned source. Each is excluded from its own scan.
This module reads the tokens; it never contains them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

LEAKAGE_DENYLIST = REPO_ROOT / "ci" / "leakage_denylist.txt"
HONESTY_DENYLIST = REPO_ROOT / "ci" / "honesty_denylist.txt"

# The trees the leakage gate scans (design spec §6 Stage 0), widened at Stage 4 to include the
# shipped example agent (``examples/``) and the demo scripts (``demo/``) — both are filmable
# surfaces the honesty + leakage laws must cover. The honesty gate scans the same trees plus the
# top-level README. Non-existent roots are skipped, so this list is safe before those dirs land.
SCANNED_DIRS = ("src", "formpacks", "docs", "tests", "examples", "demo")

# File suffixes worth scanning — text the build ships. Binary assets are skipped.
_TEXT_SUFFIXES = {
    ".py", ".yaml", ".yml", ".json", ".toml", ".txt", ".md", ".cfg", ".ini", ".sh",
}


@dataclass(frozen=True)
class Hit:
    """One deny-list match: the file, 1-indexed line, the pattern label, and the line text."""

    path: Path
    line_no: int
    label: str
    line_text: str

    def render(self, root: Path) -> str:
        rel = self.path.relative_to(root)
        return f"{rel}:{self.line_no}: matched {self.label!r} :: {self.line_text.strip()}"


def load_patterns(denylist_path: str | Path) -> list[tuple[str, re.Pattern[str]]]:
    """Parse a deny-list file into ``(label, compiled_regex)`` pairs.

    Each non-blank, non-``#`` line is a case-insensitive regex. Blank lines and lines whose
    first non-space character is ``#`` are ignored.
    """
    patterns: list[tuple[str, re.Pattern[str]]] = []
    text = Path(denylist_path).read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append((line, re.compile(line, re.IGNORECASE)))
    return patterns


def _iter_text_files(roots: list[Path], exclude: set[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            if path.resolve() in exclude:
                continue
            files.append(path)
    return files


def scan(
    roots: list[Path],
    patterns: list[tuple[str, re.Pattern[str]]],
    *,
    exclude: set[Path] | None = None,
) -> list[Hit]:
    """Scan every text file under ``roots`` for any of ``patterns``; return all hits."""
    excluded = {p.resolve() for p in (exclude or set())}
    hits: list[Hit] = []
    for path in _iter_text_files(roots, excluded):
        content = path.read_text(encoding="utf-8", errors="replace")
        for line_no, line_text in enumerate(content.splitlines(), start=1):
            for label, pattern in patterns:
                if pattern.search(line_text):
                    hits.append(Hit(path, line_no, label, line_text))
    return hits


def scan_leakage(repo_root: Path | None = None) -> list[Hit]:
    """Run the client-leakage deny-list over the four scanned trees."""
    root = repo_root or REPO_ROOT
    roots = [root / d for d in SCANNED_DIRS]
    patterns = load_patterns(LEAKAGE_DENYLIST)
    return scan(roots, patterns, exclude={LEAKAGE_DENYLIST, HONESTY_DENYLIST})


def scan_honesty(repo_root: Path | None = None) -> list[Hit]:
    """Run the honesty banned-phrase deny-list over the scanned trees plus README."""
    root = repo_root or REPO_ROOT
    roots = [root / d for d in SCANNED_DIRS]
    patterns = load_patterns(HONESTY_DENYLIST)
    hits = scan(roots, patterns, exclude={LEAKAGE_DENYLIST, HONESTY_DENYLIST})
    readme = root / "README.md"
    if readme.is_file():
        hits += scan([readme], patterns, exclude={LEAKAGE_DENYLIST, HONESTY_DENYLIST})
    return hits


def run_gates(repo_root: Path | None = None) -> int:
    """Run both gates and print a verdict. Returns 0 when clean, 1 on any hit.

    This is the standalone leakage/honesty gate — the one CI runs as a required step before
    anything else, and the same one that runs inside the pytest suite.
    """
    root = repo_root or REPO_ROOT
    import sys

    exit_code = 0
    for name, hits in (("leakage", scan_leakage(root)), ("honesty", scan_honesty(root))):
        if hits:
            exit_code = 1
            print(f"{name} gate: FAIL ({len(hits)} hit(s))", file=sys.stderr)
            for hit in hits:
                print(f"  {hit.render(root)}", file=sys.stderr)
        else:
            print(f"{name} gate: OK")
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_gates())
