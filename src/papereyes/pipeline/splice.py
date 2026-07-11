"""Markdown reconstruction + the two splice modes (design spec §2 IN "two splice modes", §6).

The converted pages are reconstructed into a single markdown string in reading order. A
``picture_signature`` region (the boxed grid) is collapsed to a single ``<!-- image -->``
placeholder at its position — mirroring how the local Docling pipeline emits a placeholder for a
``PictureItem`` — so the ``replace_placeholder`` splice can substitute the VLM re-read there. A
``heading_span`` region keeps its OCR text intact and the ``insert_after_anchor`` splice adds the
re-read *after* the anchor line, non-destructively (INSERT, never REPLACE, for spans — so a
locator drift never deletes good text; design spec §4.1).

Both splices are plain, deterministic string operations — unit-tested with no models.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from papereyes.pipeline.locate import RegionMatch
from papereyes.pipeline.ocr import Page, group_rows

PLACEHOLDER = "<!-- image -->"

_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*|\s*```\s*$")
_MD_WRAP = re.compile(r"</?md>", re.IGNORECASE)
_LOC_TAG = re.compile(r"<loc_\d+>")
_DOCTAG = re.compile(r"</?(?:doctag|text|otsl|picture|caption|section_header|page_footer)>",
                     re.IGNORECASE)


def clean_region_text(raw: str) -> str:
    """Strip ``<md>``/code-fence/DocTag wrappers from a VLM re-read; keep the plain lines.

    The served Docling VLM returns location-tagged lines; the region re-read only needs the text,
    so loc tags and structural tags are removed and blank lines collapsed. Deterministic.
    """
    text = _MD_WRAP.sub("", raw)
    text = _DOCTAG.sub("", text)
    text = _LOC_TAG.sub("", text)
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not _FENCE.match(ln)]
    return "\n".join(lines)


def pick_grid_value(reread_clean: str, fallback: str) -> str:
    """Choose the grid field value: the VLM re-read when it is a clean token, else the fallback.

    The picture region's ``fallback`` is the boxed cells joined from the bulk conversion. The
    bulk pass counts the cells, so the grid's length is known: the re-read replaces the fallback
    only when it is a clean alphanumeric token of exactly that length — a different-length
    re-read is a misread, and the bulk value stands (graceful degradation, §9 Risk 1). With no
    fallback (the region carried no legible cells) any clean token is accepted.
    """
    tokens = [t for t in re.split(r"\s+", reread_clean) if t]
    joined = "".join(t for t in tokens if len(t) == 1 and t.isalnum())
    candidate = joined or "".join(ch for ch in reread_clean if ch.isalnum())
    if not candidate or not candidate.isalnum():
        return fallback
    if fallback and len(candidate) != len(fallback):
        return fallback
    return candidate


@dataclass
class Reconstruction:
    """The reconstructed markdown plus the ordered region ids of its ``<!-- image -->`` slots."""

    markdown: str
    placeholder_region_ids: list[str]


def reconstruct_markdown(pages: list[Page], picture_matches: list[RegionMatch]) -> Reconstruction:
    """Join the converted pages into markdown, collapsing each picture region to a placeholder.

    Only ``picture_signature`` members are collapsed; a picture region's first member becomes the
    ``<!-- image -->`` slot (in document order) and its remaining members are dropped from the
    text. Everything else is emitted as ``row`` lines (elements joined left-to-right).
    """
    member_set = {el for m in picture_matches for el in m.members}
    first_member = {m.members[0]: m.region_id for m in picture_matches if m.members}

    lines: list[str] = []
    slot_ids: list[str] = []
    for page in pages:
        for row in group_rows(page.elements):
            parts: list[str] = []
            for el in row:
                if el in member_set:
                    if el in first_member:
                        parts.append(PLACEHOLDER)
                        slot_ids.append(first_member[el])
                    continue
                parts.append(el.text)
            if parts:
                lines.append(" ".join(parts))
    return Reconstruction(markdown="\n".join(lines), placeholder_region_ids=slot_ids)


def apply_replace_placeholder(markdown: str, k: int, text: str) -> str:
    """Replace the ``k``-th (0-based) ``<!-- image -->`` placeholder with ``text``.

    If there is no k-th placeholder the markdown is returned unchanged (the region degraded to a
    no-op; design spec §9 Risk 1).
    """
    out: list[str] = []
    idx = 0
    pos = 0
    replacement = text if text else ""
    while True:
        found = markdown.find(PLACEHOLDER, pos)
        if found == -1:
            out.append(markdown[pos:])
            break
        out.append(markdown[pos:found])
        if idx == k:
            out.append(replacement)
        else:
            out.append(PLACEHOLDER)
        pos = found + len(PLACEHOLDER)
        idx += 1
    return "".join(out)


def apply_insert_after_anchor(markdown: str, anchor: str, text: str) -> str:
    """Insert ``text`` on the line after the first line matching the ``anchor`` regex.

    Non-destructive: the anchor line and all existing text are preserved. If the anchor does not
    match, the markdown is returned unchanged (region no-op).
    """
    if not text:
        return markdown
    anchor_re = re.compile(anchor)
    lines = markdown.splitlines()
    for i, line in enumerate(lines):
        if anchor_re.search(line):
            insert_at = i + 1
            new_lines = lines[:insert_at] + text.splitlines() + lines[insert_at:]
            return "\n".join(new_lines)
    return markdown
