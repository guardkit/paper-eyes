"""Hermetic pipeline test support: a stub model client + canned CH2 DocTags.

The stub implements the :class:`~papereyes.pipeline.client.ModelClient` protocol structurally, so
the whole pipeline runs with no endpoint and no GPU — deckhand's stub-injection discipline. The
canned page-1 DocTags mirror the real ``granite-docling`` output shape observed on the fleet
(location tags on the 0-500 grid; the NINO grid fragmented into single-glyph cells).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from papereyes.pipeline.client import ModelResult

# Page 1 (claimant) — the NINO is fragmented into 9 single-glyph cells (the boxed-grid signature).
PERSONA01_PAGE1 = """\
<loc_43><loc_23><loc_164><loc_33>Child Benefit claim
<loc_43><loc_38><loc_258><loc_44>Form CH2 - claim Child Benefit for one or more children.
<loc_43><loc_57><loc_101><loc_64>1 About you
<loc_43><loc_72><loc_57><loc_77>Title
<loc_186><loc_72><loc_199><loc_77>Ms
<loc_43><loc_84><loc_82><loc_89>First names
<loc_186><loc_84><loc_224><loc_89>Brandon
<loc_43><loc_96><loc_78><loc_101>Last name
<loc_186><loc_96><loc_221><loc_102>Thomas
<loc_43><loc_108><loc_84><loc_113>Date of birth
<loc_186><loc_108><loc_233><loc_113>1989-08-25
<loc_43><loc_120><loc_133><loc_125>National Insurance number
<loc_186><loc_120><loc_192><loc_126>B
<loc_210><loc_120><loc_216><loc_126>N
<loc_226><loc_120><loc_232><loc_126>6
<loc_245><loc_120><loc_251><loc_126>0
<loc_266><loc_120><loc_272><loc_126>5
<loc_286><loc_120><loc_292><loc_126>9
<loc_306><loc_120><loc_312><loc_126>9
<loc_325><loc_120><loc_331><loc_126>0
<loc_346><loc_120><loc_352><loc_126>B
<loc_43><loc_139><loc_70><loc_144>Address
<loc_186><loc_139><loc_308><loc_145>Studio 08, Beverley harbors
<loc_186><loc_151><loc_239><loc_157>Hollandland
<loc_43><loc_163><loc_74><loc_168>Postcode
<loc_186><loc_163><loc_222><loc_168>S13 9ZD
"""

# Page 2 (children span between "2 Children you're claiming for" and "3 Higher income").
PERSONA01_PAGE2 = """\
<loc_43><loc_30><loc_180><loc_38>2 Children you're claiming for
<loc_43><loc_45><loc_70><loc_51>Child 1
<loc_43><loc_57><loc_82><loc_62>First names
<loc_186><loc_57><loc_224><loc_62>Joel
<loc_43><loc_69><loc_78><loc_74>Surname
<loc_186><loc_69><loc_221><loc_74>Thomas
<loc_43><loc_81><loc_84><loc_86>Date of birth
<loc_186><loc_81><loc_233><loc_86>2019-12-26
<loc_43><loc_99><loc_70><loc_104>Child 2
<loc_43><loc_111><loc_82><loc_116>First names
<loc_186><loc_111><loc_224><loc_116>Hayley
<loc_43><loc_123><loc_78><loc_128>Surname
<loc_186><loc_123><loc_221><loc_128>Thomas
<loc_43><loc_135><loc_84><loc_140>Date of birth
<loc_186><loc_135><loc_233><loc_140>2017-12-12
<loc_43><loc_160><loc_120><loc_168>3 Higher income
"""

PERSONA01_EXTRACTION = """{
  "claimant": {"title": "Ms", "first_names": "Brandon", "last_name": "Thomas",
    "full_name": "Brandon Thomas", "nino": "BN605990B", "date_of_birth": "1989-08-25",
    "address_lines": ["Studio 08, Beverley harbors", "Hollandland"], "postcode": "S13 9ZD"},
  "children": [
    {"first_names": "Joel", "last_name": "Thomas", "full_name": "Joel Thomas",
     "date_of_birth": "2019-12-26"},
    {"first_names": "Hayley", "last_name": "Thomas", "full_name": "Hayley Thomas",
     "date_of_birth": "2017-12-12"}
  ]
}"""


@dataclass
class StubModelClient:
    """A deterministic, offline model client for tests."""

    convert_pages: list[str] = field(
        default_factory=lambda: [PERSONA01_PAGE1, PERSONA01_PAGE2]
    )
    region_text: str = "<loc_10><loc_10><loc_20><loc_20>BN605990B"
    extraction_text: str = PERSONA01_EXTRACTION
    docling_model: str = "granite-docling"
    region_model: str = "granite-vision-4-1-4b"
    extract_model: str = "qwen36-workhorse"

    # capture for assertions
    convert_calls: int = 0
    region_prompts: list[str] = field(default_factory=list)
    last_extract_markdown: str = ""

    def convert_page(self, image_png: bytes, *, max_tokens: int) -> ModelResult:
        # Cyclic: the pipeline converts the doc's pages twice (identify pass at fixed defaults,
        # then the full conversion after routing), so page k maps to canned page k both times.
        idx = self.convert_calls % len(self.convert_pages)
        text = self.convert_pages[idx]
        self.convert_calls += 1
        return ModelResult(text=text, model=self.docling_model, latency_s=0.01)

    def read_region(self, prompt: str, image_png: bytes, *, max_tokens: int) -> ModelResult:
        self.region_prompts.append(prompt)
        return ModelResult(text=self.region_text, model=self.region_model, latency_s=0.01)

    def extract_json(
        self, system_prompt: str, user_text: str, json_schema: dict[str, object], *,
        max_tokens: int
    ) -> ModelResult:
        self.last_extract_markdown = user_text
        return ModelResult(text=self.extraction_text, model=self.extract_model, latency_s=0.01)
