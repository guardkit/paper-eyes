# Paper Eyes — the standalone Lane-1 demo

Turn a scanned, image-only UK CH2 form into deterministic structured JSON and a human-legible
review report, dropped into a [deckhand](https://github.com/guardkit/deckhand) agent's `inbox/`.
deckhand's own watch-daemon, review UI, ledger, and trust engine then propose a filing, let you
approve it, and record the decision. **Paper Eyes changes nothing inside deckhand** — it imports
zero deckhand code; the seam is a pure filesystem drop.

## The front door

```bash
# one-time: seed the shipped example agent into the shared agents root
cp -r ../examples/paper-clerk agents/

# gate the agent (earn the right to exist) — scores its golden set on your model, freezes baseline.json
( cd ../../deckhand && deckhand gate ../paper-eyes/demo/agents/paper-clerk --yes )

# cold start both containers (paper-eyes watcher + the unmodified deckhand review UI)
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up --build

# in another terminal: drop a synthetic scan
( cd .. && papereyes synth uk-ch2 --count 1 --seed 7 )
cp ../formpacks/uk-ch2/golden/persona-01.pdf drop/

# review at http://127.0.0.1:8477 — approve the proposed filing; a .meta sidecar is written
```

The model is a URL. Point both services at your OpenAI-compatible server with `OPENAI_BASE_URL` /
`PAPEREYES_OPENAI_BASE_URL` (default `http://host.docker.internal:9000/v1`). There are no network
calls except that configured endpoint, which is recorded in every provenance sidecar.

## verify_e2e.sh — the cold-start acceptance script

```bash
./verify_e2e.sh --stub    # hermetic: offline stub models, no docker, no GPU (proves the plumbing)
./verify_e2e.sh --live    # the full cross-container flow on your real model endpoint
```

- **`--stub`** (runs in CI): a deterministic offline OpenAI stub (`openai_stub.py`) serves the
  pipeline so the real `papereyes watch` / `papereyes run` commands run end-to-end with no models.
  It asserts: the report + `.extraction.json` sidecar land in the paper-clerk inbox with an SM-4-valid
  name; re-dropping the same scan emits nothing new (the `processed.jsonl` idempotency receipt);
  `papereyes run --force` re-emits a byte-identical report. The offline stub returns fixed canned
  output for any input — it proves the wiring, never accuracy.
- **`--live`** (the main session's receipt — calls real models): cold `docker compose up`, drops a
  scan, waits for the daemon's proposal, approves it through the review resolve endpoint, and asserts
  the `.meta` sidecar, the `proposal` + `human_response` ledger events, a non-`unbaselined`
  `baseline_hash` on every recorded event, and both idempotency receipts (re-drop = no second emit;
  `run --force` -> the daemon's `skipped_resolved` outcome for the same doc_key).

## The composed end-to-end demo — paper-eyes → doc-router → relay → digest-clerk

The standalone demo above stops at a title_tag filing. The **composed** demo runs the whole
master-plan §2.1 chain: the report is emitted into a **move-first** producer (`doc-router`) instead
of paper-clerk (SM-1), a human
approves the move, the move executor writes a handoff receipt before the artifact into
`outbox/reports/`, and deckhand's deterministic, model-free **relay** ferries it into `digest-clerk`'s
inbox — where digest-clerk proposes at its own `ask` tier (composition granted nothing).

```bash
# hermetic acceptance (no docker, no GPU, no models): the exit gate for the assembly.
# needs a sibling deckhand checkout — set DECKHAND_REPO / DECKHAND_PYTHON if not at ../../deckhand.
./verify_chain.sh --stub

# the full cross-container flow the main session films (real models, scripted approvals):
./verify_chain.sh --live
```

- **`--stub`** (CI-runnable where a deckhand checkout is present): the offline stub serves paper-eyes'
  pipeline to emit the report, then `chain_driver.py` drives deckhand's OWN watch / resolve / relay /
  board code in-process with deterministic stub proposers (no model). It asserts the §2.1 trace: report
  emitted (sidecar-first) → move proposed (`fields.folder=reports`, `move_dest` bound) → approved →
  handoff receipt beside the artifact → relay `delivered` (+ provenance sidecar, source unlinked) →
  consumer proposal at ask tier → both ledgers' event sequences exact → both board pages render → the
  emitted name passes the relay contract's `name_pattern` (SM-4).
- **`--live`** (calls real models — the main session's receipt): seeds the shared `agents/` root by
  copying deckhand's shipped `relay-demo` agents, gates `doc-router`/`digest-clerk` (freezing their
  baselines), `docker compose -f docker-compose.chain.yml up`, drops a scan, approves the move and then
  the consumer's title_tag through the review resolve endpoint, and asserts the relay `delivered` line
  and that both board pages render.

The composed demo **reuses deckhand's `examples/relay-demo` agents verbatim** (copied at build, never
forked) and pins the demo agents to `qwen36-workhorse`; `DECKHAND_OPERATOR=owner@localhost` everywhere.

## What is data here

- `openai_stub.py` — the offline plumbing stub (a deterministic OpenAI-compatible server; not a model).
- `docker-compose.yml` — the **standalone** demo: paper-eyes + deckhand over one `agents/` root.
- `docker-compose.chain.yml` — the **composed** demo: paper-eyes (emitting into `doc-router` via the
  mounted `pipeline.yaml` SM-1 override) + `deckhand serve` (watch daemons + the relay + the review UI).
- `pipeline.yaml` — the composed pipeline config: identical to the repo-root one but
  `emit.agent_inbox=/agents/doc-router/inbox` (SM-1). Mounted over the image's baked config.
- `chain_driver.py` — the deckhand-side stub driver for `verify_chain.sh --stub` (run by deckhand's
  venv python; imports deckhand + stdlib only; paper-eyes never depends on deckhand code).
- `Dockerfile` — the paper-eyes watcher image (poppler for rasterising; the model is served remotely).
- `workflows.yaml` — the composed-demo relay contract (copied from deckhand's relay-demo); the source
  of the SM-4 filename constraint the emitted report name is asserted against. It is **not** used by
  this standalone title_tag-first demo (nothing reaches `outbox/`, so no relay fires) — it is the seed
  the composed end-to-end demo builds on.

`agents/`, `drop/`, and `work/` are runtime directories (gitignored). All demo data is synthetic:
seeded synthetic personas of the public HMRC CH2 form, `owner@localhost` as the reviewer, `.example`
domains. No rendered Crown-copyright scan pixels are ever committed (golden scans regenerate from
seeds).
