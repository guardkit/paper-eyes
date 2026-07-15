"""A bounded-resource wrapper around every poppler subprocess (THREAT-MODEL.md "crafted PDF").

The bytes Paper Eyes parses that it did not author are scanned PDFs, rendered by poppler
(``pdftoppm`` / ``pdftotext``). poppler is a C toolchain with a real history of parser bugs; a
crafted PDF that trips one could hang the parse or exhaust resources. Paper Eyes cannot patch a
poppler CVE, but it refuses to run the parser unbounded: every poppler call goes through this
wrapper, which applies — before ``exec`` on POSIX, so the caps bind the poppler process itself,
not Paper Eyes:

- **CPU time** — ``RLIMIT_CPU`` (a runaway parser is killed) *plus* a wall-clock ``timeout`` on
  the parent (a parser stuck in ``sleep`` / I/O is not caught by CPU time alone).
- **Address space** — ``RLIMIT_AS`` (a memory-bomb PDF cannot exhaust the box).
- **File size** — ``RLIMIT_FSIZE`` (a parser that spills a huge file is capped); ``pdftext`` to
  a ``stdout`` pipe is additionally truncated to a byte cap after read (a pipe is not a file, so
  ``RLIMIT_FSIZE`` does not bound it — see :func:`decode_capped`).

Provenance: this mirrors deckhand's ``src/deckhand/extract/pdftext.py`` (the same RLIMIT preexec
+ parent wall-clock timeout + fixed-argv-never-a-shell + loud-typed-error-never-swallowed
pattern). It is Paper Eyes' own code — deckhand is **not** imported — sized for Paper Eyes' two
poppler workloads: ``pdftotext`` (text to a stdout pipe) and ``pdftoppm`` (page rasters written
as PNG files into a per-scan workdir).

This is **software mitigation, not an OS sandbox** — the honest wording SECURITY.md /
THREAT-MODEL.md bind. A seccomp profile and a network-isolated namespace remain honestly
absent / planned. A cap breach (the wall-clock timeout, or ``RLIMIT_CPU`` / ``RLIMIT_AS`` /
``RLIMIT_FSIZE`` killing the child) raises the caller's typed error and is never swallowed; a
non-zero exit does too when the caller asks for it (``check=True``).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable

from papereyes.errors import PaperEyesError

# --- pdftotext: text to a stdout pipe — mirrors deckhand's text-extraction defaults ---
# The optional fetch path probes a blank form's licence page; the text is small.
PDFTOTEXT_TIMEOUT_S = 20.0
PDFTOTEXT_CPU_SECONDS = 10
PDFTOTEXT_ADDRESS_SPACE_BYTES = 512 * 1024 * 1024  # 512 MiB
PDFTOTEXT_MAX_OUTPUT_BYTES = 8 * 1024 * 1024  # 8 MiB — a pipe is not bounded by RLIMIT_FSIZE

# --- pdftoppm: page rasters written as PNG files — sized for image rendering, not text ---
# A page render is single-digit MB and takes seconds even at the pipeline's full DPI (identify
# 150 DPI / full ~200-300 DPI). These caps sit well above that real workload so a legitimate
# render never trips them, while still bounding a raster bomb: RLIMIT_FSIZE caps each PNG file
# pdftoppm writes, RLIMIT_AS bounds the render's memory, and the wall-clock/CPU caps bound a
# parser that hangs. (RLIMIT_FSIZE bounds each single file; multi-page output is many files,
# each individually capped — total disk is bounded by the mounted workdir.)
PDFTOPPM_TIMEOUT_S = 90.0
PDFTOPPM_CPU_SECONDS = 60
PDFTOPPM_ADDRESS_SPACE_BYTES = 1024 * 1024 * 1024  # 1 GiB
PDFTOPPM_FSIZE_BYTES = 256 * 1024 * 1024  # 256 MiB per PNG file pdftoppm writes


def _rlimit_preexec(
    *, cpu_seconds: int, address_space_bytes: int, fsize_bytes: int
) -> Callable[[], None]:
    """Build the child-side ``preexec_fn`` that applies the rlimit caps (POSIX only).

    Runs in the forked child after fork and before ``exec``, so the caps bind the poppler
    process itself, not Paper Eyes. A cap of ``0`` (or less) is treated as "do not set this
    limit", which keeps the caps individually testable with harmless stubs.
    """
    import resource

    def apply() -> None:
        if cpu_seconds > 0:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        if address_space_bytes > 0:
            resource.setrlimit(resource.RLIMIT_AS, (address_space_bytes, address_space_bytes))
        if fsize_bytes > 0:
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_bytes, fsize_bytes))

    return apply


def run_bounded(
    argv: list[str],
    *,
    error_cls: type[PaperEyesError],
    what: str,
    timeout_s: float,
    cpu_seconds: int,
    address_space_bytes: int,
    fsize_bytes: int,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run ``argv`` as a bounded subprocess: rlimit caps + wall-clock timeout, never a shell.

    ``argv`` is a fixed list (its first element already resolved by the caller) — nothing from a
    document is ever spliced into a shell line. On POSIX the CPU / address-space / file-size caps
    are applied in the child before ``exec``; the parent additionally enforces ``timeout_s`` (a
    parser stuck in ``sleep`` / I/O is not caught by CPU time alone). A wall-clock timeout is
    ALWAYS loud (it is a cap breach, not a normal exit) and raises ``error_cls``; ``check``
    additionally makes a non-zero exit raise ``error_cls``. ``stdout`` / ``stderr`` are captured
    as bytes on the returned :class:`subprocess.CompletedProcess`.
    """
    preexec = (
        _rlimit_preexec(
            cpu_seconds=cpu_seconds,
            address_space_bytes=address_space_bytes,
            fsize_bytes=fsize_bytes,
        )
        if os.name == "posix"
        else None
    )
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout_s,
            preexec_fn=preexec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise error_cls(
            f"{what} exceeded the {timeout_s}s wall-clock cap — killed "
            f"(a crafted PDF may hang the parser)"
        ) from exc
    if check and proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise error_cls(
            f"{what} exited {proc.returncode}"
            + (f": {stderr}" if stderr else " (a resource cap may have killed it)")
        )
    return proc


def decode_capped(data: bytes, cap: int) -> str:
    """Decode ``data`` as UTF-8, truncated to at most ``cap`` bytes first.

    A ``pdftotext`` pipe is not bounded by ``RLIMIT_FSIZE``, so the text it produces is capped
    here after read (mirrors deckhand's output-cap step). Decoding tolerates a mid-codepoint cut.
    """
    if len(data) > cap:
        data = data[:cap]
    return data.decode("utf-8", errors="replace")
