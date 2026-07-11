"""`papereyes init` scaffolds a valid-on-creation formpack (design spec §6 Stage 0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from papereyes.errors import ConfigError
from papereyes.formpack.check import check_formpack_dir
from papereyes.formpack.scaffold import scaffold_formpack


def test_scaffold_writes_expected_files(tmp_path: Path) -> None:
    dest = tmp_path / "uk-newform"
    written = scaffold_formpack(dest, name="uk-newform")
    names = {p.name for p in written}
    assert names == {"formpack.yaml", "schema.json"}
    assert (dest / "golden").is_dir()


def test_scaffolded_formpack_passes_check(tmp_path: Path) -> None:
    dest = tmp_path / "uk-newform"
    scaffold_formpack(dest, name="uk-newform")
    report = check_formpack_dir(dest)
    assert report.ok, report.render()


def test_scaffold_refuses_to_clobber(tmp_path: Path) -> None:
    dest = tmp_path / "uk-newform"
    scaffold_formpack(dest, name="uk-newform")
    with pytest.raises(ConfigError):
        scaffold_formpack(dest, name="uk-newform")
