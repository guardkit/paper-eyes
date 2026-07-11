"""The drop-folder watch daemon (design spec §3, §4.2, §6 Stage 4).

Watch a ``drop/`` directory for scanned PDFs; for each new scan run the pipeline and atomically emit
the report + sidecar into the configured deckhand agent ``inbox/`` (:mod:`papereyes.watch.emit`).
The daemon is **idempotent off a sha-keyed ``processed.jsonl``**: the same scan bytes dropped twice
are processed once (design spec §3.1). Successful scans move to ``drop/processed/``; failures move
to ``drop/failed/`` with a ``<name>.error.txt`` note. ``processed.jsonl`` lives in the paper-eyes
workdir (a plain receipt log — never described as tamper-evident; that property belongs to
deckhand's ledger alone).

Polling, not ``inotify`` — no extra dependency, and a poll is enough for a drop-folder (the same
choice deckhand's own daemon makes). Every model call the pipeline issues goes through the injected
:class:`~papereyes.pipeline.client.ModelClient`, so the whole daemon runs hermetically in tests
with a deterministic stub and no endpoint.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from papereyes.config.models import Formpack, Pipeline
from papereyes.errors import PaperEyesError
from papereyes.pipeline.client import ModelClient
from papereyes.pipeline.run import RunResult, run_pipeline
from papereyes.watch.emit import atomic_emit

__all__ = [
    "DEFAULT_POLL_SECONDS",
    "CycleResult",
    "ProcessedLog",
    "ScanOutcome",
    "WatchContext",
    "process_scan",
    "run_watch_cycle",
    "watch_forever",
]

log = logging.getLogger("papereyes.watch")

DEFAULT_POLL_SECONDS = 2.0

PROCESSED_DIRNAME = "processed"
FAILED_DIRNAME = "failed"
PROCESSED_LOG_NAME = "processed.jsonl"

ScanStatus = Literal["emitted", "identical", "skipped", "collision", "failed"]


@dataclass(frozen=True)
class ScanOutcome:
    """What happened to one scan in a watch cycle (all idempotent, sha-keyed)."""

    scan_name: str
    scan_sha256: str
    status: ScanStatus
    report_name: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class CycleResult:
    """The outcomes of one poll over the drop directory."""

    outcomes: list[ScanOutcome] = field(default_factory=list)

    def emitted(self) -> int:
        return sum(o.status == "emitted" for o in self.outcomes)

    def skipped(self) -> int:
        return sum(o.status in ("skipped", "identical") for o in self.outcomes)

    def failed(self) -> int:
        return sum(o.status in ("failed", "collision") for o in self.outcomes)


class ProcessedLog:
    """The sha-keyed ``processed.jsonl`` idempotency log — a plain append-only receipt file.

    Keyed on the scan's sha256: the same bytes dropped twice are recognised and skipped. It is NOT
    a tamper-evident ledger (deckhand owns that property); it is a plain receipt of what the daemon
    has already emitted, so a restart never re-emits a report the review UI may have proposed on.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def seen_shas(self) -> set[str]:
        """Every scan sha already recorded (idempotency key)."""
        if not self._path.is_file():
            return set()
        shas: set[str] = set()
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                shas.add(str(json.loads(line)["scan_sha256"]))
            except (json.JSONDecodeError, KeyError):
                continue
        return shas

    def record(self, entry: dict[str, Any]) -> None:
        """Append one processed-scan receipt line (write + fsync so a restart sees it)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, sort_keys=True) + "\n"
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())


@dataclass
class WatchContext:
    """Everything one watch loop needs to process the scans it finds in ``drop_dir``.

    The pipeline pieces (``formpack`` + ``formpack_dir`` + ``pipeline_cfg`` + ``client``) plus the
    resolved directories: where scans arrive (``drop_dir``), where reports go (``inbox_dir``), and
    the paper-eyes workdir (pipeline crops/provenance + ``processed.jsonl``). ``processed/`` and
    ``failed/`` are the archive trees under ``drop_dir``.
    """

    formpack: Formpack
    formpack_dir: Path
    pipeline_cfg: Pipeline
    client: ModelClient
    drop_dir: Path
    inbox_dir: Path
    workdir: Path
    region_model: str | None = None

    @property
    def processed_dir(self) -> Path:
        return self.drop_dir / PROCESSED_DIRNAME

    @property
    def failed_dir(self) -> Path:
        return self.drop_dir / FAILED_DIRNAME

    @property
    def processed_log(self) -> ProcessedLog:
        return ProcessedLog(self.workdir / PROCESSED_LOG_NAME)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _archive(scan: Path, dest_dir: Path) -> Path:
    """Move a handled scan out of ``drop/`` into ``dest_dir`` (``processed/`` or ``failed/``).

    A re-drop whose destination already holds a byte-identical file is de-duplicated (the source is
    unlinked); a name clash with *differing* bytes keeps both by suffixing the source's sha8, so a
    scan is never silently lost or overwritten.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / scan.name
    if dest.exists():
        if dest.read_bytes() == scan.read_bytes():
            scan.unlink()
            return dest
        dest = dest_dir / f"{scan.stem}--{_sha256_file(scan)[:8]}{scan.suffix}"
    shutil.move(str(scan), str(dest))
    return dest


def _fail(scan: Path, failed_dir: Path, reason: str) -> Path:
    """Route a scan to ``failed/`` with a legible ``<name>.error.txt`` note beside it."""
    dest = _archive(scan, failed_dir)
    (failed_dir / f"{dest.name}.error.txt").write_text(
        f"papereyes: {scan.name} was not emitted.\n\nreason:\n{reason}\n", encoding="utf-8"
    )
    return dest


def process_scan(ctx: WatchContext, scan_path: str | Path, *, force: bool = False) -> ScanOutcome:
    """Process one scan sitting in ``drop/``: pipeline -> atomic emit -> archive/record.

    Idempotent: a scan whose sha is already in ``processed.jsonl`` is skipped (its drop copy
    archived so the loop stops re-seeing it) unless ``force`` re-runs it. A pipeline failure or an
    emit ``collision`` (a same-named report already present with differing bytes, §3.1 #4) routes
    the scan to ``failed/`` rather than overwrite. A byte-identical re-emit is ``identical``.
    """
    scan = Path(scan_path)
    sha = _sha256_file(scan)

    if not force and sha in ctx.processed_log.seen_shas():
        _archive(scan, ctx.processed_dir)
        return ScanOutcome(scan.name, sha, "skipped", detail="sha already in processed.jsonl")

    try:
        result: RunResult = run_pipeline(
            scan,
            ctx.formpack,
            ctx.formpack_dir,
            ctx.pipeline_cfg,
            ctx.client,
            workdir=ctx.workdir / scan.stem,
            region_model=ctx.region_model,
        )
    except (PaperEyesError, OSError) as exc:
        _fail(scan, ctx.failed_dir, str(exc))
        return ScanOutcome(scan.name, sha, "failed", detail=str(exc))

    outcome = atomic_emit(
        ctx.inbox_dir, result.report_name, result.report_text, result.extraction
    )
    if outcome.status == "collision":
        _fail(scan, ctx.failed_dir, outcome.detail)
        return ScanOutcome(scan.name, sha, "collision", result.report_name, outcome.detail)

    _archive(scan, ctx.processed_dir)
    ctx.processed_log.record(
        {
            "scan_sha256": sha,
            "scan_name": scan.name,
            "report_name": result.report_name,
            "emit": outcome.status,
            "formpack": ctx.formpack.slug(),
        }
    )
    return ScanOutcome(scan.name, sha, outcome.status, result.report_name)


def _scans_in(drop_dir: Path) -> list[Path]:
    """Top-level ``*.pdf`` scans awaiting processing (``processed/`` / ``failed/`` are skipped)."""
    if not drop_dir.is_dir():
        return []
    return sorted(p for p in drop_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")


def run_watch_cycle(ctx: WatchContext, *, force: bool = False) -> CycleResult:
    """Process every scan currently in ``drop/`` once. Returns the per-scan outcomes."""
    ctx.drop_dir.mkdir(parents=True, exist_ok=True)
    ctx.inbox_dir.mkdir(parents=True, exist_ok=True)
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    outcomes = [process_scan(ctx, scan, force=force) for scan in _scans_in(ctx.drop_dir)]
    return CycleResult(outcomes=outcomes)


def watch_forever(
    ctx: WatchContext,
    *,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    stop: threading.Event | None = None,
) -> None:  # pragma: no cover - the live loop; run_watch_cycle carries the tested behaviour
    """Poll ``drop/`` until ``stop`` is set (or forever). The container entry point.

    Each cycle is independent and idempotent (sha-keyed), so a slow filesystem, a mid-cycle crash,
    or a restart never re-emits a report. ``stop`` lets a signal handler end the loop cleanly.
    """
    stop = stop or threading.Event()
    log.info("WATCH: polling %s every %.1fs -> %s", ctx.drop_dir, poll_seconds, ctx.inbox_dir)
    while not stop.is_set():
        result = run_watch_cycle(ctx)
        if result.outcomes:
            log.info(
                "WATCH: cycle — %d emitted, %d skipped, %d failed",
                result.emitted(),
                result.skipped(),
                result.failed(),
            )
        stop.wait(poll_seconds)
