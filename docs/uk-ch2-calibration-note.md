# uk-ch2 calibration note — Stage 2/3 (2026-07-11)

Every value below was derived **fresh** against the synthetic CH2 corpus (seeded personas,
`papereyes synth`) on the live pinned endpoint. Nothing here is transliterated from any prior
engagement; the leakage CI gate enforces that mechanically.

## Serving setup (verified live, 2026-07-11)

- Endpoint: `http://localhost:9000/v1` (llama-swap; the model NAME routes).
- Models exercised with real calls: `granite-docling` (bulk conversion), `granite-vision-4-1-4b`
  (region re-read — the spec pin), `qwen36-workhorse` (extraction, `response_format:
  json_schema`, reasoning disabled).
- Cold-swap latency is real: a first call to a cold model took 100–170 s before responding.
  The client retries with growing backoff and long timeouts rather than concluding failure —
  an earlier session mis-diagnosed the pinned region VLM as "returns HTTP 500 on every
  request" from exactly this (a 500 mid-swap is transient).

## Deviation: bulk conversion is the SERVED Docling VLM

The design spec's bulk pass is Docling's local `StandardPdfPipeline` (Tesseract OCR + layout).
On this build host there is **no tesseract binary and no sudo** to install one (verified), and
Docling's non-Tesseract local engines are multi-GB torch stacks with Hugging Face weight
downloads that live outside the one pinned, provenance-recorded endpoint. The bulk conversion
is therefore served by `granite-docling` — the Docling project's own document-conversion VLM —
on the same endpoint as every other call. Consequences, all recorded:

- Recorded as a `deviations` entry in **every** `provenance.json`.
- The boxed-capital NINO grid is not misrouted to a `PictureItem` (the local layout-model
  behaviour the spec anticipated); the converter instead **fragments it into single-glyph
  cells**. The `picture_signature` locator therefore detects a run of adjacent single-glyph
  cells — same intent, backend-shaped mechanism. The run's own characters double as a
  `fallback_text` receipt.
- The identify pass cannot be "plain tesseract" either; it converts the first
  `identify.pages` pages (rasterised at the fixed `identify.dpi`) with the same served
  converter and is timed separately, so routing cost stays honest.
- A multi-document gate run swaps between three served models per document (convert → region
  re-read → extract). Cross-document call batching to reduce swaps is explicitly out of scope
  for v0 (design spec §2 OUT); swap latency shows up honestly in `timings_s`.

## Locator geometry (granite-docling 0–500 loc grid, NOT raster pixels)

Measured on persona-01 (dpi 200, A4 → 1654×2339 px):

- NINO grid: 9 single-glyph cells, bbox union x 186–352, y 120–126 → **166 loc wide, 6 loc
  tall**. Calibrated bounds: `min_cells: 6`, `min_width: 80`, `max_width: 400`,
  `min_height: 3`, `page_range: [1, 2]`.
- `heading_span` for the children block brackets `2 Children you're claiming for` →
  `3 Higher income` on page 2.
- The converter occasionally repeats elements (a known repetition quirk); exact duplicates
  are collapsed at parse, near-duplicates are tolerated by the extractor.

## Region-crop presentation (calibrated live against the pinned VLM)

The NINO strip crop is 583×74 px at dpi 200 with a 1% page margin. Live A/B on
`granite-vision-4-1-4b` (temperature 0, seed 42):

| presentation | output |
|---|---|
| tight crop (583×74) | `BN605990B` — **correct** |
| tight crop ×3 upscale | `BN6059900B` — extra character |
| pasted on larger white canvas | `B N 6 0 5 9 9 0 E` — last char misread |
| padded canvas ×3 | `BN605990E` — last char misread |

**The tight crop is the model input** (and the filmable receipt). An earlier page-sized-canvas
presentation was removed for this reason.

Prompt calibration, same live pass: a direct boxed-row instruction ("one printed character per
box… single line, no spaces") transcribes the grid correctly; the earlier
`'Field name: value'` phrasing made the model emit one `Field name: X` line per box and
misread characters.

Grid-value rule: the bulk pass counts the grid's cells, so the re-read replaces the bulk value
only when it is a clean alphanumeric token of **exactly** that length; a different-length
re-read is a misread and the bulk value stands.

## Gate + determinism receipts

Recorded in [session-b-receipts.md](session-b-receipts.md) §§4-5 (verbatim gate output table,
frozen `formpack.baseline.json`, and the two-run byte-compare determinism receipt with its
serving conditions).
