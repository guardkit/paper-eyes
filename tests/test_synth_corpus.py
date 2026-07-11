"""Rasterised-corpus gate (design spec §6 Stage 1): image-only + byte-deterministic scans.

Requires the poppler ``pdftoppm`` binary; skipped when absent (CI installs poppler-utils).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from papereyes.synth.generator import synth_corpus

pytestmark = pytest.mark.skipif(
    shutil.which("pdftoppm") is None, reason="pdftoppm (poppler-utils) not installed"
)


def _pdftotext_chars(pdf: Path) -> int:
    out = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True)
    return len("".join(out.stdout.split()))


def test_generates_six_image_only_scans(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    result = synth_corpus(golden, base_seed=7, count=6, dpi=200)
    assert result.count == 6

    scans = sorted(golden.glob("*.pdf"))
    assert len(scans) == 6
    for scan in scans:
        # Image-only: pdftotext yields well under 50 characters (in fact none).
        assert _pdftotext_chars(scan) < 50, f"{scan.name} is not image-only"


def test_scans_regenerate_byte_identically(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    synth_corpus(a, base_seed=7, count=3, dpi=200)
    synth_corpus(b, base_seed=7, count=3, dpi=200)
    for name in ("persona-01.pdf", "persona-02.pdf", "persona-03.pdf"):
        sha_a = hashlib.sha256((a / name).read_bytes()).hexdigest()
        sha_b = hashlib.sha256((b / name).read_bytes()).hexdigest()
        assert sha_a == sha_b, f"{name} is not byte-deterministic across regeneration"


def test_expected_sha_recorded_in_manifest(tmp_path: Path) -> None:
    import json

    golden = tmp_path / "golden"
    synth_corpus(golden, base_seed=7, count=2, dpi=200, expected_only=True)
    manifest = json.loads((golden / "seeds.json").read_text(encoding="utf-8"))
    assert manifest["base_seed"] == 7
    for doc in manifest["docs"]:
        payload = (golden / Path(doc["expected"]).name).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == doc["expected_sha256"]
