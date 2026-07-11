"""`papereyes check` — passes a valid formpack, fails loudly on 5 malformed fixtures.

This is the Stage 0 gate (design spec §6): ``papereyes check`` passes on a fixture formpack
and fails loudly on 5 malformed fixtures.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from papereyes.formpack.check import check_formpack_dir, check_target
from tests.support import MALFORMED, MALFORMED_DIRS, REPO_ROOT, VALID_FORMPACK


def test_valid_formpack_passes() -> None:
    report = check_formpack_dir(VALID_FORMPACK)
    assert report.ok, report.render()


def test_exactly_five_malformed_fixtures_present() -> None:
    on_disk = sorted(p.name for p in MALFORMED.iterdir() if p.is_dir())
    assert on_disk == sorted(MALFORMED_DIRS)
    assert len(on_disk) == 5


@pytest.mark.parametrize("name", MALFORMED_DIRS)
def test_malformed_formpack_fails_loudly(name: str) -> None:
    report = check_formpack_dir(MALFORMED / name)
    assert not report.ok, f"{name} should have failed check"
    assert report.errors, f"{name} failed but produced no error line"


def test_missing_schema_file_fails(tmp_path: Path) -> None:
    # A formpack.yaml that references schema.json, but the schema file is absent.
    shutil.copy(VALID_FORMPACK / "formpack.yaml", tmp_path / "formpack.yaml")
    report = check_formpack_dir(tmp_path)
    assert not report.ok
    assert any("schema" in e for e in report.errors)


def test_pipeline_check_passes() -> None:
    report = check_target(REPO_ROOT / "pipeline.yaml")
    assert report.ok, report.render()
