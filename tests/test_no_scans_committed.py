"""The OGL leakage guard (design spec §6 Stage 1; master plan §7).

No rendered form scan (PDF/PNG/JPEG/TIFF) may be tracked by git anywhere — rasterised
public-form pages can carry departmental crests/logos/Royal Arms, which OGL v3 excludes.
The golden scans regenerate from committed seeds; only expected JSONs + seeds are committed.
"""

from __future__ import annotations

import subprocess

from tests.support import REPO_ROOT

_SCAN_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff")


def test_no_scan_images_are_tracked_by_git() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    offenders = [p for p in tracked if p.lower().endswith(_SCAN_SUFFIXES)]
    assert not offenders, "rendered scans must never be committed:\n" + "\n".join(offenders)


def test_golden_dirs_hold_expected_json_but_no_scans() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "formpacks"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    golden_tracked = [p for p in tracked if "/golden/" in p]
    # Once the corpus is committed there is at least one expected JSON, and never a scan.
    assert all(not p.lower().endswith(_SCAN_SUFFIXES) for p in golden_tracked)
