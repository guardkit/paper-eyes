"""The pipeline orchestrator (design spec §3, §6 Stage 2).

``run_pipeline`` turns one scanned PDF into ``(report text, extraction JSON, provenance)``:

1. **identify** — rasterise the first ``identify.pages`` pages at the fixed identify DPI
   (formpack-independent defaults, design spec §4.2), convert them with the served Docling VLM,
   and match the formpack's ``all_of_patterns`` — routing, timed separately so the reported
   runtime is honest about what routing costs;
2. **bulk conversion** — rasterise every page at the formpack's scale and convert each exactly
   once, after routing, with the winning formpack's settings (the "Docling" pass);
3. **locate** the formpack regions; **crop** each tightly and **re-read** with the pinned region
   VLM (sequential, pinned decoding);
4. **splice** the re-reads into the reconstructed markdown (``replace_placeholder`` /
   ``insert_after_anchor``);
5. **extract** structured JSON (pinned, ``response_format`` = the formpack schema, reasoning off);
6. render the deterministic **report** and the **provenance** sidecar (per-stage timings).

Every model call is sequential and pinned. Timings live only in provenance — never in the report.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from papereyes.config.models import Formpack, PictureSignatureLocator, Pipeline
from papereyes.errors import PaperEyesError
from papereyes.pipeline.client import ModelClient, resolve_base_url
from papereyes.pipeline.extract import extract_fields
from papereyes.pipeline.flatten import resolve_path
from papereyes.pipeline.locate import crop_region, fallback_match, locate_regions
from papereyes.pipeline.ocr import Page, page_size, parse_doctags, rasterize_pdf_pages
from papereyes.pipeline.report import render_report
from papereyes.pipeline.splice import (
    apply_insert_after_anchor,
    apply_replace_placeholder,
    clean_region_text,
    pick_grid_value,
    reconstruct_markdown,
)

__all__ = ["PAPEREYES_PIPELINE_VERSION", "RunResult", "run_pipeline"]

PAPEREYES_PIPELINE_VERSION = "0.1.0"
_CONVERT_MAX_TOKENS = 4096


class IdentifyError(PaperEyesError):
    """The identify pass did not match the formpack's routing patterns."""


@dataclass
class RunResult:
    """Everything one pipeline run produced."""

    report_text: str
    extraction: dict[str, Any]
    provenance: dict[str, Any]
    scan_sha256: str
    report_name: str
    crops: list[Path] = field(default_factory=list)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_schema(formpack_dir: Path, formpack: Formpack) -> dict[str, Any]:
    schema_path = formpack_dir / formpack.extraction.schema_file
    data: dict[str, Any] = json.loads(schema_path.read_text(encoding="utf-8"))
    return data


def _convert_pages(
    client: ModelClient, page_pngs: list[Path]
) -> tuple[list[Page], float, list[dict[str, Any]]]:
    """Convert ``page_pngs`` with the served Docling VLM into Page objects; return timing."""
    pages: list[Page] = []
    calls: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    for i, png in enumerate(page_pngs):
        w, h = page_size(png)
        result = client.convert_page(png.read_bytes(), max_tokens=_CONVERT_MAX_TOKENS)
        calls.append(
            {"kind": "convert_page", "model": result.model, "latency_s": round(result.latency_s, 3)}
        )
        pages.append(
            Page(
                page_no=i + 1,
                width_px=w,
                height_px=h,
                image_path=png,
                elements=parse_doctags(result.text),
            )
        )
    return pages, time.perf_counter() - t0, calls


def run_pipeline(
    scan_path: str | Path,
    formpack: Formpack,
    formpack_dir: str | Path,
    pipeline_cfg: Pipeline,
    client: ModelClient,
    *,
    workdir: str | Path,
    region_model: str | None = None,
) -> RunResult:
    """Execute the full pipeline for one scan; return the report, extraction and provenance."""
    scan = Path(scan_path)
    formpack_dir = Path(formpack_dir)
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    crops_dir = work / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    scan_sha = _sha256_file(scan)
    formpack_yaml = formpack_dir / "formpack.yaml"
    formpack_sha = _sha256_file(formpack_yaml)
    schema = _load_schema(formpack_dir, formpack)

    timings: dict[str, Any] = {}
    model_calls: list[dict[str, Any]] = []
    total_t0 = time.perf_counter()

    # ── identify pass: first N pages at FIXED defaults (formpack-independent routing) ──
    id_t0 = time.perf_counter()
    id_pngs = rasterize_pdf_pages(
        scan,
        dpi=pipeline_cfg.identify.dpi,
        workdir=work / "identify",
        last_page=pipeline_cfg.identify.pages,
    )
    id_pages, _, id_calls = _convert_pages(client, id_pngs)
    model_calls += id_calls
    timings["identify"] = round(time.perf_counter() - id_t0, 3)
    id_text = "\n".join(e.text for p in id_pages for e in p.elements)
    for pattern in formpack.identify.all_of_patterns:
        if not re.search(pattern, id_text):
            raise IdentifyError(
                f"{scan.name}: identify pattern {pattern!r} did not match — not a "
                f"{formpack.slug()} document (matched text: {id_text[:120]!r})"
            )

    # ── bulk conversion: every page exactly once, after routing, at formpack scale ────
    full_dpi = round(100 * formpack.docling.images_scale)
    page_pngs = rasterize_pdf_pages(scan, dpi=full_dpi, workdir=work / "pages")
    n_pages = len(page_pngs)
    pages, ocr_time, bulk_calls = _convert_pages(client, page_pngs)
    model_calls += bulk_calls
    timings["ocr"] = round(ocr_time, 3)

    # ── locate + reconstruct markdown ─────────────────────────────────────────────────
    matches = locate_regions(formpack.regions, pages)
    picture_matches = [
        m for m, r in ((m, _region_by_id(formpack, m.region_id)) for m in matches)
        if isinstance(r.locate, PictureSignatureLocator)
    ]
    recon = reconstruct_markdown(pages, picture_matches)
    markdown = recon.markdown

    # ── per-region VLM re-read (sequential) + splice ──────────────────────────────────
    vlm_timings: dict[str, float] = {}
    crops_saved: list[Path] = []
    regions_triggered: list[str] = []
    region_pngs: dict[str, bytes] = {}
    # Splice picture regions at their placeholder slot, span regions after their anchor.
    for match in matches:
        region = _region_by_id(formpack, match.region_id)
        page = pages[match.page_index]
        crop = crop_region(page, match)
        crop_path = crops_dir / f"{match.region_id}-p{match.page_no}.png"
        crop.image.save(crop_path)
        crops_saved.append(crop_path)
        region_pngs[match.region_id] = _png_bytes(crop.image)

        t0 = time.perf_counter()
        reread = client.read_region(
            region.vlm.prompt, region_pngs[match.region_id], max_tokens=region.vlm.max_tokens
        )
        vlm_timings[match.region_id] = round(time.perf_counter() - t0, 3)
        model_calls.append(
            {"kind": "read_region", "region": match.region_id, "model": reread.model,
             "latency_s": round(reread.latency_s, 3)}
        )
        reread_clean = clean_region_text(reread.text)
        regions_triggered.append(match.region_id)

        if isinstance(region.locate, PictureSignatureLocator):
            value = pick_grid_value(reread_clean, match.fallback_text)
            k = recon.placeholder_region_ids.index(match.region_id)
            markdown = apply_replace_placeholder(markdown, k, value)
        else:
            splice_text = reread_clean if _looks_field_like(reread_clean) else ""
            markdown = apply_insert_after_anchor(markdown, region.locate.anchor, splice_text)

    timings["vlm"] = vlm_timings

    # ── extraction ────────────────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    extraction, extract_result = extract_fields(
        client,
        system_prompt=formpack.extraction.system_prompt,
        markdown=markdown,
        schema=schema,
        max_tokens=formpack.extraction.max_tokens,
    )
    timings["extract"] = round(time.perf_counter() - t0, 3)
    model_calls.append(
        {"kind": "extract", "model": extract_result.model,
         "latency_s": round(extract_result.latency_s, 3)}
    )

    # ── strict-format reject-and-re-ask (one bounded re-ask per declared field) ───────
    # The proven pattern for identifier-shaped fields: a value that fails its declared
    # format triggers ONE character-precise re-ask of the region crop; the reply is adopted
    # only if it passes the same format. A region whose signature never fired uses its
    # pinned fallback bbox to produce the crop (and the saved receipt) first.
    reasks: list[dict[str, Any]] = []
    for region in formpack.regions:
        rk = region.vlm.reask
        if rk is None:
            continue
        fmt_re = re.compile(rk.format)
        current = resolve_path(extraction, rk.field)
        norm = _normalize_id_value(current)
        if current is not None and fmt_re.fullmatch(norm):
            if norm != current:
                _set_path(extraction, rk.field, norm)
                reasks.append({"region": region.id, "field": rk.field,
                               "outcome": "normalized", "was": current})
            continue
        t0 = time.perf_counter()
        pinned = fallback_match(region, pages)
        candidate = ""
        raw_reply = ""
        if rk.boxes is not None and pinned is not None:
            # Per-box: segment the PINNED bbox (exact by construction — a detected-signature
            # bbox drifts) into equal-pitch cells; one single-character ask per cell. Immune
            # to the whole-row repeated-character collapse seen at pinned decoding.
            page = pages[pinned.page_index]
            row = crop_region(page, pinned)
            row_path = crops_dir / f"{region.id}-p{pinned.page_no}-reask-row.png"
            row.image.save(row_path)
            crops_saved.append(row_path)
            if region.id not in region_pngs:
                regions_triggered.append(f"{region.id} (fallback bbox)")
            chars: list[str] = []
            for cell_png in _grid_cells(page, pinned.bbox_loc, rk.boxes):
                rr = client.read_region(_CELL_PROMPT, cell_png, max_tokens=8)
                model_calls.append(
                    {"kind": "reask_cell", "region": region.id, "field": rk.field,
                     "model": rr.model, "latency_s": round(rr.latency_s, 3)}
                )
                chars.append(_normalize_id_value(clean_region_text(rr.text))[:1])
            candidate = "".join(chars)
            raw_reply = candidate
        else:
            png = region_pngs.get(region.id)
            if png is None:
                if pinned is None:
                    reasks.append({"region": region.id, "field": rk.field,
                                   "outcome": "no_crop", "was": current})
                    continue
                page = pages[pinned.page_index]
                crop = crop_region(page, pinned)
                crop_path = crops_dir / f"{region.id}-p{pinned.page_no}-fallback.png"
                crop.image.save(crop_path)
                crops_saved.append(crop_path)
                regions_triggered.append(f"{region.id} (fallback bbox)")
                png = _png_bytes(crop.image)
                region_pngs[region.id] = png
            rr = client.read_region(rk.prompt, png, max_tokens=rk.max_tokens)
            model_calls.append(
                {"kind": "reask", "region": region.id, "field": rk.field, "model": rr.model,
                 "latency_s": round(rr.latency_s, 3)}
            )
            candidate = _normalize_id_value(clean_region_text(rr.text))
            raw_reply = rr.text[:80]
        if candidate and fmt_re.fullmatch(candidate):
            _set_path(extraction, rk.field, candidate)
            reasks.append({"region": region.id, "field": rk.field,
                           "outcome": "repaired", "was": current})
        else:
            reasks.append({"region": region.id, "field": rk.field, "outcome": "unrepaired",
                           "was": current, "reask_raw": raw_reply})
        timings.setdefault("reask", {})[region.id] = round(time.perf_counter() - t0, 3)

    timings["total"] = round(time.perf_counter() - total_t0, 3)

    # ── report + provenance ───────────────────────────────────────────────────────────
    used_region_model = region_model or pipeline_cfg.models.vlm
    decoding_desc = (
        f"temperature={pipeline_cfg.decoding.temperature} "
        f"seed={pipeline_cfg.decoding.seed} (all calls)"
    )
    report_text = render_report(
        extraction,
        formpack=formpack,
        scan_name=scan.name,
        scan_sha256=scan_sha,
        formpack_sha256=formpack_sha,
        docling_model=pipeline_cfg.models.docling,
        region_model=used_region_model,
        extract_model=pipeline_cfg.models.extract,
        regions_triggered=regions_triggered,
        decoding_desc=decoding_desc,
    )

    provenance = _build_provenance(
        formpack=formpack,
        formpack_sha=formpack_sha,
        scan=scan,
        scan_sha=scan_sha,
        n_pages=n_pages,
        pipeline_cfg=pipeline_cfg,
        used_region_model=used_region_model,
        regions_triggered=regions_triggered,
        timings=timings,
        crops=crops_saved,
        model_calls=model_calls,
        reasks=reasks,
    )

    report_name = f"{scan.stem}--{scan_sha[:8]}--fp{formpack.version}"
    (work / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return RunResult(
        report_text=report_text,
        extraction=extraction,
        provenance=provenance,
        scan_sha256=scan_sha,
        report_name=report_name,
        crops=crops_saved,
    )


def _region_by_id(formpack: Formpack, region_id: str) -> Any:
    for region in formpack.regions:
        if region.id == region_id:
            return region
    raise KeyError(region_id)


def _looks_field_like(text: str) -> bool:
    """A conservative guard: only insert a span re-read that reads like field lines."""
    return bool(text) and (":" in text or any(c.isalpha() for c in text)) and len(text) < 2000


_CELL_PROMPT = (
    "This is one box from a form containing one printed character. "
    "Reply with ONLY that single character."
)


def _normalize_id_value(value: Any) -> str:
    """Normalize an identifier-shaped value for format matching: uppercase A-Z0-9 only."""
    if value is None:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def _grid_cells(
    page: Page, bbox_loc: tuple[float, float, float, float], n: int, *, upscale: int = 4
) -> list[bytes]:
    """Segment a pinned grid bbox into ``n`` equal-pitch cell crops (upscaled PNGs)."""
    from PIL import Image

    x1, y1 = page.loc_to_px(bbox_loc[0], bbox_loc[1])
    x2, y2 = page.loc_to_px(bbox_loc[2], bbox_loc[3])
    cells: list[bytes] = []
    with Image.open(page.image_path) as raw:
        img = raw.convert("RGB")
        _, h = img.size
        my = round(0.005 * h)
        pitch = (x2 - x1) / n
        for i in range(n):
            cell = img.crop(
                (round(x1 + i * pitch), max(0, y1 - my),
                 round(x1 + (i + 1) * pitch), min(h, y2 + my))
            )
            cell = cell.resize((cell.width * upscale, cell.height * upscale), Image.Resampling.LANCZOS)
            cells.append(_png_bytes(cell))
    return cells


def _set_path(obj: Any, path: str, value: Any) -> None:
    """Set a leaf path (``a.b[0].c`` — resolve_path's grammar) in-place; no-op if absent."""
    tokens = [t for t in path.replace("]", "").replace("[", ".").split(".") if t != ""]
    cur = obj
    for token in tokens[:-1]:
        if isinstance(cur, dict):
            if token not in cur:
                return
            cur = cur[token]
        elif isinstance(cur, list):
            try:
                cur = cur[int(token)]
            except (ValueError, IndexError):
                return
        else:
            return
    last = tokens[-1]
    if isinstance(cur, dict):
        cur[last] = value
    elif isinstance(cur, list):
        try:
            cur[int(last)] = value
        except (ValueError, IndexError):
            return


def _png_bytes(img: Any) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _build_provenance(
    *,
    formpack: Formpack,
    formpack_sha: str,
    scan: Path,
    scan_sha: str,
    n_pages: int,
    pipeline_cfg: Pipeline,
    used_region_model: str,
    regions_triggered: list[str],
    timings: dict[str, Any],
    crops: list[Path],
    model_calls: list[dict[str, Any]],
    reasks: list[dict[str, Any]],
) -> dict[str, Any]:
    dec = {
        "temperature": pipeline_cfg.decoding.temperature,
        "seed": pipeline_cfg.decoding.seed,
    }
    deviations: list[str] = [
        # Recorded on EVERY run: the design spec's bulk pass is the local Docling
        # StandardPdfPipeline; on this build host it is served instead (see pipeline/ocr.py).
        f"bulk conversion served by {pipeline_cfg.models.docling!r} (the Docling project's "
        "document-conversion VLM) on the pinned endpoint — the local Docling "
        "StandardPdfPipeline is not runnable on the build host (no tesseract binary, no sudo)"
    ]
    if used_region_model != pipeline_cfg.models.vlm:
        deviations.append(
            f"region re-read model is {used_region_model!r}, not the pinned "
            f"{pipeline_cfg.models.vlm!r} (explicit --region-model override)"
        )
    return {
        "papereyes_version": PAPEREYES_PIPELINE_VERSION,
        "formpack": formpack.slug(),
        "formpack_sha256": formpack_sha,
        "source_scan": {"name": scan.name, "sha256": scan_sha, "pages": n_pages},
        "endpoint_base_url": resolve_base_url(pipeline_cfg.endpoint.base_url),
        "models": {
            "docling": pipeline_cfg.models.docling,
            "vlm": pipeline_cfg.models.vlm,
            "region": used_region_model,
            "extract": pipeline_cfg.models.extract,
        },
        "decoding": {"convert": dec, "region": dec, "extract": dec},
        "serving_note": pipeline_cfg.serving_note or "sequential single-slot requests assumed",
        "deviations": deviations,
        "regions_triggered": regions_triggered,
        "timings_s": timings,
        "crops": [str(p.name) for p in crops],
        "model_calls": model_calls,
        "reasks": reasks,
    }
