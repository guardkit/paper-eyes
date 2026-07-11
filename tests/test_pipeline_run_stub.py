"""End-to-end pipeline run with the stub client (design spec §6 Stage 2) — hermetic.

Needs the poppler rasteriser (a real synthetic scan is generated and rasterised), but **no
endpoint and no GPU**: every model call goes through the stub. Proves the plumbing (identify ->
convert -> locate -> splice -> extract -> report + provenance) and the deterministic report.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from papereyes.config.loader import load_formpack, load_pipeline
from papereyes.pipeline.run import run_pipeline
from papereyes.synth.generator import synth_corpus
from tests.pipeline_support import StubModelClient

REPO_ROOT = Path(__file__).resolve().parent.parent
UK_CH2 = REPO_ROOT / "formpacks" / "uk-ch2"

pytestmark = pytest.mark.skipif(
    shutil.which("pdftoppm") is None, reason="pdftoppm (poppler-utils) not installed"
)


def _persona01_scan(tmp_path: Path) -> Path:
    golden = tmp_path / "golden"
    synth_corpus(golden, base_seed=7, count=1, dpi=200)
    return golden / "persona-01.pdf"


def test_run_pipeline_end_to_end_with_stub(tmp_path: Path) -> None:
    scan = _persona01_scan(tmp_path)
    fp = load_formpack(UK_CH2)
    pipeline_cfg = load_pipeline(REPO_ROOT / "pipeline.yaml")
    stub = StubModelClient()

    result = run_pipeline(scan, fp, UK_CH2, pipeline_cfg, stub, workdir=tmp_path / "work")

    # extraction structured correctly
    assert result.extraction["claimant"]["nino"] == "BN605990B"
    assert result.extraction["children"][0]["first_names"] == "Joel"

    # the NINO placeholder was spliced with the grid value (splice wired end-to-end)
    assert "National Insurance number BN605990B" in stub.last_extract_markdown

    # report is snippet-first
    assert "Brandon Thomas" in result.report_text[:320]

    # provenance: models, per-stage timings, regions, crops, the served-docling deviation
    prov = result.provenance
    assert prov["models"]["docling"] == "granite-docling"
    assert prov["models"]["region"] == "granite-vision-4-1-4b"  # the PIN, no silent substitute
    assert prov["regions_triggered"] == ["claimant-nino-grid", "child-details"]
    assert set(prov["timings_s"]) >= {"identify", "ocr", "vlm", "extract", "total"}
    assert any("granite-docling" in d for d in prov["deviations"]), (
        "the served-docling bulk-conversion deviation must be recorded on every run"
    )

    # two region crops saved as receipts
    assert len(result.crops) == 2
    for crop in result.crops:
        assert crop.is_file()


def test_run_pipeline_report_is_byte_deterministic(tmp_path: Path) -> None:
    scan = _persona01_scan(tmp_path)
    fp = load_formpack(UK_CH2)
    pipeline_cfg = load_pipeline(REPO_ROOT / "pipeline.yaml")

    r1 = run_pipeline(scan, fp, UK_CH2, pipeline_cfg, StubModelClient(), workdir=tmp_path / "w1")
    r2 = run_pipeline(scan, fp, UK_CH2, pipeline_cfg, StubModelClient(), workdir=tmp_path / "w2")
    assert r1.report_text == r2.report_text
    assert r1.extraction == r2.extraction
    assert r1.scan_sha256 == r2.scan_sha256


def test_identify_rejects_wrong_document(tmp_path: Path) -> None:
    scan = _persona01_scan(tmp_path)
    fp = load_formpack(UK_CH2)
    pipeline_cfg = load_pipeline(REPO_ROOT / "pipeline.yaml")
    # A stub whose pages carry no CH2 markers -> identify must refuse to route.
    stub = StubModelClient(convert_pages=["<loc_1><loc_2><loc_3><loc_4>Some other form entirely"])
    from papereyes.pipeline.run import IdentifyError

    with pytest.raises(IdentifyError):
        run_pipeline(scan, fp, UK_CH2, pipeline_cfg, stub, workdir=tmp_path / "work")
