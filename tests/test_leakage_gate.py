"""The client-leakage deny-list gate (design spec §6 Stage 0; master plan §7).

Three checks:
1. the real repo is clean under the real deny-list (the required-before-push gate);
2. the scanner mechanism reports a planted match with file + line;
3. the real deny-list actually bites known-bad tokens (decoded at runtime from base64, so no
   confidential identity string ever appears in scanned source).
"""

from __future__ import annotations

import base64
from pathlib import Path

from papereyes.gates import LEAKAGE_DENYLIST, load_patterns, scan, scan_leakage

# base64-encoded known-bad tokens, decoded at runtime. The literals are kept out of scanned
# source on purpose; base64 text does not match any deny-list pattern.
_ENCODED_BAD = (
    "ZmlucHJveHk=",  # obfuscated brand pattern
    "YXNod29ydGg=",  # obfuscated surname pattern
    "T1BH",          # readable public-domain pattern
    "c21va2UtNA==",  # smoke-persona regex
    "ZG9ub3I=",      # readable legal-role term
)


def test_repo_is_leakage_clean() -> None:
    hits = scan_leakage()
    rendered = "\n".join(h.render(Path(__file__).resolve().parent.parent) for h in hits)
    assert not hits, f"client-leakage gate failed:\n{rendered}"


def test_denylist_parses_to_patterns() -> None:
    patterns = load_patterns(LEAKAGE_DENYLIST)
    assert len(patterns) >= 8


def test_scanner_reports_a_planted_pattern(tmp_path: Path) -> None:
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("# a test deny-list\nPLANTED_LEAK_TOKEN\n", encoding="utf-8")
    target_dir = tmp_path / "src"
    target_dir.mkdir()
    (target_dir / "leaky.py").write_text(
        "x = 1\ny = 'this line has a PLANTED_LEAK_TOKEN in it'\n", encoding="utf-8"
    )
    hits = scan([target_dir], load_patterns(denylist))
    assert len(hits) == 1
    assert hits[0].line_no == 2
    assert hits[0].label == "PLANTED_LEAK_TOKEN"


def test_real_denylist_bites_known_tokens() -> None:
    patterns = load_patterns(LEAKAGE_DENYLIST)
    for encoded in _ENCODED_BAD:
        token = base64.b64decode(encoded).decode("utf-8")
        assert any(rx.search(token) for _, rx in patterns), (
            "no leakage pattern matched a known-bad token shape"
        )
