"""Shared paths for the test suite."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
VALID_FORMPACK = FIXTURES / "formpack_valid"
MALFORMED = FIXTURES / "malformed"

# The five malformed fixture dirs the Stage 0 gate must reject (design spec §6 Stage 0).
MALFORMED_DIRS = (
    "extra_key",
    "missing_extraction",
    "bad_locator_kind",
    "bad_splice",
    "floor_out_of_range",
)
