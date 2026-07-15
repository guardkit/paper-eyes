# Threat model

This document names Paper Eyes' assets, its trust boundaries, the surfaces that parse untrusted
input, and — just as importantly — the things Paper Eyes **deliberately does not defend against**. It
is written to be honest about scope rather than reassuring. The build-time posture that backs these
claims is in [SECURITY.md](SECURITY.md).

Paper Eyes ingests scanned PDFs of a known public form and turns them into structured JSON, then
hands a report to a [deckhand](https://github.com/guardkit/deckhand) agent for human review. The
untrusted thing it parses is a document; the human review in deckhand is where trust is finally
placed.

## Assets — what is worth protecting

| Asset | Why it matters |
| --- | --- |
| **The dropped scans** (`drop/`) | The operator's own source documents. In real use these are scanned forms that can carry personal data. Local data — the daemon reads them in place and archives them under `drop/processed/` or `drop/failed/`. |
| **The emitted report + `.extraction.json` sidecar** | The structured extraction (the personal data lifted off the form) plus the human-legible report — the record a downstream reviewer trusts. Written atomically into the shared agent `inbox/`. |
| **The workdir** (`work/`) | Page crops, the `provenance` sidecar, and `processed.jsonl` for each scan. Contains extracted content and the record of what ran. |
| **`processed.jsonl`** | The sha-keyed idempotency receipt — valuable operational data, but a plain append-only log, **not** an integrity ledger. |
| **The host box** | The operator's own machine. Paper Eyes runs a polling loop on it and writes only under its mounted `drop/`, `agents/`, and `work/` trees. |
| **The formpack pins** (`source_form.url` + `sha256`) | Integrity anchors for the blank public forms — they make a silent upstream change fail. Not secrets. |
| **The model endpoint URL** | A config value (`PAPEREYES_OPENAI_BASE_URL`). No secret token is attached to it; the client sends no `Authorization` header. |

## Trust boundaries — the boxes

Paper Eyes deploys as its own container plus an unmodified deckhand container over one shared
`agents/` root on a single host, and reaches one model endpoint (a URL). The boundaries are where
trust changes:

1. **The drop folder ↔ the Paper Eyes container.** Anything that can write a `*.pdf` into `drop/`
   feeds the poppler parser. Scans are treated as **untrusted parser input** (see below). The daemon
   is idempotent (sha-keyed off `processed.jsonl` — the same bytes dropped twice are processed once)
   and routes a pipeline failure or an emit collision to `drop/failed/` with a note, rather than
   overwriting anything.
2. **The Paper Eyes container ↔ the model endpoint** (`PAPEREYES_OPENAI_BASE_URL`). The client posts
   over stdlib HTTP with no auth header and treats what comes back as **untrusted bytes** (see below).
3. **The Paper Eyes container ↔ deckhand**, via the shared `agents/` **filesystem** root. Paper Eyes
   writes the report + sidecar atomically into an agent `inbox/` and imports **zero** deckhand code —
   the integration seam is files on disk. The review UI is deckhand's, published on the host
   **loopback only** (`127.0.0.1:8477`). The UI's own web-surface posture (authentication, CSRF,
   Host-header validation) is deckhand's to state, not Paper Eyes' — see deckhand's own threat model;
   its current containment is the loopback bind.

These boundaries, plus the optional `papereyes fetch-forms` blank-form URL, are the **entire** egress
surface: no network calls except the configured model endpoint at runtime (recorded in every
`provenance` sidecar), and the single pinned form URL when the optional fetch command is run.

## Untrusted-parser surfaces

These are the places where Paper Eyes parses input it did not author, and treats as hostile:

- **The dropped scan PDF** — the primary untrusted input. Rendered by poppler `pdftoppm` to page PNGs,
  which are then decoded and normalised through a version-pinned Pillow; the optional fetch path
  probes a downloaded blank with `pdftotext`. Every poppler call goes through a **bounded-resource
  wrapper** (`papereyes.sandbox`): a **fixed argv list** (no shell), a **wall-clock timeout** on the
  parent, and — applied in the child before `exec` on POSIX — CPU-time, address-space, and file-size
  **rlimits** (`RLIMIT_CPU` / `RLIMIT_AS` / `RLIMIT_FSIZE`; the `pdftotext` stdout pipe, which
  `RLIMIT_FSIZE` cannot bound, is additionally truncated after read). It runs as a **non-root user**
  with output confined to a **per-scan workdir**. This is **software mitigation, not an OS sandbox**:
  a seccomp profile and a network-isolated namespace are still absent (see "what Paper Eyes does not
  defend against").
- **Model responses** — the structured-extraction call constrains the server with a strict
  `json_schema`, and the result is re-checked client-side: a bounded salvage re-parse and an
  object-type check turn a malformed completion into a typed error rather than a crash. The
  bulk-conversion output is parsed by a bounded location-tag regex with exact-duplicate collapse. The
  transport validates completion shape and retries a bounded number of times.
- **Config, formpacks, and JSON Schemas** — `yaml.safe_load` / `json.loads` only, validated with
  `extra="forbid"`; treated as data, never executed.

## What Paper Eyes deliberately does NOT defend against

Being explicit about the edges of the model is part of the model:

- **A hostile local operator.** Paper Eyes runs on the operator's own machine and trusts that
  operator. It does not defend against someone with local access editing the drop folder, the
  workdir, the emitted reports, the config, or the formpacks. `processed.jsonl` is a plain append-only
  log with **no** anti-tampering property — that is a deliberate design choice, and it is never
  described otherwise.
- **The poppler parser beyond its resource caps, and below the syscall / network layer.** poppler
  (`pdftoppm` / `pdftotext`) is a C toolchain with a real history of parser bugs. Paper Eyes runs it
  as a non-root user in a container, from a fixed argv, on bytes already in hand, with output confined
  to a workdir, **and** through the bounded-resource wrapper above — a wall-clock timeout plus CPU /
  address-space / file-size rlimits, so a malicious PDF that trips a poppler bug can no longer hang
  the parse indefinitely or exhaust memory / CPU / disk beyond those caps. What it does **not** add is
  an OS sandbox *below* that: there is no seccomp profile restricting the syscalls poppler may make
  and no network-isolated namespace, so poppler still runs with the container's ambient syscall
  surface and network access. This is software mitigation, not kernel-level confinement; the
  seccomp / namespace leg is named as planned hardening in [SECURITY.md](SECURITY.md).
- **A compromised model endpoint.** Paper Eyes validates the *shape*, *schema*, and *structure* of
  model output, but a malicious endpoint could return well-formed, wrong extractions. The extraction
  is advisory and deckhand's human review is load-bearing precisely because the model is never fully
  trusted — but Paper Eyes does not attest the endpoint.
- **Network-level adversaries on the box network.** Paper Eyes uses plain HTTP to the configured
  endpoint on the assumption it sits on a trusted local network; it does not add its own transport
  security over it.
- **Multi-tenant / hosted deployment.** Paper Eyes is single-operator and single-host by design, and
  the review UI it feeds is deckhand's loopback-only surface. Multi-operator access control, remote
  hosting, and authentication are explicit non-goals — running it as a shared service is outside what
  this threat model covers.
- **Extraction correctness as a guarantee.** Accuracy is stated only as "*X% field-level accuracy on
  N golden synthetic scans of form Y*", and determinism only under the stated sequential, single-slot
  serving condition. Paper Eyes reads formpack-calibrated typed / printed forms — never "any form",
  never handwriting. A correct-looking JSON is not a promise the underlying document said so; that is
  what the human review is for.

If your deployment changes any of these assumptions (a drop folder writable by others, an untrusted
network to the model endpoint, a hosted review UI, or poppler pointed at adversarial PDFs without the
added isolation above), the properties above no longer hold and you are responsible for the additional
controls that situation needs.
