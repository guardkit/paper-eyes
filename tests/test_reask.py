"""Strict-format reject-and-re-ask + the pinned fallback bbox (gate evidence 2026-07-11).

Hermetic: stub client only, no endpoint, no GPU. The real 6-golden gate run over the served
models is the live receipt; these tests pin the mechanism — the four re-ask outcomes
(untouched / normalized / repaired / unrepaired stays honest) and the fallback-bbox locator
for a grid the converter merged instead of fragmenting.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from papereyes.config.loader import load_formpack, load_pipeline
from papereyes.config.models import PictureSignatureLocator
from papereyes.pipeline.client import ModelResult
from papereyes.pipeline.locate import crop_region, fallback_match
from papereyes.pipeline.ocr import LOC_SCALE, Page
from papereyes.pipeline.run import RunResult, _normalize_id_value, _set_path, run_pipeline
from papereyes.synth.generator import synth_corpus
from tests.pipeline_support import PERSONA01_EXTRACTION, StubModelClient

REPO_ROOT = Path(__file__).resolve().parent.parent
UK_CH2 = REPO_ROOT / "formpacks" / "uk-ch2"

pytestmark = pytest.mark.skipif(
    shutil.which("pdftoppm") is None, reason="pdftoppm (poppler-utils) not installed"
)


def _persona01_scan(tmp_path: Path) -> Path:
    golden = tmp_path / "golden"
    synth_corpus(golden, base_seed=7, count=1, dpi=200)
    return golden / "persona-01.pdf"


def _reask_calls(result: RunResult) -> list[dict[str, Any]]:
    return [
        c for c in result.provenance["model_calls"] if c["kind"] in ("reask", "reask_cell")
    ]


class CellStub(StubModelClient):
    """Answers the per-box single-character prompt from a queue; parent default otherwise."""

    cells: list[str]

    def read_region(self, prompt: str, image_png: bytes, *, max_tokens: int) -> ModelResult:
        cells = getattr(self, "cells", [])
        if "single character" in prompt and cells:
            self.region_prompts.append(prompt)
            return ModelResult(text=cells.pop(0), model=self.region_model, latency_s=0.01)
        return super().read_region(prompt, image_png, max_tokens=max_tokens)


# ── units ─────────────────────────────────────────────────────────────────────────────


def test_normalize_id_value() -> None:
    assert _normalize_id_value("BN 60 59 90 b") == "BN605990B"
    assert _normalize_id_value(" bn-605990-B\n") == "BN605990B"
    assert _normalize_id_value(None) == ""


def test_set_path_dict_and_list_hops() -> None:
    obj: dict[str, Any] = {"claimant": {"nino": "X"}, "children": [{"date_of_birth": "1"}]}
    _set_path(obj, "claimant.nino", "BN605990B")
    _set_path(obj, "children[0].date_of_birth", "2020-01-01")
    assert obj["claimant"]["nino"] == "BN605990B"
    assert obj["children"][0]["date_of_birth"] == "2020-01-01"
    # absent hops are a no-op, never a crash
    _set_path(obj, "claimant.missing.deep", "v")
    _set_path(obj, "children[9].x", "v")


def test_fallback_requires_both_fields() -> None:
    with pytest.raises(ValueError, match="set together"):
        PictureSignatureLocator(
            kind="picture_signature",
            min_width=1,
            min_height=1,
            max_width=10,
            page_range=(1, 1),
            fallback_page=1,
        )


def test_fallback_match_synthesizes_the_pinned_crop(tmp_path: Path) -> None:
    """A page with no single-glyph run still yields the region's pinned crop."""
    fp = load_formpack(UK_CH2)
    region = next(r for r in fp.regions if r.id == "claimant-nino-grid")
    assert isinstance(region.locate, PictureSignatureLocator)
    assert region.locate.fallback_bbox is not None  # the calibration under test

    page_png = tmp_path / "page-1.png"
    Image.new("RGB", (1654, 2339), "white").save(page_png)
    page = Page(page_no=1, width_px=1654, height_px=2339, image_path=page_png, elements=[])

    m = fallback_match(region, [page])
    assert m is not None
    assert m.members == () and m.fallback_text == ""
    assert m.bbox_loc == region.locate.fallback_bbox

    crop = crop_region(page, m)
    x1, _ = page.loc_to_px(m.bbox_loc[0], m.bbox_loc[1])
    x2, _ = page.loc_to_px(m.bbox_loc[2], m.bbox_loc[3])
    # margin_frac pads by 1% of the page each side
    assert abs(crop.image.width - ((x2 - x1) + 2 * round(0.01 * 1654))) <= 2
    assert m.bbox_loc[2] <= LOC_SCALE and m.bbox_loc[3] <= LOC_SCALE


# ── the re-ask outcomes, end-to-end over the stub ─────────────────────────────────────


def test_valid_nino_makes_no_reask_call(tmp_path: Path) -> None:
    scan = _persona01_scan(tmp_path)
    fp = load_formpack(UK_CH2)
    cfg = load_pipeline(REPO_ROOT / "pipeline.yaml")
    result = run_pipeline(scan, fp, UK_CH2, cfg, StubModelClient(), workdir=tmp_path / "w")
    assert result.extraction["claimant"]["nino"] == "BN605990B"
    assert _reask_calls(result) == []
    assert result.provenance["reasks"] == []


def test_spaced_nino_is_normalized_without_a_model_call(tmp_path: Path) -> None:
    scan = _persona01_scan(tmp_path)
    fp = load_formpack(UK_CH2)
    cfg = load_pipeline(REPO_ROOT / "pipeline.yaml")
    stub = StubModelClient(
        extraction_text=PERSONA01_EXTRACTION.replace('"BN605990B"', '"BN 60 59 90 B"')
    )
    result = run_pipeline(scan, fp, UK_CH2, cfg, stub, workdir=tmp_path / "w")
    assert result.extraction["claimant"]["nino"] == "BN605990B"
    assert _reask_calls(result) == []  # canonicalized in place, no model call
    assert [r["outcome"] for r in result.provenance["reasks"]] == ["normalized"]


def test_garbled_nino_is_repaired_by_per_box_reask(tmp_path: Path) -> None:
    scan = _persona01_scan(tmp_path)
    fp = load_formpack(UK_CH2)
    cfg = load_pipeline(REPO_ROOT / "pipeline.yaml")
    # the gate's observed failure shape: characters dropped by the transcription
    stub = CellStub(extraction_text=PERSONA01_EXTRACTION.replace('"BN605990B"', '"B60599"'))
    stub.cells = list("BN605990B")  # one single-character reply per box
    result = run_pipeline(scan, fp, UK_CH2, cfg, stub, workdir=tmp_path / "w")
    assert result.extraction["claimant"]["nino"] == "BN605990B"
    calls = _reask_calls(result)
    assert len(calls) == 9 and all(c["kind"] == "reask_cell" for c in calls)
    (entry,) = result.provenance["reasks"]
    assert entry["outcome"] == "repaired" and entry["was"] == "B60599"
    # the pinned row crop is saved as the filmable receipt
    assert any("reask-row" in p.name for p in result.crops)


def test_unrepairable_nino_stays_as_extracted(tmp_path: Path) -> None:
    """A reply that still fails the format is never adopted — an honest miss, not a mask."""
    scan = _persona01_scan(tmp_path)
    fp = load_formpack(UK_CH2)
    cfg = load_pipeline(REPO_ROOT / "pipeline.yaml")
    stub = CellStub(extraction_text=PERSONA01_EXTRACTION.replace('"BN605990B"', '"B60599"'))
    stub.cells = ["?"] * 9  # every box unreadable
    result = run_pipeline(scan, fp, UK_CH2, cfg, stub, workdir=tmp_path / "w")
    assert result.extraction["claimant"]["nino"] == "B60599"
    (entry,) = result.provenance["reasks"]
    assert entry["outcome"] == "unrepaired"
