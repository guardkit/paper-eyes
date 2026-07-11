"""The drop-folder watch/emit surface (design spec §3, §6 Stage 4).

Paper Eyes lives *outside* deckhand: it watches a ``drop/`` directory for scanned PDFs, runs the
pipeline, and drops a human-legible report (plus an inert structured sidecar) into a deckhand
agent's ``inbox/``. deckhand's own watch daemon, review UI, ledger, and trust engine then do what
they already do — Paper Eyes changes nothing inside deckhand and imports zero deckhand code. The
seam is pure filesystem: an atomic, never-overwrite file drop.

Two modules:

- :mod:`papereyes.watch.emit` — the atomic, never-overwrite emit of the ``.extraction.json`` sidecar
  (first) and the ``<stem>--<scanSha8>--fp<ver>.txt`` report (the trigger, second).
- :mod:`papereyes.watch.daemon` — the poll loop: sha-keyed ``processed.jsonl`` idempotency, the
  ``processed/`` / ``failed/`` trees, and the per-scan processing that ties pipeline to emit.
"""

from __future__ import annotations

from papereyes.watch.daemon import (
    ProcessedLog,
    ScanOutcome,
    process_scan,
    run_watch_cycle,
    watch_forever,
)
from papereyes.watch.emit import EmitOutcome, atomic_emit

__all__ = [
    "EmitOutcome",
    "ProcessedLog",
    "ScanOutcome",
    "atomic_emit",
    "process_scan",
    "run_watch_cycle",
    "watch_forever",
]
