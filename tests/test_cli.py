"""The Stage 0-1 CLI surface: version, check, init exit codes."""

from __future__ import annotations

from pathlib import Path

import pytest

from papereyes import __version__
from papereyes.cli import main
from tests.support import MALFORMED, VALID_FORMPACK


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_check_valid_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(VALID_FORMPACK)]) == 0
    assert "OK" in capsys.readouterr().out


def test_check_malformed_returns_one(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(MALFORMED / "extra_key")]) == 1
    assert "FAIL" in capsys.readouterr().err


def test_init_returns_zero_and_scaffolds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "uk-newform"
    assert main(["init", str(dest)]) == 0
    assert (dest / "formpack.yaml").is_file()
    assert "scaffolded" in capsys.readouterr().out


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "papereyes" in capsys.readouterr().out
