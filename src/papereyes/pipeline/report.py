"""Render the emitted review report (design spec §3 step 6, §4.3).

The report is the artifact deckhand's review UI shows: its **first 320 characters are the
snippet** the operator decides from (``web/registry.py`` ``SNIPPET_CHARS = 320``, verified), so
the headline fields lead. The report is **byte-deterministic** for a given (scan, formpack,
models): no timestamps, no timings — timings live only in ``provenance.json``, because any
volatile byte would change deckhand's ``stable_doc_key`` and defeat its idempotency (§3.1 #3).
"""

from __future__ import annotations

from typing import Any

from papereyes.config.models import Formpack
from papereyes.pipeline.flatten import flatten_leaves, resolve_path

RULE = "-" * 64


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def render_report(
    extraction: dict[str, Any],
    *,
    formpack: Formpack,
    scan_name: str,
    scan_sha256: str,
    formpack_sha256: str,
    docling_model: str,
    region_model: str,
    extract_model: str,
    regions_triggered: list[str],
    decoding_desc: str,
) -> str:
    """Return the deterministic report text (snippet-first, no timings)."""
    leaves = flatten_leaves(extraction)
    lines: list[str] = []

    # ── snippet-first headline (first ~320 chars) ─────────────────────────────────────
    lines.append(f"{formpack.display_name} — extracted from scan {scan_name}")
    headline = [
        f"{_fmt(resolve_path(extraction, p))}"
        for p in formpack.report.headline_fields
    ]
    labels = [p.split(".")[-1].split("[")[0] for p in formpack.report.headline_fields]
    pairs = zip(labels, headline, strict=False)
    lines.append(" | ".join(f"{lbl} {val}".strip() for lbl, val in pairs))
    lines.append(
        f"Fields: {len(leaves)} leaf fields | formpack {formpack.slug()} ({formpack_sha256[:12]}) "
        f"| models docling={docling_model} region={region_model} extract={extract_model}"
    )
    lines.append(RULE)

    # ── full field list (one click away in the same file) ─────────────────────────────
    lines.append("FIELDS")
    for path in sorted(leaves):
        lines.append(f"{path}: {_fmt(leaves[path])}")
    lines.append(RULE)

    # ── deterministic provenance subset (timings live in provenance.json) ─────────────
    lines.append("PROVENANCE (deterministic subset — timings live in provenance.json)")
    lines.append(f"source_scan_sha256: {scan_sha256}")
    lines.append(f"formpack: {formpack.slug()} ({formpack_sha256})")
    triggered = ", ".join(regions_triggered) if regions_triggered else "none"
    lines.append(f"regions_triggered: {triggered}")
    lines.append(f"decoding: {decoding_desc}")

    return "\n".join(lines) + "\n"
