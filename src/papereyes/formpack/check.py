"""``papereyes check`` — validate a formpack (or pipeline) as data (design spec §6 Stage 0).

``check`` answers one question: *is this config well-formed as data?* It loads the model
(``extra='forbid'``, ``yaml.safe_load`` only), confirms the extraction ``schema.json`` exists
and is parseable JSON, and confirms every locator/splice is one of the known kinds (enforced
by the discriminated union). It does NOT run the pipeline or require golden scans to exist —
that is ``papereyes gate`` (Stage 3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from papereyes.config.loader import FORMPACK_FILENAME, load_formpack, load_pipeline
from papereyes.errors import ConfigError


@dataclass
class CheckReport:
    """The result of a ``check`` run — an ``ok`` flag, error lines, and notes."""

    target: str
    ok: bool
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        head = "OK  " if self.ok else "FAIL  "
        lines = [f"{head}{self.target}"]
        lines += [f"    note: {n}" for n in self.notes]
        lines += [f"    error: {e}" for e in self.errors]
        return "\n".join(lines)


def check_formpack_dir(formpack_dir: str | Path) -> CheckReport:
    """Validate the formpack rooted at ``formpack_dir``.

    Never raises for a *content* fault — a malformed formpack yields ``ok=False`` with the
    reason in ``errors``. (An unreadable directory still surfaces as an error line.)
    """
    d = Path(formpack_dir)
    report = CheckReport(target=str(d), ok=True)
    try:
        formpack = load_formpack(d)
    except ConfigError as exc:
        report.ok = False
        report.errors.append(str(exc))
        return report

    report.notes.append(f"{formpack.slug()} — {formpack.display_name}")
    report.notes.append(
        f"{len(formpack.regions)} region(s), "
        f"floor {formpack.golden.field_accuracy_floor}, "
        f"{len(formpack.golden.docs)} golden doc(s)"
    )

    schema_path = d / formpack.extraction.schema_file
    if not schema_path.is_file():
        report.ok = False
        report.errors.append(f"extraction schema not found: {schema_path}")
    else:
        try:
            json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.ok = False
            report.errors.append(f"extraction schema is not valid JSON ({schema_path}): {exc}")

    if not formpack.golden.docs:
        report.notes.append("golden set is empty (add seeded docs before `papereyes gate`)")
    return report


def check_pipeline_file(pipeline_path: str | Path) -> CheckReport:
    """Validate a ``pipeline.yaml`` as data."""
    p = Path(pipeline_path)
    report = CheckReport(target=str(p), ok=True)
    try:
        pipeline = load_pipeline(p)
    except ConfigError as exc:
        report.ok = False
        report.errors.append(str(exc))
        return report
    report.notes.append(
        f"models vlm={pipeline.models.vlm} extract={pipeline.models.extract}; "
        f"decoding t={pipeline.decoding.temperature} seed={pipeline.decoding.seed}"
    )
    return report


def check_target(target: str | Path) -> CheckReport:
    """Dispatch ``check`` by what ``target`` is.

    A directory (or a path ending in ``formpack.yaml``) is checked as a formpack; any other
    file is checked as a pipeline config.
    """
    p = Path(target)
    if p.is_dir() or p.name == FORMPACK_FILENAME:
        return check_formpack_dir(p.parent if p.name == FORMPACK_FILENAME else p)
    return check_pipeline_file(p)
