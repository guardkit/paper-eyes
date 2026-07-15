"""Bulk document conversion — the "Docling" step (design spec §3 step 2, §6 Stage 2).

The spec's bulk pass is Docling's ``StandardPdfPipeline`` (Tesseract OCR + layout + tables).
That local pipeline is not runnable on the build host: there is no ``tesseract`` binary and no
sudo to install one (verified 2026-07-11), and Docling's non-Tesseract local engines are
multi-gigabyte torch stacks with Hugging Face weight downloads that live outside the one pinned,
provenance-recorded endpoint. The bulk conversion is therefore served by ``granite-docling`` —
the Docling project's own document-conversion VLM — on the same pinned endpoint as every other
model call. It emits DocTags: per-element location tags ``<loc_x1><loc_y1><loc_x2><loc_y2>`` on
a 0-500 normalised grid, followed by the element text. This module parses that into a typed page
model the locators (``locate.py``) and splices (``splice.py``) run against.

DEVIATION (recorded in the calibration note + every provenance sidecar): the served Docling VLM
replaces the local ``StandardPdfPipeline``. It transcribes the boxed-capital grids the local
layout model would misroute to a ``PictureItem``, so ``picture_signature`` detects the
fragmented-grid signature (a run of single-glyph cells) rather than filtering picture items —
same intent, a backend-shaped mechanism.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from papereyes.errors import PaperEyesError
from papereyes.sandbox import (
    PDFTOPPM_ADDRESS_SPACE_BYTES,
    PDFTOPPM_CPU_SECONDS,
    PDFTOPPM_FSIZE_BYTES,
    PDFTOPPM_TIMEOUT_S,
    run_bounded,
)

# granite-docling emits locations on a 0-500 normalised grid.
LOC_SCALE = 500

_LOC_ELEMENT = re.compile(
    r"<loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)>([^<\n]*)"
)


class OcrError(PaperEyesError):
    """Rasterisation or document-conversion produced no usable page content."""


@dataclass(frozen=True)
class LocatedElement:
    """One converted element: its bbox on the 0-500 loc grid, and its text."""

    x1: int
    y1: int
    x2: int
    y2: int
    text: str

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2


@dataclass
class Page:
    """A converted page: its 1-indexed number, raster size (px), image path, and elements."""

    page_no: int
    width_px: int
    height_px: int
    image_path: Path
    elements: list[LocatedElement] = field(default_factory=list)

    def loc_to_px(self, x: float, y: float) -> tuple[int, int]:
        return (round(x / LOC_SCALE * self.width_px), round(y / LOC_SCALE * self.height_px))

    def bbox_px(self, el: LocatedElement) -> tuple[int, int, int, int]:
        x1, y1 = self.loc_to_px(el.x1, el.y1)
        x2, y2 = self.loc_to_px(el.x2, el.y2)
        return (x1, y1, x2, y2)


def parse_doctags(raw: str) -> list[LocatedElement]:
    """Parse granite-docling located-text output into ordered, de-duplicated elements.

    Robust to the presence or absence of ``<doctag>``/``<text>``/``<otsl>`` structural wrappers:
    every ``<loc><loc><loc><loc>text`` run is an element. Exact-duplicate elements (a known
    repetition-loop failure mode of the converter) are collapsed to stabilise the reconstruction.
    """
    seen: set[tuple[int, int, int, int, str]] = set()
    out: list[LocatedElement] = []
    for m in _LOC_ELEMENT.finditer(raw):
        x1, y1, x2, y2 = (int(m.group(i)) for i in range(1, 5))
        text = m.group(5).strip()
        if not text:
            continue
        key = (x1, y1, x2, y2, text)
        if key in seen:
            continue
        seen.add(key)
        out.append(LocatedElement(x1=x1, y1=y1, x2=x2, y2=y2, text=text))
    return out


def group_rows(elements: list[LocatedElement], tol: int = 6) -> list[list[LocatedElement]]:
    """Group elements into visual rows by y-centre proximity, each row sorted left-to-right.

    Reading order for both the locators and the markdown reconstruction: rows top-to-bottom,
    elements within a row left-to-right.
    """
    rows: list[list[LocatedElement]] = []
    for el in sorted(elements, key=lambda e: (e.cy, e.x1)):
        for row in rows:
            if abs(row[0].cy - el.cy) <= tol:
                row.append(el)
                break
        else:
            rows.append([el])
    for row in rows:
        row.sort(key=lambda e: e.x1)
    rows.sort(key=lambda r: r[0].cy)
    return rows


def _require(exe_name: str) -> str:
    exe = shutil.which(exe_name)
    if exe is None:
        raise OcrError(f"{exe_name} not found — install poppler-utils to rasterise scans.")
    return exe


def rasterize_pdf_pages(
    pdf_path: str | Path, *, dpi: int, workdir: str | Path, last_page: int | None = None
) -> list[Path]:
    """Rasterise ``pdf_path`` pages to PNGs at ``dpi``; return the page PNGs in order.

    ``last_page`` limits the render to the first N pages — the identify pass rasterises only
    ``identify.pages`` at the fixed identify DPI (design spec §4.2).
    """
    exe = _require("pdftoppm")
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    prefix = work / "page"
    cmd = [exe, "-r", str(dpi), "-png"]
    if last_page is not None:
        cmd += ["-l", str(last_page)]
    cmd += [str(pdf_path), str(prefix)]
    # poppler runs at arm's length through the bounded-resource wrapper (rlimits + wall-clock
    # timeout, fixed argv, never a shell) — see papereyes.sandbox / THREAT-MODEL.md "crafted PDF".
    run_bounded(
        cmd,
        error_cls=OcrError,
        what="pdftoppm",
        timeout_s=PDFTOPPM_TIMEOUT_S,
        cpu_seconds=PDFTOPPM_CPU_SECONDS,
        address_space_bytes=PDFTOPPM_ADDRESS_SPACE_BYTES,
        fsize_bytes=PDFTOPPM_FSIZE_BYTES,
        check=True,
    )

    def page_no(p: Path) -> int:
        stem = p.stem.rsplit("-", 1)[-1]
        return int(stem) if stem.isdigit() else 0

    pages = sorted(work.glob("page-*.png"), key=page_no)
    if not pages:
        raise OcrError(f"pdftoppm produced no pages for {pdf_path}")
    return pages


def page_size(png_path: str | Path) -> tuple[int, int]:
    with Image.open(png_path) as img:
        w, h = img.size
        return (int(w), int(h))
