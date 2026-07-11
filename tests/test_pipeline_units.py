"""Hermetic unit tests for the pipeline mechanisms (design spec §6 Stage 2) — no endpoint, no GPU.

Covers: DocTags parsing, the two locators, markdown reconstruction + both splices, region-text
cleaning, the extraction helpers, and the deterministic report render.
"""

from __future__ import annotations

from pathlib import Path

from papereyes.config.loader import load_formpack
from papereyes.config.models import (
    HeadingSpanLocator,
    PictureSignatureLocator,
)
from papereyes.pipeline.extract import parse_json_object, sanitize_schema, strip_wrapper
from papereyes.pipeline.locate import locate_region, locate_regions
from papereyes.pipeline.ocr import Page, group_rows, parse_doctags
from papereyes.pipeline.report import render_report
from papereyes.pipeline.splice import (
    PLACEHOLDER,
    apply_insert_after_anchor,
    apply_replace_placeholder,
    clean_region_text,
    pick_grid_value,
    reconstruct_markdown,
)
from tests.pipeline_support import PERSONA01_EXTRACTION, PERSONA01_PAGE1, PERSONA01_PAGE2

REPO_ROOT = Path(__file__).resolve().parent.parent
UK_CH2 = REPO_ROOT / "formpacks" / "uk-ch2"


def _page(page_no: int, doctags: str) -> Page:
    return Page(
        page_no=page_no,
        width_px=1654,
        height_px=2339,
        image_path=Path("/dev/null"),
        elements=parse_doctags(doctags),
    )


# ── DocTags parsing ───────────────────────────────────────────────────────────────────


def test_parse_doctags_reads_located_elements() -> None:
    els = parse_doctags(PERSONA01_PAGE1)
    texts = [e.text for e in els]
    assert "Child Benefit claim" in texts
    assert "Ms" in texts
    # NINO grid fragmented into single-glyph cells.
    assert texts.count("B") == 2  # first + suffix letter of BN605990B


def test_parse_doctags_handles_wrappers_and_dedup() -> None:
    raw = (
        "<doctag><text><loc_1><loc_2><loc_3><loc_4>Hello</text>\n"
        "<loc_1><loc_2><loc_3><loc_4>Hello\n"  # exact duplicate -> collapsed
        "<loc_5><loc_6><loc_7><loc_8>World</doctag>"
    )
    els = parse_doctags(raw)
    assert [e.text for e in els] == ["Hello", "World"]


def test_group_rows_orders_top_to_bottom_left_to_right() -> None:
    els = parse_doctags(PERSONA01_PAGE1)
    rows = group_rows(els)
    # First row is the title; the NINO row groups the label + 9 cells together.
    assert rows[0][0].text == "Child Benefit claim"
    nino_row = next(r for r in rows if any(e.text == "National Insurance number" for e in r))
    single_cells = [e for e in nino_row if len(e.text) == 1]
    assert len(single_cells) == 9


# ── locators ──────────────────────────────────────────────────────────────────────────


def test_picture_signature_locates_nino_grid() -> None:
    fp = load_formpack(UK_CH2)
    region = next(r for r in fp.regions if isinstance(r.locate, PictureSignatureLocator))
    match = locate_region(region, [_page(1, PERSONA01_PAGE1)])
    assert match is not None
    assert match.region_id == "claimant-nino-grid"
    assert match.fallback_text == "BN605990B"  # cells joined from the bulk conversion
    assert len(match.members) == 9


def test_picture_signature_respects_min_cells() -> None:
    fp = load_formpack(UK_CH2)
    region = next(r for r in fp.regions if isinstance(r.locate, PictureSignatureLocator))
    # A short run of 3 single chars must not trip the grid signature (min_cells=6).
    doctags = (
        "<loc_186><loc_120><loc_192><loc_126>A\n"
        "<loc_200><loc_120><loc_206><loc_126>B\n"
        "<loc_214><loc_120><loc_220><loc_126>C\n"
    )
    assert locate_region(region, [_page(1, doctags)]) is None


def test_picture_signature_respects_page_range() -> None:
    fp = load_formpack(UK_CH2)
    region = next(r for r in fp.regions if isinstance(r.locate, PictureSignatureLocator))
    # NINO content but on page 5 (outside page_range [1,2]) -> no match.
    assert locate_region(region, [_page(5, PERSONA01_PAGE1)]) is None


def test_heading_span_brackets_children() -> None:
    fp = load_formpack(UK_CH2)
    region = next(r for r in fp.regions if isinstance(r.locate, HeadingSpanLocator))
    match = locate_region(region, [_page(2, PERSONA01_PAGE2)])
    assert match is not None
    assert match.region_id == "child-details"
    # anchor included, "3 Higher income" (the stop) excluded.
    joined = match.fallback_text
    assert "Children you're claiming for" in joined
    assert "Higher income" not in joined
    assert "Joel" in joined and "Hayley" in joined


def test_locate_regions_skips_nonmatching() -> None:
    fp = load_formpack(UK_CH2)
    # Empty page: neither region matches, and that is not an error.
    assert locate_regions(fp.regions, [_page(1, "<loc_1><loc_2><loc_3><loc_4>nothing here")]) == []


# ── reconstruction + splices ────────────────────────────────────────────────────────────


def test_reconstruct_collapses_grid_to_placeholder() -> None:
    fp = load_formpack(UK_CH2)
    pages = [_page(1, PERSONA01_PAGE1), _page(2, PERSONA01_PAGE2)]
    picture_matches = locate_regions([fp.regions[0]], pages)
    recon = reconstruct_markdown(pages, picture_matches)
    assert recon.markdown.count(PLACEHOLDER) == 1
    assert recon.placeholder_region_ids == ["claimant-nino-grid"]
    # The 9 single cells are gone from the text, replaced by one placeholder on the NINO row.
    assert "National Insurance number <!-- image -->" in recon.markdown


def test_replace_placeholder_substitutes_kth() -> None:
    md = f"a {PLACEHOLDER} b {PLACEHOLDER} c"
    assert apply_replace_placeholder(md, 0, "X") == f"a X b {PLACEHOLDER} c"
    assert apply_replace_placeholder(md, 1, "Y") == f"a {PLACEHOLDER} b Y c"
    # No k-th placeholder -> unchanged (region no-op).
    assert apply_replace_placeholder(md, 5, "Z") == md


def test_insert_after_anchor_is_nondestructive() -> None:
    md = "before\n2 Children you're claiming for\nafter"
    out = apply_insert_after_anchor(md, r"(?i)children you", "First names: Joel")
    lines = out.splitlines()
    assert lines == ["before", "2 Children you're claiming for", "First names: Joel", "after"]
    # Original anchor text preserved; nothing deleted.
    assert "2 Children you're claiming for" in out


def test_clean_region_text_strips_tags() -> None:
    raw = "<md><loc_1><loc_2><loc_3><loc_4>B\n<loc_5><loc_6><loc_7><loc_8>N</md>"
    assert clean_region_text(raw) == "B\nN"


def test_pick_grid_value_prefers_clean_reread_else_fallback() -> None:
    # Clean re-read of exactly the grid's length -> used (single line or spaced cells).
    assert pick_grid_value("B N 6 0 5 9 9 0 B", "XX000000X") == "BN605990B"
    assert pick_grid_value("BN605990B", "XX000000X") == "BN605990B"
    # Degenerate re-read -> fallback stands.
    assert pick_grid_value("garbled ??? nonsense text here", "BN605990B") == "BN605990B"
    # A different-length re-read is a misread: the bulk pass counted the cells -> fallback.
    assert pick_grid_value("BN6059900B", "BN605990B") == "BN605990B"
    # No fallback (no legible cells): a clean token is accepted as-is.
    assert pick_grid_value("BN605990B", "") == "BN605990B"


# ── extraction helpers ──────────────────────────────────────────────────────────────────


def test_sanitize_schema_drops_meta_keys() -> None:
    schema = {"$schema": "x", "title": "T", "type": "object", "properties": {}}
    out = sanitize_schema(schema)
    assert "$schema" not in out and "title" not in out
    assert out["type"] == "object"


def test_strip_wrapper_removes_fences_and_md() -> None:
    assert strip_wrapper("```json\n{\"a\": 1}\n```") == '{"a": 1}'
    assert strip_wrapper("<md>{\"a\": 1}</md>") == '{"a": 1}'


def test_parse_json_object_salvages_embedded_object() -> None:
    assert parse_json_object('here you go: {"a": 1} thanks') == {"a": 1}
    assert parse_json_object(PERSONA01_EXTRACTION)["claimant"]["nino"] == "BN605990B"


# ── report ──────────────────────────────────────────────────────────────────────────────


def test_render_report_is_snippet_first_and_deterministic() -> None:
    fp = load_formpack(UK_CH2)
    extraction = parse_json_object(PERSONA01_EXTRACTION)
    kwargs = dict(
        formpack=fp, scan_name="persona-01.pdf", scan_sha256="a" * 64,
        formpack_sha256="b" * 64, docling_model="granite-docling",
        region_model="granite-vision-4-1-4b", extract_model="qwen36-workhorse",
        regions_triggered=["claimant-nino-grid", "child-details"],
        decoding_desc="temperature=0.0 seed=42 (all calls)",
    )
    r1 = render_report(extraction, **kwargs)  # type: ignore[arg-type]
    r2 = render_report(extraction, **kwargs)  # type: ignore[arg-type]
    assert r1 == r2  # deterministic
    # snippet-first: headline identity fields inside the first 320 chars.
    snippet = r1[:320]
    assert "Brandon Thomas" in snippet
    assert "BN605990B" in snippet
    # no timing VALUES / latencies / timestamps in the report (they live in provenance.json).
    assert "timings_s" not in r1 and "latency" not in r1
    assert "total:" not in r1
    assert "PROVENANCE (deterministic subset — timings live in provenance.json)" in r1
