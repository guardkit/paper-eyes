"""The atomic, never-overwrite emit (design spec §3.1) — hermetic, no pipeline, no models.

Proves the four binding emit properties directly against :mod:`papereyes.watch.emit`:

1. both files land, byte-correct, with the report as the trigger and the ``.extraction.json`` as an
   inert sidecar (deckhand's daemon globs only ``*.txt`` / ``*.pdf``);
2. the sidecar is written BEFORE the report (order asserted by spying on ``os.replace``);
3. no ``.part`` temp file is ever left behind;
4. a same-named report with DIFFERING bytes is a ``collision`` — nothing is overwritten (§3.1 #4) —
   while a byte-identical re-emit is ``identical`` and repairs a lost sidecar.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from papereyes.watch.emit import atomic_emit, extraction_bytes, report_bytes

REPORT = "CH2 Child Benefit claim — extracted from scan persona-01.pdf\nClaimant: X\n"
EXTRACTION: dict[str, Any] = {"claimant": {"nino": "BN605990B"}, "children": [{"full_name": "Y"}]}
NAME = "persona-01--ab12cd34--fp1"


def test_emit_lands_both_files_byte_correct(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    outcome = atomic_emit(inbox, NAME, REPORT, EXTRACTION)

    assert outcome.status == "emitted"
    report = inbox / f"{NAME}.txt"
    sidecar = inbox / f"{NAME}.extraction.json"
    assert report.read_bytes() == report_bytes(REPORT)
    assert sidecar.read_bytes() == extraction_bytes(EXTRACTION)
    # the only *.txt / *.pdf file (deckhand's trigger glob) is the report — the sidecar is inert
    triggers = sorted(p.name for p in inbox.iterdir() if p.suffix.lower() in (".txt", ".pdf"))
    assert triggers == [f"{NAME}.txt"]
    # nothing left half-written
    assert not any(p.name.endswith(".part") for p in inbox.iterdir())


def test_emit_writes_sidecar_before_report(tmp_path: Path, monkeypatch: Any) -> None:
    inbox = tmp_path / "inbox"
    replaced: list[str] = []
    real_replace = os.replace

    def spy(src: Any, dst: Any) -> None:
        replaced.append(Path(dst).name)
        real_replace(src, dst)

    monkeypatch.setattr("papereyes.watch.emit.os.replace", spy)
    atomic_emit(inbox, NAME, REPORT, EXTRACTION)

    # sidecar (.extraction.json) is committed before the .txt trigger (§3.1 #2)
    assert replaced == [f"{NAME}.extraction.json", f"{NAME}.txt"]


def test_reemit_identical_is_idempotent_and_repairs_sidecar(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    atomic_emit(inbox, NAME, REPORT, EXTRACTION)
    sidecar = inbox / f"{NAME}.extraction.json"
    sidecar.unlink()  # simulate a lost sidecar

    outcome = atomic_emit(inbox, NAME, REPORT, EXTRACTION)

    assert outcome.status == "identical"
    assert sidecar.read_bytes() == extraction_bytes(EXTRACTION)  # repaired without a new report
    assert (inbox / f"{NAME}.txt").read_bytes() == report_bytes(REPORT)


def test_never_overwrite_report_with_differing_bytes(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    atomic_emit(inbox, NAME, REPORT, EXTRACTION)
    report = inbox / f"{NAME}.txt"
    before = report.read_bytes()

    # a differing report for the SAME name (e.g. a model changed output for the same scan+formpack)
    outcome = atomic_emit(inbox, NAME, REPORT + "TAMPERED\n", EXTRACTION)

    assert outcome.status == "collision"
    assert report.read_bytes() == before  # untouched — never overwritten (§3.1 #4)
    assert not any(p.name.endswith(".part") for p in inbox.iterdir())
