"""The extraction gate — the component's own acceptance mechanism (design spec §6 Stage 3, §7).

``papereyes gate formpacks/uk-ch2`` regenerates the golden scans from their committed seeds,
runs every scan end-to-end, flattens expected + actual to leaf paths, normalises them
(whitespace/case; dates -> ISO; NINO/postcode uppercased and de-spaced), prints a per-field diff
table, and enforces ``field_accuracy_floor`` + ``required_fields``. A passing run freezes
``formpack.baseline.json`` (per-field scores + formpack sha + model ids, content-hashed) — the
same no-relative-ratchet doctrine as deckhand's ``Baseline``: future formpack/model changes
re-gate against the floor, never against the previous score.

**No LLM judge anywhere in the scoring path** — the comparison is deterministic string equality
after normalisation. The scoring functions are pure and unit-tested with no models; only
``run_gate`` touches the endpoint.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from papereyes.config.models import Formpack, Pipeline
from papereyes.pipeline.client import ModelClient
from papereyes.pipeline.flatten import flatten_leaves
from papereyes.pipeline.run import run_pipeline

BASELINE_FILENAME = "formpack.baseline.json"

_DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%Y/%m/%d",
)


def _to_iso(text: str) -> str:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text.casefold()


def normalize_value(path: str, value: Any) -> str:
    """Normalise one leaf value for comparison (design spec §6 Stage 3 rules)."""
    if value is None:
        return ""
    s = re.sub(r"\s+", " ", str(value).strip())
    leaf = path.split(".")[-1].split("[")[0]
    if leaf in ("nino", "postcode"):
        return s.replace(" ", "").upper()
    if "date" in leaf or leaf == "dob":
        return _to_iso(s)
    return s.casefold()


@dataclass(frozen=True)
class FieldComparison:
    path: str
    expected: str
    actual: str
    match: bool


@dataclass
class DocScore:
    doc_id: str
    comparisons: list[FieldComparison]
    matched: int
    total: int
    missing_required: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.matched / self.total if self.total else 0.0

    @property
    def required_ok(self) -> bool:
        return not self.missing_required


def score_doc(
    doc_id: str, expected: dict[str, Any], actual: dict[str, Any], formpack: Formpack
) -> DocScore:
    """Score one doc's extraction against its expected JSON — deterministic, no model."""
    exp_leaves = flatten_leaves(expected)
    act_leaves = flatten_leaves(actual)
    comparisons: list[FieldComparison] = []
    matched = 0
    for path in sorted(exp_leaves):
        exp_n = normalize_value(path, exp_leaves[path])
        act_n = normalize_value(path, act_leaves.get(path))
        ok = exp_n == act_n
        matched += ok
        comparisons.append(
            FieldComparison(path=path, expected=exp_n, actual=act_n, match=ok)
        )
    missing_required = []
    for req in formpack.golden.required_fields:
        exp_n = normalize_value(req, exp_leaves.get(req))
        act_n = normalize_value(req, act_leaves.get(req))
        if exp_n != act_n:
            missing_required.append(req)
    return DocScore(
        doc_id=doc_id,
        comparisons=comparisons,
        matched=matched,
        total=len(exp_leaves),
        missing_required=missing_required,
    )


@dataclass
class GateResult:
    formpack_slug: str
    doc_scores: list[DocScore]
    floor: float
    models: dict[str, str]
    formpack_sha256: str

    @property
    def total_matched(self) -> int:
        return sum(d.matched for d in self.doc_scores)

    @property
    def total_fields(self) -> int:
        return sum(d.total for d in self.doc_scores)

    @property
    def overall_accuracy(self) -> float:
        return self.total_matched / self.total_fields if self.total_fields else 0.0

    @property
    def required_ok(self) -> bool:
        return all(d.required_ok for d in self.doc_scores)

    @property
    def passed(self) -> bool:
        return self.overall_accuracy >= self.floor and self.required_ok


def render_diff_table(result: GateResult) -> str:
    """A legible per-field diff table (the beat-2 'gate that says no' receipt)."""
    lines: list[str] = []
    lines.append(f"extraction gate — {result.formpack_slug}")
    lines.append(
        f"  models: docling={result.models.get('docling')} region={result.models.get('region')} "
        f"extract={result.models.get('extract')}"
    )
    lines.append("")
    for doc in result.doc_scores:
        lines.append(
            f"[{doc.doc_id}] {doc.matched}/{doc.total} = {doc.accuracy:.3f}"
            + ("" if doc.required_ok else f"  REQUIRED MISS: {', '.join(doc.missing_required)}")
        )
        for cmp in doc.comparisons:
            if not cmp.match:
                lines.append(f"    MISS {cmp.path}: expected {cmp.expected!r} got {cmp.actual!r}")
    lines.append("")
    verdict = "PASS" if result.passed else "FAIL"
    lines.append(
        f"overall: {result.total_matched}/{result.total_fields} = {result.overall_accuracy:.4f} "
        f"(floor {result.floor}) required_ok={result.required_ok} -> {verdict}"
    )
    return "\n".join(lines)


def build_baseline(result: GateResult) -> dict[str, Any]:
    """The frozen ``formpack.baseline.json`` payload, content-hashed (no relative ratchet)."""
    payload: dict[str, Any] = {
        "formpack": result.formpack_slug,
        "formpack_sha256": result.formpack_sha256,
        "models": result.models,
        "field_accuracy_floor": result.floor,
        "overall_accuracy": round(result.overall_accuracy, 4),
        "total_matched": result.total_matched,
        "total_fields": result.total_fields,
        "required_ok": result.required_ok,
        "per_doc": {
            d.doc_id: {
                "accuracy": round(d.accuracy, 4),
                "matched": d.matched,
                "total": d.total,
                "required_ok": d.required_ok,
                # per-field scores (design spec §6 Stage 3): every expected leaf, matched or not.
                "fields": {c.path: c.match for c in d.comparisons},
            }
            for d in result.doc_scores
        },
        "generated_by": "papereyes gate",
    }
    canonical = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    payload["content_hash"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    return payload


def write_baseline(formpack_dir: str | Path, result: GateResult) -> Path:
    path = Path(formpack_dir) / BASELINE_FILENAME
    payload = build_baseline(result)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _regenerate_scans_if_absent(formpack_dir: Path, formpack: Formpack) -> None:
    golden = formpack_dir / "golden"
    missing = [d for d in formpack.golden.docs if not (formpack_dir / d.scan).is_file()]
    if not missing:
        return
    from papereyes.synth.generator import synth_corpus

    synth_corpus(
        golden,
        base_seed=formpack.synth.base_seed,
        count=max(formpack.synth.count, len(formpack.golden.docs)),
        dpi=formpack.synth.dpi,
    )


def run_gate(
    formpack: Formpack,
    formpack_dir: str | Path,
    pipeline_cfg: Pipeline,
    client: ModelClient,
    *,
    workdir: str | Path,
    region_model: str | None = None,
    regenerate: bool = True,
) -> GateResult:
    """Run every golden scan end-to-end and score it — the real-model gate."""
    formpack_dir = Path(formpack_dir)
    workdir = Path(workdir)
    if regenerate:
        _regenerate_scans_if_absent(formpack_dir, formpack)

    formpack_sha = hashlib.sha256((formpack_dir / "formpack.yaml").read_bytes()).hexdigest()
    used_region = region_model or pipeline_cfg.models.vlm
    models = {
        "docling": pipeline_cfg.models.docling,
        "vlm": pipeline_cfg.models.vlm,
        "region": used_region,
        "extract": pipeline_cfg.models.extract,
    }

    doc_scores: list[DocScore] = []
    for doc in formpack.golden.docs:
        scan_path = formpack_dir / doc.scan
        expected = json.loads((formpack_dir / doc.expected).read_text(encoding="utf-8"))
        result = run_pipeline(
            scan_path,
            formpack,
            formpack_dir,
            pipeline_cfg,
            client,
            workdir=workdir / doc.id,
            region_model=region_model,
        )
        doc_scores.append(score_doc(doc.id, expected, result.extraction, formpack))

    return GateResult(
        formpack_slug=formpack.slug(),
        doc_scores=doc_scores,
        floor=formpack.golden.field_accuracy_floor,
        models=models,
        formpack_sha256=formpack_sha,
    )
