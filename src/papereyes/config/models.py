"""The validated formpack + pipeline models (design spec §4).

A **formpack** is per-form-type calibration expressed as pure data: how to route to it
(``identify``), how to OCR it (``docling``), which regions a small VLM must re-read
(``regions``), how to extract structured JSON (``extraction``), what the review report
shows first (``report``), and how it is graded (``golden``). A formpack is loaded with
``yaml.safe_load`` and **never executed** — the same config-is-data law deckhand holds.

A **pipeline** config is the serving side: the one OpenAI-compatible endpoint, the two
model ids, the globally pinned decoding, the cheap identify pass, and the emit/watch dirs.

Extra keys are forbidden everywhere: an unknown field in a file meant to be audited by
its owner is a smell, not a convenience.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# ── formpack ────────────────────────────────────────────────────────────────────────


class SourceForm(BaseModel):
    """Provenance of the public blank a formpack models (design spec §4.1).

    The blank itself is **never committed** (Crown-copyright crests/logos/Royal Arms are
    excluded from OGL v3). Only the ``url`` and the ``sha256`` pin live here; ``fetch-forms``
    verifies them at build time. ``sha256`` is optional because the default synthetic corpus
    is rendered from scratch (``synth.mode: render``) and needs no fetched blank.
    """

    model_config = ConfigDict(extra="forbid")

    url: str
    sha256: str | None = None
    licence: str


class IdentifyRule(BaseModel):
    """Deterministic routing — no model call (design spec §4.1 ``identify``).

    ``all_of_patterns`` are case-handling regexes matched against the CHEAP,
    formpack-independent identify pass (plain tesseract over the first N pages), never
    against a full Docling run. A document routes to this formpack only if *all* match.
    """

    model_config = ConfigDict(extra="forbid")

    all_of_patterns: list[str] = Field(min_length=1)


class DoclingConfig(BaseModel):
    """The Docling pass settings the winning formpack runs exactly once (design spec §4.1)."""

    model_config = ConfigDict(extra="forbid")

    pipeline: Literal["standard"] = "standard"
    images_scale: float = Field(default=2.0, gt=0.0)


class PictureSignatureLocator(BaseModel):
    """Locate a region the layout model misroutes as a picture, by its shape/position.

    Generalises the picture-grid-signature mechanism: boxed-capital grids are commonly
    routed to ``PictureItem`` by the layout model, so we re-read them with the VLM. The
    predicate is width/height bounds plus an explicit page band — pure geometry, no
    client-derived crop fractions.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["picture_signature"]
    min_width: float = Field(ge=0.0)
    min_height: float = Field(ge=0.0)
    max_width: float = Field(gt=0.0)
    max_height: float | None = Field(default=None, gt=0.0)
    page_range: tuple[int, int]


class HeadingSpanLocator(BaseModel):
    """Locate a region as the span from an anchor heading to a stop heading / page break.

    Generalises the heading-span-bbox mechanism: an ``anchor`` regex marks the start, the
    bbox union runs to the ``stop`` regex or a page break. ``single_page`` clamps the span
    to the anchor's page.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["heading_span"]
    anchor: str
    stop: str | None = None
    single_page: bool = False


Locator = Annotated[
    PictureSignatureLocator | HeadingSpanLocator,
    Field(discriminator="kind"),
]


class VlmConfig(BaseModel):
    """The per-region VLM re-read prompt (design spec §4.1).

    Decoding params are **not** set here: ``temperature``/``seed`` are pinned globally in
    ``pipeline.yaml`` ``decoding:`` for every model call, VLM re-reads included. Single
    source of truth, no per-region override.
    """

    model_config = ConfigDict(extra="forbid")

    prompt: str
    max_tokens: int = Field(gt=0)


SpliceMode = Literal["replace_placeholder", "insert_after_anchor"]


class Region(BaseModel):
    """One generalised per-region VLM re-read pass (design spec §4.1 ``regions``).

    ``insert_after_anchor`` is NON-destructive and is the required splice for span regions —
    insertion keeps the OCR text intact if the locator drifts; ``replace_placeholder``
    substitutes the k-th ``<!-- image -->`` placeholder for picture-signature regions.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    locate: Locator
    vlm: VlmConfig
    splice: SpliceMode


class ExtractionConfig(BaseModel):
    """The deterministic text->JSON extraction (design spec §4.1 ``extraction``).

    ``schema_file`` names a JSON Schema sent as ``response_format: json_schema``. Decoding
    is inherited from ``pipeline.yaml`` — single source of truth, no per-formpack override.
    """

    model_config = ConfigDict(extra="forbid")

    # `schema` collides with pydantic's BaseModel.schema; alias keeps the YAML key `schema`.
    schema_file: str = Field(alias="schema")
    system_prompt: str
    max_tokens: int = Field(gt=0)


class ReportConfig(BaseModel):
    """What the emitted ``inbox/*.txt`` shows first (design spec §4.1 ``report``).

    ``headline_fields`` are rendered in the first lines, in order — the review UI snippet is
    the first 320 chars, so the human decision must be possible from these alone.
    """

    model_config = ConfigDict(extra="forbid")

    headline_fields: list[str] = Field(min_length=1)


class GoldenDoc(BaseModel):
    """One golden scan + its expected JSON (design spec §4.1 ``golden.docs``).

    ``scan`` names a file **regenerated locally** by ``papereyes synth --seed`` — the scan
    PDF is not committed. ``expected`` is the committed ground truth.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    seed: int
    scan: str
    expected: str


class GoldenConfig(BaseModel):
    """The formpack's own acceptance mechanism (design spec §4.1 ``golden`` / §7).

    Deterministic leaf-field comparison vs expected JSON: ``field_accuracy_floor`` is the
    fraction of expected leaf fields that must match after normalisation; any miss on a
    ``required_fields`` leaf path fails regardless of the floor.
    """

    model_config = ConfigDict(extra="forbid")

    field_accuracy_floor: float = Field(ge=0.0, le=1.0)
    required_fields: list[str] = Field(min_length=1)
    # Empty on a fresh scaffold; `papereyes gate` (Stage 3) enforces a non-empty golden set.
    docs: list[GoldenDoc] = Field(default_factory=list)


class SynthConfig(BaseModel):
    """How the synthetic corpus is produced (design spec §4.1 note; §6 Stage 1).

    ``render`` (default): draw a synthetic, form-shaped document from scratch with reportlab
    and seeded personas, then rasterise — no fetched blank, no Crown-copyright pixels, the
    corpus that ships. ``overlay``: fill the fetched real blank (``fetch-forms`` must have
    run) — higher fidelity, never used for committed goldens (OGL crest exclusion).
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["render", "overlay"] = "render"
    base_seed: int = 7
    count: int = Field(default=6, gt=0)
    dpi: int = Field(default=200, gt=0)


class Formpack(BaseModel):
    """A whole per-form-type calibration, as data (design spec §4.1)."""

    model_config = ConfigDict(extra="forbid")

    formpack: str
    version: int = Field(ge=1)
    display_name: str
    source_form: SourceForm
    identify: IdentifyRule
    docling: DoclingConfig = Field(default_factory=DoclingConfig)
    regions: list[Region] = Field(default_factory=list)
    extraction: ExtractionConfig
    report: ReportConfig
    golden: GoldenConfig
    synth: SynthConfig = Field(default_factory=SynthConfig)

    def slug(self) -> str:
        """The versioned identity used in emitted filenames and provenance (``uk-ch2@1``)."""
        return f"{self.formpack}@{self.version}"


# ── pipeline ────────────────────────────────────────────────────────────────────────


class Endpoint(BaseModel):
    """The one OpenAI-compatible endpoint that serves both models (design spec §4.2).

    ``base_url`` may carry a ``${VAR:-default}`` template resolved at run time; at Stage 0-1
    it is validated as an opaque string (no network call is made).
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str


class PipelineModels(BaseModel):
    """The two model ids; the model NAME routes on llama-swap (design spec §4.2)."""

    model_config = ConfigDict(extra="forbid")

    vlm: str
    extract: str


class Decoding(BaseModel):
    """Applied to EVERY model call — region VLM re-reads AND the extraction call.

    Single source of truth; recorded per-call in ``provenance.json``. A determinism receipt
    is only as good as the pins it proves.
    """

    model_config = ConfigDict(extra="forbid")

    temperature: float = Field(ge=0.0)
    seed: int


class IdentifyPass(BaseModel):
    """The cheap, formpack-independent routing pass (design spec §4.2 ``identify``)."""

    model_config = ConfigDict(extra="forbid")

    pages: int = Field(gt=0)
    dpi: int = Field(gt=0)


class EmitConfig(BaseModel):
    """Where the report + sidecar are dropped — a deckhand agent's inbox (design spec §4.2)."""

    model_config = ConfigDict(extra="forbid")

    agent_inbox: str


class WatchConfig(BaseModel):
    """The drop-folder watch settings (design spec §4.2). Enforced in Stage 4, data here."""

    model_config = ConfigDict(extra="forbid")

    drop_dir: str
    poll_seconds: float = Field(gt=0.0)
    on_success: Literal["move_to_processed"] = "move_to_processed"
    on_failure: Literal["move_to_failed"] = "move_to_failed"


class Pipeline(BaseModel):
    """The global serving config (design spec §4.2).

    ``serving_note`` records, in the config, the determinism serving assumption stated
    honestly in the receipts: temp-0 byte-determinism on llama.cpp/llama-swap additionally
    requires sequential requests against a single-slot server.
    """

    model_config = ConfigDict(extra="forbid")

    endpoint: Endpoint
    models: PipelineModels
    decoding: Decoding
    identify: IdentifyPass
    emit: EmitConfig
    watch: WatchConfig
    serving_note: str | None = None
