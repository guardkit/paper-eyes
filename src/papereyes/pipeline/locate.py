"""Region locators + cropping (design spec §2 IN "two region locator kinds", §6 Stage 2).

Two locator kinds, generalised from the proven mechanisms (read for mechanism only — leakage
law, §6 Stage 0):

- ``picture_signature`` — the boxed-capital grid the layout model mishandles. The local Docling
  pipeline misroutes such a grid to a ``PictureItem``; the served Docling VLM instead fragments
  it into a run of single-glyph elements. Either way the *signature* is the same: a horizontal
  run of small, equal-ish, adjacent cells. This locator detects that run within a page band and
  size window. Because the run's own characters are legible, it also carries a ``fallback_text``
  (the cells joined) so the field survives even if the VLM re-read is unavailable.
- ``heading_span`` — the span from an ``anchor`` heading to a ``stop`` heading / page break. The
  bbox union of the bracketed elements is the region.

Both return a :class:`RegionMatch`. Cropping converts the loc-grid bbox to page pixels, adds a
small margin, and presents the **tight crop** to the vision model. Calibrated live against the
pinned region VLM (granite-vision-4-1-4b, 2026-07-11): the tight NINO-grid strip is transcribed
correctly, while pasting it onto a page-sized white canvas — an earlier presentation — made the
model misread characters. The saved crop is also the filmable "what the VLM saw" receipt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PIL import Image

from papereyes.config.models import HeadingSpanLocator, PictureSignatureLocator, Region
from papereyes.pipeline.ocr import LocatedElement, Page, group_rows


@dataclass(frozen=True)
class RegionMatch:
    """A located region: which formpack region, which page, the bbox, and a text fallback."""

    region_id: str
    page_index: int  # 0-based index into the pages list
    page_no: int  # 1-based page number
    bbox_loc: tuple[float, float, float, float]
    members: tuple[LocatedElement, ...]
    fallback_text: str
    splice: str


def _union(elements: list[LocatedElement]) -> tuple[int, int, int, int]:
    return (
        min(e.x1 for e in elements),
        min(e.y1 for e in elements),
        max(e.x2 for e in elements),
        max(e.y2 for e in elements),
    )


def _longest_single_char_run(row: list[LocatedElement]) -> list[LocatedElement]:
    """The longest run of adjacent single-glyph cells in a row — the boxed-grid signature."""
    best: list[LocatedElement] = []
    cur: list[LocatedElement] = []
    for el in row:
        if len(el.text) == 1 and not el.text.isspace():
            cur.append(el)
            if len(cur) > len(best):
                best = list(cur)
        else:
            cur = []
    return best


def _locate_picture_signature(
    region: Region, loc: PictureSignatureLocator, pages: list[Page]
) -> RegionMatch | None:
    lo, hi = loc.page_range
    for idx, page in enumerate(pages):
        if not (lo <= page.page_no <= hi):
            continue
        for row in group_rows(page.elements):
            run = _longest_single_char_run(row)
            if len(run) < loc.min_cells:
                continue
            x1, y1, x2, y2 = _union(run)
            width, height = x2 - x1, y2 - y1
            if width < loc.min_width or width > loc.max_width or height < loc.min_height:
                continue
            if loc.max_height is not None and height > loc.max_height:
                continue
            return RegionMatch(
                region_id=region.id,
                page_index=idx,
                page_no=page.page_no,
                bbox_loc=(x1, y1, x2, y2),
                members=tuple(run),
                fallback_text="".join(e.text for e in run),
                splice=region.splice,
            )
    return None


def _locate_heading_span(
    region: Region, loc: HeadingSpanLocator, pages: list[Page]
) -> RegionMatch | None:
    anchor_re = re.compile(loc.anchor)
    stop_re = re.compile(loc.stop) if loc.stop else None
    for idx, page in enumerate(pages):
        ordered = sorted(page.elements, key=lambda e: (e.cy, e.x1))
        start: int | None = None
        for i, el in enumerate(ordered):
            if anchor_re.search(el.text):
                start = i
                break
        if start is None:
            continue
        members: list[LocatedElement] = [ordered[start]]
        for el in ordered[start + 1 :]:
            if stop_re is not None and stop_re.search(el.text):
                break
            members.append(el)
        # single_page is implicit: we only ever collect within one page here.
        x1, y1, x2, y2 = _union(members)
        return RegionMatch(
            region_id=region.id,
            page_index=idx,
            page_no=page.page_no,
            bbox_loc=(x1, y1, x2, y2),
            members=tuple(members),
            fallback_text="\n".join(e.text for e in members),
            splice=region.splice,
        )
    return None


def locate_region(region: Region, pages: list[Page]) -> RegionMatch | None:
    """Locate one formpack region across the converted pages, or ``None`` if it does not match.

    A non-match is a legitimate outcome, not an error: the region pass degrades to a no-op and
    extraction runs on the bulk conversion alone (design spec §9 Risk 1).
    """
    loc = region.locate
    if isinstance(loc, PictureSignatureLocator):
        return _locate_picture_signature(region, loc, pages)
    return _locate_heading_span(region, loc, pages)


def locate_regions(regions: list[Region], pages: list[Page]) -> list[RegionMatch]:
    """Locate every region; drop the ones that do not match."""
    matches: list[RegionMatch] = []
    for region in regions:
        m = locate_region(region, pages)
        if m is not None:
            matches.append(m)
    return matches


def fallback_match(region: Region, pages: list[Page]) -> RegionMatch | None:
    """Synthesize a match from a picture-signature locator's pinned fallback bbox.

    Fired only when the signature did not match anywhere in its page range (the converter
    merged the grid cells instead of fragmenting them, so there is no run to detect). The
    synthesized match has no members and no fallback text — it exists to produce the crop
    (the filmable "what the VLM saw" receipt) for the strict-format re-ask; it never takes
    part in placeholder splicing.
    """
    loc = region.locate
    if not isinstance(loc, PictureSignatureLocator):
        return None
    if loc.fallback_bbox is None or loc.fallback_page is None:
        return None
    for idx, page in enumerate(pages):
        if page.page_no == loc.fallback_page:
            return RegionMatch(
                region_id=region.id,
                page_index=idx,
                page_no=page.page_no,
                bbox_loc=loc.fallback_bbox,
                members=(),
                fallback_text="",
                splice=region.splice,
            )
    return None


@dataclass
class RegionCrop:
    """A cropped region: the tight image sent to the VLM, saved as the filmable receipt."""

    region_id: str
    image: Image.Image


def crop_region(page: Page, match: RegionMatch, *, margin_frac: float = 0.01) -> RegionCrop:
    """Crop ``match`` tightly from ``page``'s raster, with a small margin.

    ``margin_frac`` pads the tight crop (fraction of page size). The tight crop is both the
    VLM input and the receipt — the presentation validated against the pinned region VLM.
    """
    with Image.open(page.image_path) as raw:
        img = raw.convert("RGB")
        w, h = img.size
        mx = round(margin_frac * w)
        my = round(margin_frac * h)
        x1, y1 = page.loc_to_px(match.bbox_loc[0], match.bbox_loc[1])
        x2, y2 = page.loc_to_px(match.bbox_loc[2], match.bbox_loc[3])
        box = (max(0, x1 - mx), max(0, y1 - my), min(w, x2 + mx), min(h, y2 + my))
        tight = img.crop(box)
    return RegionCrop(region_id=match.region_id, image=tight)
