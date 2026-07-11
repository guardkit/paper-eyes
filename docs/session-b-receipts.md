# Session B receipts — extraction gate + determinism (2026-07-11)

All numbers below are measured, not claimed. Serving conditions for every run on this page:
one llama-swap endpoint (`localhost:9000/v1`), sequential single-client requests, pinned
decoding (`temperature=0`, fixed seed) on every call. Models: docling=`granite-docling`,
region=`granite-vision-4-1-4b`, extract=`qwen36-workhorse`.

## 1. The gate that said no (first full run)

```
gate FAILED: floor not met or a required field missing — baseline NOT frozen
[persona-01] 17/17 = 1.000
[persona-02] 19/21 = 0.905  REQUIRED MISS: claimant.nino   (expected 'NP727337B' got 'N7337')
[persona-03] 16/17 = 0.941  REQUIRED MISS: claimant.nino   (expected 'CZ698875C' got 'CZ69875C')
[persona-04] 21/21 = 1.000
[persona-05] 13/13 = 1.000
[persona-06] 12/13 = 0.923  REQUIRED MISS: claimant.nino   (expected 'XL544569A' got 'XL544556')
overall: 98/102 = 0.9608 (floor 0.95) required_ok=False -> FAIL
```

0.9608 beats the floor — and the gate still failed, because the field that matters most was
wrong on half the documents. That is the gate doing its job.

## 2. The diagnosis (each hypothesis tested live before any code changed)

Two distinct causes, one field:

- **Personas 02/05 — the locator never fired.** The converter sometimes *merges* the boxed
  grid into one garbled element instead of fragmenting it into single-glyph cells, so there
  is no signature to detect and the garbled OCR text leaks straight through.
- **Personas 03/06 — the VLM garbled the detected crop.** Whole-row re-asks at pinned
  decoding **collapse repeated digits** (`698875 → 68875`, `544569 → 54456`), and a
  detected-signature bbox drifts enough to clip characters.

Hypotheses tested against the served VLM, in order: whole-row re-ask on the detected crop
(failed: `NPP27373B`); 3× upscale (fixed one persona, repeated-digit collapse remained);
per-box on the detected crop (failed: misalignment). **Winner: per-box on the pinned bbox**
— exact geometry derived from the synthetic renderer's own layout constants — which read
all three failing NINOs correctly on the first try.

## 3. The fix (calibration as data)

- `picture_signature` locators may pin a `fallback_page`/`fallback_bbox` so the region
  always produces its crop receipt even when the signature cannot fire.
- `reask` on a region: a field that fails its declared `format` after extraction gets ONE
  bounded re-ask pass; with `boxes: N` the pinned bbox is segmented into equal-pitch cells
  read one character each. The result is adopted **only if it passes the same format**; a
  still-failing value stays as extracted — an honest miss, never masked. Every re-ask is
  recorded in provenance (`repaired` / `normalized` / `unrepaired`, with the prior value).

## 4. The gate that says yes (second full run, after the fix)

```
extraction gate — uk-ch2@1
[persona-01] 17/17 = 1.000
[persona-02] 20/21 = 0.952   (remaining miss: a non-required address-line bleed)
[persona-03] 17/17 = 1.000
[persona-04] 21/21 = 1.000
[persona-05] 13/13 = 1.000
[persona-06] 13/13 = 1.000
overall: 101/102 = 0.9902 (floor 0.95) required_ok=True -> PASS
baseline frozen -> formpacks/uk-ch2/formpack.baseline.json
```

Stated precisely: **99.0% field-level accuracy on 6 golden synthetic scans of form CH2**,
with every required field correct on every document, under the serving conditions above.
This says nothing about other forms, other scans, or handwriting.

## 5. The determinism receipt

Two full sequential runs of the same scan (persona-02 — deliberately the hardest case: it
exercises the fallback bbox AND the per-box re-ask):

```
sha256(report,     run 1) = 033a6c026bc424c225f6b0ad8b0c5bc14c4942bc8d820ef3582fe1131f69d491
sha256(report,     run 2) = 033a6c026bc424c225f6b0ad8b0c5bc14c4942bc8d820ef3582fe1131f69d491
sha256(extraction, run 1) = e9e633d49c8b164632abc4c7dfb9505ac65d43961d76a15ee6541ac3c64d6848
sha256(extraction, run 2) = e9e633d49c8b164632abc4c7dfb9505ac65d43961d76a15ee6541ac3c64d6848
reask outcome, both runs  = repaired
runtimes: 297.4s / 298.1s (timings live in provenance, never in the report)
```

Byte-identical output, **deterministic under the stated serving conditions** (sequential
single-client, pinned decoding). Concurrent serving was not tested and is not claimed.
