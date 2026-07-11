"""The honesty grep-lint gate (master plan §7; design spec §9).

Mirrors the leakage test's shape: the repo is clean under the real banned-phrase list, the
scanner mechanism catches a planted phrase, and the real list bites known banned phrases
(decoded at runtime so the phrases never appear literally in scanned source).
"""

from __future__ import annotations

import base64
from pathlib import Path

from papereyes.gates import HONESTY_DENYLIST, load_patterns, scan, scan_honesty

# base64-encoded banned phrases, decoded at runtime (kept out of scanned source so the
# honesty gate does not flag its own test). Each decodes to one entry in the deny-list.
_ENCODED_BANNED = (
    "aW5qZWN0aW9uLXByb29m",
    "Y29tcGxpYW50",
    "dGFtcGVyLXByb29m",
    "emVyby1lZ3Jlc3M=",
    "c2VjdXJlIGJ5IGNvbnN0cnVjdGlvbg==",
)


def test_repo_is_honest() -> None:
    hits = scan_honesty()
    rendered = "\n".join(h.render(Path(__file__).resolve().parent.parent) for h in hits)
    assert not hits, f"honesty gate failed:\n{rendered}"


def test_scanner_reports_a_planted_phrase(tmp_path: Path) -> None:
    denylist = tmp_path / "honesty.txt"
    denylist.write_text("no[\\s_-]?marketing\n", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "claims.md").write_text("It is totally no-marketing safe.\n", encoding="utf-8")
    hits = scan([docs], load_patterns(denylist))
    assert len(hits) == 1
    assert hits[0].line_no == 1


def test_real_denylist_bites_known_phrases() -> None:
    patterns = load_patterns(HONESTY_DENYLIST)
    for encoded in _ENCODED_BANNED:
        phrase = base64.b64decode(encoded).decode("utf-8")
        assert any(rx.search(phrase) for _, rx in patterns), (
            "no honesty pattern matched a known banned phrase"
        )
