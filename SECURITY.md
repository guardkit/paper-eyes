# Security

This document states Paper Eyes' security posture in plain language and tells you how to report a
vulnerability. It is a description of how the software is built and what it does **not** promise — not
a guarantee. For the fuller picture of assets, boundaries, and the things Paper Eyes deliberately does
not defend, read [THREAT-MODEL.md](THREAT-MODEL.md).

## Posture — how Paper Eyes is built

**Config is data, never code.** `pipeline.yaml`, every `formpack.yaml`, and the JSON Schema each
formpack points at are parsed as data and validated. YAML is read with `yaml.safe_load` **only** (no
`!!python/object`, no code execution) and every config model forbids unknown keys
(`ConfigDict(extra="forbid")`), so a typo or an unexpected field fails loudly at `papereyes check`
time rather than being silently accepted. The one environment reference — an endpoint `base_url` —
is a plain `${NAME:-default}` substitution against `os.environ`; there is no template engine, no
expression evaluation, and no attribute access. JSON Schemas are loaded with `json.loads` and used as
data. There is no `eval`, no `exec`, no `pickle`, and no `marshal` anywhere in the tree.

**The document parser runs untrusted PDFs at arm's length — and is honest about how far that goes.**
The bytes Paper Eyes parses that it did not author are scanned PDFs. They are rendered to page images
by poppler (`pdftoppm`; the optional fetch path also probes a blank with `pdftotext`). Every poppler
call goes through `subprocess.run` with a **fixed argument list** — there is no `shell=True`, no
`os.system`, and no string interpolation anywhere in the tree; the scan path is passed as an argv
element, not spliced into a shell line. The tool runs on bytes already written to disk, as a
**non-root user** (the image builds a uid-10001 `papereyes` user, and compose runs the process as the
host uid), inside a container, with its output confined to a **per-scan workdir**. That process /
user / mount isolation is the blast-radius bound — and it is the *whole* of it. Paper Eyes does **not**
wrap poppler in a wall-clock timeout, resource limits (memory / CPU / file-size), a seccomp profile,
or a network-isolated namespace. That is an accepted v0 limit, named as such in
[THREAT-MODEL.md](THREAT-MODEL.md) — not a sandbox this document will claim it has.

**Model output is treated as untrusted bytes.** The served-model responses are validated by
construction: the structured-extraction call sends `response_format: json_schema` with `strict: true`,
and the parsed result is re-checked client-side (a bounded salvage re-parse, then an `isinstance`
object check — a non-object or unparseable completion becomes a typed `ExtractionError`, never a
crash). The bulk-conversion output is parsed by a **bounded regex** over location tags with
exact-duplicate collapse. The HTTP client validates completion shape, retries a bounded number of
times with a growing backoff for transient swap stalls, and raises a typed `ModelError` on a
malformed or empty completion. Decoding is pinned (`temperature=0`, fixed `seed`) on every call.

**Egress is enumerated and recorded.** At watch / pipeline runtime the **only** network destination
Paper Eyes reaches is the configured OpenAI-compatible model endpoint
(`PAPEREYES_OPENAI_BASE_URL`), and that endpoint is recorded in every per-scan `provenance` sidecar.
There is no analytics, no telemetry, and no callback. One separate, optional prep command —
`papereyes fetch-forms` — additionally reaches exactly one URL, the blank-form URL declared in a
formpack, verifies its sha256 against the committed pin (a mismatch is a hard error, never a silent
re-pin), and writes into a gitignored build dir. The default synthetic corpus is rendered locally and
needs no fetch. Nothing else leaves the machine.

**No secrets at rest.** There are no secrets in the repository tree, and the model client carries
none: it posts to the endpoint with only a `Content-Type` header — no API key, no bearer token, no
`Authorization`. The endpoint is a plain URL. The form-URL `sha256` pins in a formpack are integrity
anchors, not secrets.

**Supply-chain: the base and the parser toolchain are pinned by digest, not by a floating tag.** The
demo image pins its base (`python:3.11-slim-bookworm`) and the `uv` tool image by `@sha256:` digest,
and pins `poppler-utils` to an exact bookworm version, so an upstream swap cannot land in a build
without a visible diff. Python dependencies are installed from the frozen `uv.lock` — the audited set,
nothing floats. Bump any of these deliberately; never let them drift.

**Its own logs are plain receipts.** `processed.jsonl` (the sha-keyed idempotency log) is a plain
append-only receipt file. It is **not** a tamper-evident record — that hash-chained property belongs
to deckhand's ledger alone, and Paper Eyes never claims it for its own files.

## What this posture is not

Paper Eyes does not claim to be unbreakable, and this document avoids absolute security language on
purpose. Extraction scores are a rubric calibrated against synthetic goldens of a known form family —
they are not an objective correctness verdict on an arbitrary document, and Paper Eyes reads only
formpack-calibrated typed / printed forms, never handwriting and never "any form." See
[THREAT-MODEL.md](THREAT-MODEL.md) for the boundaries Paper Eyes does **not** defend (a hostile local
operator; a malicious PDF beyond the process / user / mount isolation above; a compromised model
endpoint beyond the shape and range checks above).

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue for a suspected
vulnerability.

- **Channel:** use GitHub's **private vulnerability reporting** on this repository (the repository's
  *Security → Report a vulnerability* / private security advisory flow).
- **Direct contact:** `<SECURITY-CONTACT-PLACEHOLDER>` — a direct security address is not yet
  published; the maintainer fills this in (tracked as MA-24). Until then, use the GitHub private
  advisory channel above.
- **Acknowledgement:** we aim to acknowledge a report on a **best-effort basis within 7 days**. This
  is a best-effort target, not a contractual SLA.
- **Please include:** what you observed, the steps to reproduce, and the impact you believe it has.

Reports are handled through the private advisory channel and kept separate from the public issue
tracker until a fix is available.

## Planned hardening (not yet implemented)

These are named follow-ons, not current properties. Each needs infrastructure or keys Paper Eyes does
not carry today, so it is listed here honestly rather than implied above:

- **A bounded-resource parser wrapper.** Wrap the poppler calls in a wall-clock timeout plus memory /
  CPU / output-size resource limits (and, where the platform allows, a seccomp profile or a
  network-isolated namespace), so a malicious PDF that trips a poppler bug cannot hang or exhaust the
  container's ambient resources. This closes the accepted v0 limit named in the threat model.
- **CI build-provenance attestation + SBOM.** Emit a signed build-provenance attestation and a
  software bill of materials per image on release, so the published artefact's supply chain can be
  verified independently. Needs CI signing identity.
- **Signed release tags.** There are no git tags on this repository yet; the next release tag should
  be signed. Needs a signing key.
- **A published security contact** (MA-24) to replace the placeholder above.

## Licence

MIT (this code) — see [LICENSE](LICENSE).
