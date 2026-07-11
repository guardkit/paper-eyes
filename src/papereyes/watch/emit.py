"""Atomic, never-overwrite emit into a deckhand agent's ``inbox/`` (design spec §3.1).

The seam deckhand's watch daemon reads (verified against ``deckhand`` ``watch/service.py``):

- ``read_inbox`` picks up **only** ``inbox/*.txt`` and ``inbox/*.pdf``. Any other suffix is inert by
  construction — so a ``.extraction.json`` sidecar and a ``.part`` temp file are invisible to the
  daemon and can never be read torn.
- The daemon keys idempotency off ``stable_doc_key(name, content) = sha256(name + NUL + content)``,
  so the *bytes* of the report matter: a byte-identical re-emit proposes the same doc_key once.

Two binding design consequences this module implements (design spec §3.1):

1. **Emit is atomic for BOTH files** — each is written to a ``.part`` sibling, fsynced, then
   ``os.replace``d into place. ``.part`` is suffix-filtered out of the daemon's glob, so neither
   file is ever visible half-written.
2. **Emit order is sidecar-first, then the report** — the ``.extraction.json`` is guaranteed present
   before the ``.txt`` (the trigger) exists, so the downstream lane that reads the sidecar
   post-approval can never race a missing sidecar.

**Never overwrite an existing report with differing bytes** (design spec §3.1 #4). The emitted name
carries the scan sha and the formpack version, so a recalibration emits a NEW name rather than
replacing one in place. If the exact name already exists with *differing* bytes anyway (e.g. a model
change altered output for the same scan+formpack), :func:`atomic_emit` reports a ``collision`` and
writes nothing — the caller routes the source scan to ``failed/`` rather than let the review UI
re-read a report whose bytes no longer match the pending proposal. A byte-identical re-emit is
idempotent (``identical``): the report is already present, nothing is rewritten.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

__all__ = ["EmitOutcome", "EmitStatus", "atomic_emit", "extraction_bytes", "report_bytes"]

EmitStatus = Literal["emitted", "identical", "collision"]

# The report is the trigger deckhand's glob picks up; the sidecar rides beside it, inert.
REPORT_SUFFIX = ".txt"
SIDECAR_SUFFIX = ".extraction.json"
PART_SUFFIX = ".part"


@dataclass(frozen=True)
class EmitOutcome:
    """The result of one emit into an inbox.

    ``status`` is ``emitted`` (both files newly written), ``identical`` (the report already exists
    byte-for-byte — an idempotent re-emit, nothing rewritten), or ``collision`` (the name exists
    with DIFFERING bytes — nothing written, the caller routes the scan to ``failed/``, never
    overwriting).
    """

    status: EmitStatus
    report_path: Path
    sidecar_path: Path
    detail: str = ""


def report_bytes(report_text: str) -> bytes:
    """The exact bytes the report file carries (the report text already ends with a newline)."""
    return report_text.encode("utf-8")


def extraction_bytes(extraction: dict[str, Any]) -> bytes:
    """The exact bytes the ``.extraction.json`` sidecar carries — sorted keys, trailing newline.

    Deterministic serialisation so a byte-identical run yields a byte-identical sidecar (the same
    determinism the report holds), and so an ``identical`` re-emit is detectable.
    """
    return (json.dumps(extraction, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically: a ``.part`` sibling, fsynced, then ``os.replace``.

    The ``.part`` suffix is filtered out of deckhand's ``inbox/*.txt|*.pdf`` glob, so the temp file
    is never picked up mid-write; ``os.replace`` is atomic on the same filesystem, so a reader sees
    either the whole previous file or the whole new one, never a torn one.
    """
    tmp = path.with_name(path.name + PART_SUFFIX)
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def atomic_emit(
    inbox: str | Path,
    report_name: str,
    report_text: str,
    extraction: dict[str, Any],
) -> EmitOutcome:
    """Atomically emit the sidecar ``<report_name>.extraction.json`` first, then ``.txt`` (trigger).

    Returns an :class:`EmitOutcome`. On ``collision`` (the ``.txt`` already exists with differing
    bytes) NOTHING is written — the never-overwrite rule (design spec §3.1 #4) — and the caller
    routes the scan to ``failed/``. On ``identical`` the report is already present byte-for-byte;
    the sidecar is (re)written only if missing or differing, so a re-emit repairs a lost sidecar
    without touching the trigger the daemon already keyed on.
    """
    inbox_dir = Path(inbox)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    report_path = inbox_dir / f"{report_name}{REPORT_SUFFIX}"
    sidecar_path = inbox_dir / f"{report_name}{SIDECAR_SUFFIX}"

    new_report = report_bytes(report_text)
    new_sidecar = extraction_bytes(extraction)

    if report_path.exists():
        existing = report_path.read_bytes()
        if existing != new_report:
            return EmitOutcome(
                status="collision",
                report_path=report_path,
                sidecar_path=sidecar_path,
                detail=(
                    f"{report_path.name} already exists with differing bytes "
                    f"({len(existing)} vs {len(new_report)}) — refusing to overwrite (§3.1 #4)"
                ),
            )
        # Byte-identical re-emit: keep the trigger the daemon already keyed on; only repair the
        # sidecar if it drifted or was lost (never leave the inbox with a stale/absent sidecar).
        if not sidecar_path.exists() or sidecar_path.read_bytes() != new_sidecar:
            _atomic_write(sidecar_path, new_sidecar)
        return EmitOutcome(
            status="identical", report_path=report_path, sidecar_path=sidecar_path
        )

    # Fresh emit — sidecar FIRST (guaranteed present before any proposal exists), then the trigger.
    _atomic_write(sidecar_path, new_sidecar)
    _atomic_write(report_path, new_report)
    return EmitOutcome(status="emitted", report_path=report_path, sidecar_path=sidecar_path)
