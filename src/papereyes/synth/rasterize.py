"""Rasterise a vector PDF into an **image-only** PDF (design spec §6 Stage 1).

Pipeline: ``pdftoppm`` (poppler) renders each page to a PNG at the target DPI; each PNG is
re-encoded through Pillow to normalise away any renderer metadata; ``img2pdf`` losslessly wraps
the page rasters into a single PDF whose pages carry no extractable text layer. The result is
guaranteed image-only — exactly the corpus the OCR pipeline is meant to consume, and the reason
``pdftotext`` yields nothing.

Reproducibility: img2pdf's ``internal`` engine (no random document /ID) with fixed
creation/mod dates, plus reportlab's invariant mode and Pillow-normalised page rasters, make
the scan bytes identical run to run for a given persona.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import img2pdf
from PIL import Image

from papereyes.errors import SynthError

# A fixed instant so img2pdf embeds a stable creation/mod date AND a stable document /ID
# (its /ID is an md5 derived from the date + producer). This is what makes the rendered
# scan byte-reproducible run to run, alongside reportlab's invariant mode.
FIXED_DATETIME = datetime.fromtimestamp(1_700_000_000, tz=UTC)


def _require_pdftoppm() -> str:
    exe = shutil.which("pdftoppm")
    if exe is None:
        raise SynthError(
            "pdftoppm not found — install poppler-utils (Debian/Ubuntu) or poppler (macOS) "
            "to rasterise the synthetic corpus."
        )
    return exe


def _page_pngs(prefix: Path) -> list[Path]:
    pages = list(prefix.parent.glob(f"{prefix.name}-*.png"))

    def page_no(p: Path) -> int:
        stem = p.stem.rsplit("-", 1)[-1]
        return int(stem) if stem.isdigit() else 0

    return sorted(pages, key=page_no)


def rasterize_to_image_pdf(
    vector_pdf: str | Path, out_pdf: str | Path, *, dpi: int, workdir: str | Path
) -> Path:
    """Rasterise ``vector_pdf`` to an image-only PDF at ``out_pdf``; return the path.

    Raises :class:`~papereyes.errors.SynthError` if ``pdftoppm`` is missing or produces no pages.
    """
    exe = _require_pdftoppm()
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    prefix = work / "page"

    subprocess.run(
        [exe, "-r", str(dpi), "-png", str(vector_pdf), str(prefix)],
        check=True,
        capture_output=True,
    )
    raw_pages = _page_pngs(prefix)
    if not raw_pages:
        raise SynthError(f"pdftoppm produced no pages for {vector_pdf}")

    normalised: list[Path] = []
    for i, page in enumerate(raw_pages, start=1):
        norm = work / f"norm-{i}.png"
        with Image.open(page) as img:
            img.convert("RGB").save(norm, format="PNG")
        normalised.append(norm)

    layout = img2pdf.get_fixed_dpi_layout_fun((dpi, dpi))
    # The `internal` engine writes no random document /ID (the pikepdf engine's /ID varies
    # run to run); with fixed dates the output PDF is byte-reproducible.
    pdf_bytes = img2pdf.convert(
        [str(p) for p in normalised],
        layout_fun=layout,
        creationdate=FIXED_DATETIME,
        moddate=FIXED_DATETIME,
        engine=img2pdf.Engine.internal,
    )

    out = Path(out_pdf)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pdf_bytes)
    return out
