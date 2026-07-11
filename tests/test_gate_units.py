"""Hermetic unit tests for the extraction gate (design spec §6 Stage 3, §7) — no endpoint.

Covers: value normalisation, per-doc scoring (floor + required fields), the legible diff
table, the frozen baseline payload, and one stub-driven ``run_gate`` end-to-end pass.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from papereyes.config.loader import load_formpack, load_pipeline
from papereyes.gate import (
    GateResult,
    build_baseline,
    normalize_value,
    render_diff_table,
    run_gate,
    score_doc,
    write_baseline,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
UK_CH2 = REPO_ROOT / "formpacks" / "uk-ch2"

EXPECTED = {
    "claimant": {
        "last_name": "Thomas",
        "nino": "BN605990B",
        "date_of_birth": "1989-08-25",
    },
    "children": [{"date_of_birth": "2019-12-26"}],
}


# ── normalisation ─────────────────────────────────────────────────────────────────────


def test_normalize_value_rules() -> None:
    # whitespace + case fold for plain fields
    assert normalize_value("claimant.last_name", "  Thomas \n") == "thomas"
    # NINO / postcode: uppercased, spaces stripped
    assert normalize_value("claimant.nino", "bn 60 59 90 b") == "BN605990B"
    assert normalize_value("claimant.postcode", "s13 9zd") == "S139ZD"
    # dates -> ISO from common renderings
    assert normalize_value("claimant.date_of_birth", "25/08/1989") == "1989-08-25"
    assert normalize_value("children[0].date_of_birth", "26 December 2019") == "2019-12-26"
    # None -> empty (a missing leaf never equals a present one)
    assert normalize_value("claimant.nino", None) == ""


# ── scoring ───────────────────────────────────────────────────────────────────────────


def test_score_doc_all_match_via_normalization() -> None:
    fp = load_formpack(UK_CH2)
    actual = {
        "claimant": {
            "last_name": "THOMAS",  # case-insensitive
            "nino": "bn605990b",  # NINO normalised
            "date_of_birth": "25/08/1989",  # date -> ISO
        },
        "children": [{"date_of_birth": "2019-12-26"}],
    }
    score = score_doc("persona-01", EXPECTED, actual, fp)
    assert score.matched == score.total == 4
    assert score.accuracy == 1.0
    assert score.required_ok


def test_score_doc_required_miss_fails_regardless_of_floor() -> None:
    fp = load_formpack(UK_CH2)
    actual = {
        "claimant": {"last_name": "Thomas", "nino": "WRONG", "date_of_birth": "1989-08-25"},
        "children": [{"date_of_birth": "2019-12-26"}],
    }
    score = score_doc("persona-01", EXPECTED, actual, fp)
    assert not score.required_ok
    assert "claimant.nino" in score.missing_required
    result = GateResult(
        formpack_slug="uk-ch2@1",
        doc_scores=[score],
        floor=0.5,  # floor met (3/4) — required miss must still fail the gate
        models={"docling": "d", "region": "r", "extract": "e"},
        formpack_sha256="0" * 64,
    )
    assert result.overall_accuracy >= 0.5
    assert not result.passed


def test_render_diff_table_names_the_missing_field() -> None:
    fp = load_formpack(UK_CH2)
    actual = {
        "claimant": {"last_name": "Thomas", "nino": "WRONG", "date_of_birth": "1989-08-25"},
        "children": [{"date_of_birth": "2019-12-26"}],
    }
    score = score_doc("persona-01", EXPECTED, actual, fp)
    result = GateResult(
        formpack_slug="uk-ch2@1",
        doc_scores=[score],
        floor=0.95,
        models={"docling": "d", "region": "r", "extract": "e"},
        formpack_sha256="0" * 64,
    )
    table = render_diff_table(result)
    assert "MISS claimant.nino" in table
    assert "REQUIRED MISS" in table
    assert "-> FAIL" in table


# ── baseline ──────────────────────────────────────────────────────────────────────────


def _passing_result(fp_sha: str = "a" * 64) -> GateResult:
    fp = load_formpack(UK_CH2)
    score = score_doc("persona-01", EXPECTED, EXPECTED, fp)
    return GateResult(
        formpack_slug="uk-ch2@1",
        doc_scores=[score],
        floor=0.95,
        models={"docling": "d", "region": "r", "extract": "e"},
        formpack_sha256=fp_sha,
    )


def test_build_baseline_is_content_hashed_and_per_field() -> None:
    payload = build_baseline(_passing_result())
    assert payload["content_hash"].startswith("sha256:")
    assert payload["formpack_sha256"] == "a" * 64
    # per-field scores present for every expected leaf
    fields = payload["per_doc"]["persona-01"]["fields"]
    assert fields["claimant.nino"] is True
    assert len(fields) == 4
    # deterministic: same result -> same hash; different formpack sha -> different hash
    assert payload["content_hash"] == build_baseline(_passing_result())["content_hash"]
    assert payload["content_hash"] != build_baseline(_passing_result("b" * 64))["content_hash"]


def test_write_baseline_roundtrip(tmp_path: Path) -> None:
    path = write_baseline(tmp_path, _passing_result())
    assert path.name == "formpack.baseline.json"
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["overall_accuracy"] == 1.0
    assert parsed["required_ok"] is True


# ── run_gate with the stub (hermetic end-to-end mechanics) ────────────────────────────


@pytest.mark.skipif(
    shutil.which("pdftoppm") is None, reason="pdftoppm (poppler-utils) not installed"
)
def test_run_gate_scores_a_golden_doc_with_stub(tmp_path: Path) -> None:
    from tests.pipeline_support import StubModelClient

    fp = load_formpack(UK_CH2)
    # Trim to one golden doc and point it at a fresh dir so regeneration is exercised.
    fp.golden.docs = fp.golden.docs[:1]
    fp.synth.count = 1
    formpack_dir = tmp_path / "uk-ch2"
    shutil.copytree(UK_CH2, formpack_dir, ignore=shutil.ignore_patterns("*.pdf"))
    pipeline_cfg = load_pipeline(REPO_ROOT / "pipeline.yaml")

    result = run_gate(
        fp, formpack_dir, pipeline_cfg, StubModelClient(), workdir=tmp_path / "work"
    )
    # The stub returns persona-01's canned pages/extraction; the golden IS persona-01.
    assert result.doc_scores[0].doc_id == "persona-01"
    assert result.overall_accuracy == 1.0
    assert result.passed
    # Regeneration happened (scan PDFs are never committed).
    assert (formpack_dir / "golden" / "persona-01.pdf").is_file()
