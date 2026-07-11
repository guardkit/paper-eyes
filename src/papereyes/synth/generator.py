"""Orchestrate synthetic-corpus generation (design spec §6 Stage 1).

``papereyes synth`` turns a base seed + count into N ``(scan, expected-JSON)`` pairs:

    seed -> persona -> render (reportlab) -> rasterise (image-only) -> scan.pdf
                    -> expected_json                                 -> *.expected.json

Committed to the repo: the expected JSONs and a ``seeds.json`` manifest (base seed, per-doc
seed, expected sha256). **Not** committed: the scan PDFs — they regenerate deterministically
from the seeds (OGL crest exclusion; enforced by ``.gitignore`` + a test).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from papereyes.synth.personas import expected_json, generate_persona, persona_seeds
from papereyes.synth.rasterize import rasterize_to_image_pdf
from papereyes.synth.render import render_form

SEEDS_MANIFEST = "seeds.json"


def expected_json_bytes(expected: dict[str, object]) -> bytes:
    """Canonical, deterministic bytes for an expected-JSON file (sorted keys, trailing newline)."""
    return (json.dumps(expected, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


@dataclass(frozen=True)
class DocResult:
    """One generated golden doc: its id, seed, relative paths, and expected-JSON sha."""

    id: str
    seed: int
    scan: str
    expected: str
    expected_sha256: str
    scan_written: bool


@dataclass(frozen=True)
class CorpusResult:
    """The outcome of a synth run over one formpack."""

    base_seed: int
    count: int
    dpi: int
    docs: tuple[DocResult, ...]


def _doc_id(index: int) -> str:
    return f"persona-{index:02d}"


def synth_corpus(
    golden_dir: str | Path,
    *,
    base_seed: int,
    count: int,
    dpi: int,
    expected_only: bool = False,
    workdir: str | Path | None = None,
) -> CorpusResult:
    """Generate the corpus into ``golden_dir``; return the manifest.

    ``expected_only`` writes just the ground-truth JSONs (no rasteriser / poppler needed) — the
    path used to (re)author committed truth. Otherwise scans are rendered too.
    """
    golden = Path(golden_dir)
    golden.mkdir(parents=True, exist_ok=True)
    seeds = persona_seeds(base_seed, count)

    tmp_ctx: tempfile.TemporaryDirectory[str] | None = None
    if workdir is None and not expected_only:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="papereyes-synth-")
        work_root = Path(tmp_ctx.name)
    else:
        work_root = Path(workdir) if workdir is not None else golden

    docs: list[DocResult] = []
    try:
        for index, seed in enumerate(seeds, start=1):
            doc_id = _doc_id(index)
            persona = generate_persona(seed)

            expected = expected_json(persona)
            payload = expected_json_bytes(expected)
            expected_name = f"{doc_id}.expected.json"
            (golden / expected_name).write_bytes(payload)
            expected_sha = hashlib.sha256(payload).hexdigest()

            scan_name = f"{doc_id}.pdf"
            scan_written = False
            if not expected_only:
                vector = work_root / f"{doc_id}.vector.pdf"
                render_form(persona, vector)
                page_work = work_root / doc_id
                rasterize_to_image_pdf(vector, golden / scan_name, dpi=dpi, workdir=page_work)
                scan_written = True

            docs.append(
                DocResult(
                    id=doc_id,
                    seed=seed,
                    scan=f"golden/{scan_name}",
                    expected=f"golden/{expected_name}",
                    expected_sha256=expected_sha,
                    scan_written=scan_written,
                )
            )
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()

    _write_manifest(golden, base_seed=base_seed, count=count, dpi=dpi, docs=docs)
    return CorpusResult(base_seed=base_seed, count=count, dpi=dpi, docs=tuple(docs))


def _write_manifest(
    golden: Path, *, base_seed: int, count: int, dpi: int, docs: list[DocResult]
) -> None:
    manifest = {
        "base_seed": base_seed,
        "count": count,
        "dpi": dpi,
        "note": (
            "Scans regenerate from these seeds via `papereyes synth`; only expected JSONs + "
            "this manifest are committed (scan PDFs are gitignored, OGL crest exclusion)."
        ),
        "docs": [
            {
                "id": d.id,
                "seed": d.seed,
                "scan": d.scan,
                "expected": d.expected,
                "expected_sha256": d.expected_sha256,
            }
            for d in docs
        ],
    }
    payload = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    (golden / SEEDS_MANIFEST).write_text(payload, encoding="utf-8")
