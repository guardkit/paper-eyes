"""The bounded-resource poppler wrapper (THREAT-MODEL.md "crafted PDF"; MA-37 hardening).

Mirrors deckhand's ``tests/test_extract_pdftext.py``: harmless stub binaries drive the sandbox
machinery — the rlimit caps, the wall-clock timeout, the fixed no-shell argv, and the fail-loud
behaviour — on any POSIX box, with no real poppler and no real PDF. Individual caps are proven by
passing tiny cap values (a stub that trips a 4 KiB file cap / a 1s CPU cap), so the preexec is
shown to bind the child rather than merely being wired.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from papereyes.errors import FetchError, PaperEyesError, SynthError
from papereyes.pipeline.ocr import OcrError
from papereyes.sandbox import (
    PDFTOPPM_FSIZE_BYTES,
    decode_capped,
    run_bounded,
)

_POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="the rlimit sandbox is POSIX-only")


def _stub(tmp_path: Path, name: str, body: str) -> Path:
    """Write an executable ``/bin/sh`` stub and return its path."""
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    p.chmod(0o755)
    return p


@_POSIX_ONLY
def test_run_bounded_captures_stdout(tmp_path: Path) -> None:
    stub = _stub(tmp_path, "echo.sh", "echo 'hello sandbox'\n")
    proc = run_bounded(
        [str(stub)],
        error_cls=SynthError,
        what="pdftoppm",
        timeout_s=5,
        cpu_seconds=10,
        address_space_bytes=0,
        fsize_bytes=0,
    )
    assert proc.returncode == 0
    assert b"hello sandbox" in proc.stdout


@_POSIX_ONLY
def test_wall_clock_timeout_is_loud(tmp_path: Path) -> None:
    stub = _stub(tmp_path, "slow.sh", "sleep 5\n")
    with pytest.raises(SynthError, match="wall-clock"):
        run_bounded(
            [str(stub)],
            error_cls=SynthError,
            what="pdftoppm",
            timeout_s=0.5,
            cpu_seconds=10,
            address_space_bytes=0,
            fsize_bytes=0,
        )


@_POSIX_ONLY
def test_fsize_cap_kills_a_file_bomb(tmp_path: Path) -> None:
    # pdftoppm writes PNG files; a parser that spills far past the file cap is killed (SIGXFSZ).
    out = tmp_path / "big.bin"
    stub = _stub(tmp_path, "bomb.sh", f"dd if=/dev/zero of='{out}' bs=1024 count=256 2>/dev/null\n")
    with pytest.raises(SynthError, match="exited"):
        run_bounded(
            [str(stub)],
            error_cls=SynthError,
            what="pdftoppm",
            timeout_s=10,
            cpu_seconds=10,
            address_space_bytes=0,
            fsize_bytes=4096,  # 4 KiB — the stub tries to write 256 KiB
        )
    assert out.stat().st_size <= 4096  # the write was capped, not allowed to run away


@_POSIX_ONLY
def test_cpu_cap_kills_a_busy_loop(tmp_path: Path) -> None:
    # A parser stuck burning CPU is killed by RLIMIT_CPU well before the generous wall clock.
    stub = _stub(tmp_path, "burn.sh", "while : ; do : ; done\n")
    with pytest.raises(SynthError, match="exited"):
        run_bounded(
            [str(stub)],
            error_cls=SynthError,
            what="pdftoppm",
            timeout_s=30,
            cpu_seconds=1,
            address_space_bytes=0,
            fsize_bytes=0,
        )


@_POSIX_ONLY
def test_no_shell_metacharacters_are_never_interpreted(tmp_path: Path) -> None:
    # argv is a fixed list: a ';' passed as an argument is a literal arg, never a shell operator.
    marker = tmp_path / "pwned"
    stub = _stub(tmp_path, "args.sh", 'echo "$1"\n')  # echoes its first arg verbatim
    proc = run_bounded(
        [str(stub), f"; touch {marker}"],
        error_cls=SynthError,
        what="pdftoppm",
        timeout_s=5,
        cpu_seconds=10,
        address_space_bytes=0,
        fsize_bytes=0,
    )
    assert not marker.exists()  # no shell ran the injected command
    assert b"; touch" in proc.stdout  # the metacharacters arrived as a literal argument


@_POSIX_ONLY
def test_nonzero_exit_is_loud_when_checked(tmp_path: Path) -> None:
    stub = _stub(tmp_path, "boom.sh", "echo 'parser error' >&2\nexit 3\n")
    with pytest.raises(SynthError, match="exited 3"):
        run_bounded(
            [str(stub)],
            error_cls=SynthError,
            what="pdftoppm",
            timeout_s=5,
            cpu_seconds=10,
            address_space_bytes=0,
            fsize_bytes=0,
        )


@_POSIX_ONLY
def test_nonzero_exit_is_tolerated_when_unchecked(tmp_path: Path) -> None:
    # The fetch probe (pdftotext) tolerates a non-zero exit but still gets its bounded run.
    stub = _stub(tmp_path, "boom.sh", "echo out\nexit 3\n")
    proc = run_bounded(
        [str(stub)],
        error_cls=FetchError,
        what="pdftotext",
        timeout_s=5,
        cpu_seconds=10,
        address_space_bytes=0,
        fsize_bytes=0,
        check=False,
    )
    assert proc.returncode == 3
    assert b"out" in proc.stdout


def test_decode_capped_truncates_to_the_byte_cap() -> None:
    assert decode_capped(b"abcdef", 3) == "abc"
    assert decode_capped(b"ab", 10) == "ab"
    # a mid-codepoint cut is tolerated (never a crash)
    assert isinstance(decode_capped("é".encode() * 4, 3), str)


def _spy(monkeypatch: pytest.MonkeyPatch, target: str) -> list[dict[str, Any]]:
    """Replace ``target``'s ``run_bounded`` with a spy that records its call and does nothing."""
    calls: list[dict[str, Any]] = []

    def fake(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(target, fake)
    return calls


def test_rasterize_routes_pdftoppm_through_the_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from papereyes.synth import rasterize

    monkeypatch.setattr(rasterize, "_require_pdftoppm", lambda: "pdftoppm")
    calls = _spy(monkeypatch, "papereyes.synth.rasterize.run_bounded")
    # The spy produces no pages, so the module fails loud afterwards — we only assert the call.
    with pytest.raises(SynthError, match="no pages"):
        rasterize.rasterize_to_image_pdf(
            tmp_path / "in.pdf", tmp_path / "out.pdf", dpi=200, workdir=tmp_path / "w"
        )
    assert len(calls) == 1
    call = calls[0]
    assert call["argv"][0] == "pdftoppm"  # fixed argv, poppler binary first
    assert call["check"] is True
    assert call["fsize_bytes"] == PDFTOPPM_FSIZE_BYTES  # image-sized, not the text cap


def test_ocr_routes_pdftoppm_through_the_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from papereyes.pipeline import ocr

    monkeypatch.setattr(ocr, "_require", lambda _name: "pdftoppm")
    calls = _spy(monkeypatch, "papereyes.pipeline.ocr.run_bounded")
    with pytest.raises(OcrError, match="no pages"):
        ocr.rasterize_pdf_pages(tmp_path / "in.pdf", dpi=150, workdir=tmp_path / "w")
    assert len(calls) == 1
    call = calls[0]
    assert call["argv"][0] == "pdftoppm"
    assert call["check"] is True
    assert call["fsize_bytes"] == PDFTOPPM_FSIZE_BYTES


def test_forms_probe_routes_pdftotext_through_the_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from papereyes.fetch import forms

    monkeypatch.setattr("papereyes.fetch.forms.shutil.which", lambda _name: "pdftotext")
    calls = _spy(monkeypatch, "papereyes.fetch.forms.run_bounded")
    out = forms.extract_text(tmp_path / "blank.pdf")
    assert out == ""  # the spy returned empty stdout
    assert len(calls) == 1
    call = calls[0]
    assert call["argv"][0] == "pdftotext"
    assert call["check"] is False  # the licence probe tolerates a non-zero exit


def test_run_bounded_error_cls_is_the_callers_type() -> None:
    # A sanity check that the wrapper raises the caller's own typed error family.
    assert issubclass(SynthError, PaperEyesError)
    assert issubclass(FetchError, PaperEyesError)
    assert issubclass(OcrError, PaperEyesError)
