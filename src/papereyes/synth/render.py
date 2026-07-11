"""Render a synthetic CH2-shaped form to a vector PDF (design spec §6 Stage 1).

This draws a form-shaped document **from scratch** with reportlab and a seeded persona — it is
NOT a reproduction of the Crown-copyright HMRC blank (no crest, no logo, no Royal Arms, no
departmental layout is copied). It deliberately reproduces the two *mechanism-relevant* layout
features the pipeline is built to handle:

- a **boxed-capital grid** for the National Insurance number — the kind of one-glyph-per-box
  grid a layout model routinely misroutes to a picture, which the ``picture_signature`` locator
  re-reads with the VLM;
- **heading spans** ("2 Children you're claiming for" ... "3 Higher income") that the
  ``heading_span`` locator brackets.

reportlab is put in invariant mode so the vector PDF is byte-reproducible for a given persona.
"""

from __future__ import annotations

from pathlib import Path

import reportlab.rl_config as rl_config
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from papereyes.synth.personas import Persona

# Byte-reproducible reportlab output: invariant mode fixes the document id and timestamps, so a
# given persona renders to identical bytes without depending on the wall clock.
rl_config.invariant = 1

# reportlab exposes no type information, so `mm` is untyped; pin it to a float for the typed
# geometry helpers below.
_MM: float = mm
_PAGE_W, _PAGE_H = A4
_MARGIN = 18 * _MM
_BOX = 7 * _MM  # one grid cell


def _heading(c: canvas.Canvas, y: float, text: str) -> float:
    c.setFont("Helvetica-Bold", 12)
    c.drawString(_MARGIN, y, text)
    c.setFont("Helvetica", 10)
    return y - 8 * _MM


def _field(c: canvas.Canvas, y: float, label: str, value: str) -> float:
    c.setFont("Helvetica", 9)
    c.drawString(_MARGIN, y, f"{label}")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(_MARGIN + 60 * mm, y, value)
    return y - 7 * _MM


def _boxed_grid(c: canvas.Canvas, y: float, label: str, text: str) -> float:
    c.setFont("Helvetica", 9)
    c.drawString(_MARGIN, y, label)
    x = _MARGIN + 60 * mm
    top = y + 3 * mm
    for ch in text:
        c.rect(x, top - _BOX, _BOX, _BOX, stroke=1, fill=0)
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(x + _BOX / 2, top - _BOX + 2 * mm, ch)
        x += _BOX + 1.5 * mm
    return y - 12 * _MM


def render_form(persona: Persona, out_pdf: str | Path) -> Path:
    """Render ``persona`` into a synthetic CH2-shaped vector PDF at ``out_pdf``; return the path."""
    out = Path(out_pdf)
    c = canvas.Canvas(str(out), pagesize=A4)
    c.setTitle("Child Benefit claim CH2 (synthetic)")

    # ── Page 1: the claimant ────────────────────────────────────────────────────────
    y = _PAGE_H - _MARGIN
    c.setFont("Helvetica-Bold", 16)
    c.drawString(_MARGIN, y, "Child Benefit claim")
    y -= 7 * mm
    c.setFont("Helvetica", 10)
    c.drawString(_MARGIN, y, "Form CH2 — claim Child Benefit for one or more children.")
    y -= 12 * mm

    y = _heading(c, y, "1 About you")
    y = _field(c, y, "Title", persona.title)
    y = _field(c, y, "First names", persona.first_names)
    y = _field(c, y, "Last name", persona.last_name)
    y = _field(c, y, "Date of birth", persona.date_of_birth)
    y = _boxed_grid(c, y, "National Insurance number", persona.nino)
    for i, line in enumerate(persona.address_lines):
        y = _field(c, y, "Address" if i == 0 else "", line)
    y = _field(c, y, "Postcode", persona.postcode)
    c.showPage()

    # ── Page 2: the children (heading span) then the stop heading ────────────────────
    c.setFont("Helvetica", 10)
    y = _PAGE_H - _MARGIN
    y = _heading(c, y, "2 Children you're claiming for")
    for idx, child in enumerate(persona.children, start=1):
        c.setFont("Helvetica-Bold", 10)
        c.drawString(_MARGIN, y, f"Child {idx}")
        y -= 6 * mm
        y = _field(c, y, "First names", child.first_names)
        y = _field(c, y, "Surname", child.last_name)
        y = _field(c, y, "Date of birth", child.date_of_birth)
        y -= 3 * mm
    y -= 4 * mm
    y = _heading(c, y, "3 Higher income")
    c.setFont("Helvetica", 9)
    c.drawString(
        _MARGIN,
        y,
        "If you or your partner has income over the threshold, the High Income Child Benefit",
    )
    y -= 5 * mm
    c.drawString(_MARGIN, y, "Charge may apply. This synthetic form models the layout only.")
    c.showPage()

    c.save()
    return out
