# Paper Eyes

A self-contained, local-first pipeline that turns a scanned / image-only PDF of a **known
public form** into deterministic structured JSON, and drops a human-legible extraction report
into a [deckhand](https://github.com/guardkit/deckhand) agent's `inbox/`. deckhand's watch
daemon, review UI, ledger and trust engine then do what they already do — propose, let the
human approve / correct / reject, advance the streak. Paper Eyes changes nothing inside
deckhand: the integration seam is files on disk, and this package imports zero deckhand code.

Per-form-type calibration — region locators, VLM prompts, the extraction schema, the golden
docs — is pure YAML data called a **formpack**. Adding a form family is a data change, never a
code change.

> Status: Stages 0-4 are in place — scaffold + config-as-data, the synthetic
> corpus, the pipeline core (`papereyes run`), the extraction gate (`papereyes gate`), and the
> watch/emit daemon (`papereyes watch`) with the shipped `examples/paper-clerk` deckhand agent and
> the `demo/` compose (`demo/verify_e2e.sh`). The composed multi-lane demo (paper-eyes → doc-router
> → relay → digest-clerk, `demo/verify_chain.sh`) has shipped too: `--stub` runs in CI and `--live`
> passed cold end-to-end on real models (see `docs/live-receipts-2026-07-12.md`).

## The shape

```
drop/ scan.pdf
   -> identify (cheap, formpack-independent routing; first pages at fixed defaults)
   -> bulk document conversion, every page exactly once, after routing
      (design: Docling StandardPdfPipeline — served here by granite-docling, the Docling
       project's conversion VLM, a recorded deviation: see docs/uk-ch2-calibration-note.md)
   -> per-region VLM re-read of the regions the bulk pass mishandles (pinned decoding)
   -> splice the VLM text back into the markdown
   -> deterministic text -> JSON (pinned decoding, response_format json_schema)
   -> atomic emit into a deckhand agent's inbox/ (report .txt + .extraction.json sidecar)
```

## Install & try

```bash
uv sync
uv run papereyes version
uv run papereyes check formpacks/uk-ch2        # validate a formpack as data
uv run papereyes init formpacks/uk-myform      # scaffold a new formpack
uv run papereyes synth uk-ch2 --count 6 --seed 7   # regenerate the golden corpus (Stage 1)

# Stages 2-4 need the served models (pipeline.yaml `models:`) reachable at the endpoint:
uv run papereyes run formpacks/uk-ch2/golden/persona-01.pdf   # one scan -> report + JSON
uv run papereyes gate formpacks/uk-ch2         # score all goldens; freeze the baseline
uv run papereyes watch                         # watch drop/, emit reports into the agent inbox/

# The standalone two-container demo (paper-eyes + the unmodified deckhand) is one cold start:
cd demo && ./verify_e2e.sh --stub              # hermetic offline plumbing check (no GPU, no docker)
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up --build   # then: cp a CH2 scan into drop/
```

`papereyes synth` needs the poppler `pdftoppm` binary for rasterising
(`sudo apt-get install poppler-utils` on Debian/Ubuntu; `brew install poppler` on macOS).

## What ships, and what does not

Formpacks commit **persona seeds, expected-JSON ground truth, and the form URL + sha256 pin**.
They do **not** commit rendered scan pages: rasterised public-form pages can carry departmental
crests, logos and the Royal Arms, which the Open Government Licence v3 excludes from its grant.
The golden scans regenerate locally and deterministically from the committed seeds
(`papereyes synth --seed`); a `.gitignore` rule and a test keep scan PDFs/PNGs out of history.

The default synthetic corpus is **rendered from scratch** (a form-shaped document drawn with
reportlab and seeded personas), so no Crown-copyright pixels are ever reproduced. A higher
-fidelity `overlay` mode (fill the fetched real blank) exists but is never used for the
committed goldens.

## Attribution

The **HMRC CH2 (Child Benefit claim)** form family is modelled under the terms of the
[Open Government Licence v3](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/)
(Crown copyright). The blank form is fetched at build time by URL with a sha256 pin and is never
vendored into this repository; departmental crests / logos / the Royal Arms are outside the OGL
grant and are never reproduced.

## Honesty

Deliberately bounded language, enforced by a CI grep-lint (`ci/honesty_denylist.txt`):

- Paper Eyes makes **no network calls except to the configured model endpoint, which is
  recorded in every provenance sidecar.** Nothing else leaves the machine.
- Paper Eyes' own logs (`processed.jsonl` and the like) are **plain receipt files**. The
  hash-chained, tamper-evident ledger belongs to deckhand alone; Paper Eyes never claims that
  property for its own records.
- Accuracy is stated only as *"X% field-level accuracy on N golden synthetic scans of form Y"*;
  determinism only under the stated sequential, single-slot serving condition. Paper Eyes reads
  formpack-calibrated typed/printed forms — never "any form", never handwriting.

## Leakage law

Every shipped calibration value is derived fresh against the public-form synthetic corpus. A CI
deny-list (`ci/leakage_denylist.txt`) fails the build on any engagement-derived token from the
reference client pipeline appearing in `src/`, `formpacks/`, `docs/` or `tests/`. Run it
directly with `uv run python -m papereyes.gates`.

## Security

Paper Eyes parses untrusted scanned PDFs with poppler and treats served-model output as untrusted
bytes. Its posture in plain language, and how to report a vulnerability, is in
[SECURITY.md](SECURITY.md); the assets, trust boundaries, and the limits it deliberately does not
defend (an unsandboxed poppler beyond process/user/mount isolation; a compromised endpoint) are in
[THREAT-MODEL.md](THREAT-MODEL.md).

## Licence

MIT (this code) — see [LICENSE](LICENSE). Modelled public forms carry their own Crown-copyright
/ OGL terms, noted per formpack.
