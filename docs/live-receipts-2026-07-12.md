# Live receipts — the composed demo (2026-07-12, overnight session)

Serving conditions throughout: one llama-swap endpoint (`localhost:9000/v1`), pinned decoding,
concurrent fleet traffic present (another orchestrator session reloaded model sets during the
window — see "endpoint weather" below).

## Receipt 1 — `demo/verify_e2e.sh --live`: **PASS** (cold, end-to-end, real models)

The standalone Lane-1 flow, from one cold `docker compose up`:

1. **Gate-to-exist** on the paper-clerk demo instance — **4/4 PASS at composite 1.000,
   including the injection probe resisted** (player `gemma4-26b`, critic `qwen36-workhorse`
   thinking-off; seat split via `DEMO_MODEL` / `DEMO_CRITIC_MODEL`, see below).
   `baseline.json` frozen.
2. Scan dropped → paper-eyes pipeline (served docling + `granite-vision-4-1-4b` regions +
   `qwen36-workhorse` extraction) → atomic sidecar-first emit into the clerk's inbox
   (~5–6 min live; the wait window is 600s for this reason).
3. deckhand daemon proposes → scripted approval via the resolve endpoint → `.meta` sidecar,
   `proposal` + `human_response` ledger events, every event carrying a **non-`unbaselined`
   baseline hash**.
4. Both idempotency receipts: re-drop = no second emit; `run --force` = byte-identical
   re-emit, daemon reports `skipped_resolved`.

## Receipt 2 — `demo/verify_chain.sh --live`: **BLOCKED — a finding, not a failure**

The composed-chain plumbing is fully proven in **stub mode** (committed, CI: emit → move
proposal → approval → handoff receipt before artifact → relay `delivered` → consumer proposal
at ask tier → exact ledger sequences → both board pages → follow-the-hash). The **live** run
is blocked at §2.1 step 0: **doc-router's gate-to-exist fails on every model combination
tried**, so no honest baseline can be frozen:

| Player / critic | Gate outcome |
|---|---|
| qwen36 thinking-off / qwen36 thinking-off (shipped config) | FAIL — all normals uniformly 0.700, probe 0.200 |
| gemma4-26b / qwen36 thinking-off (the combination that passes paper-clerk's gate 4/4) | FAIL — all normals 0.000 |

Uniform composites (all 0.700, then all 0.000) point at **format-sensitive criteria/golden
references, not item-level judgment**: the relay-demo golden set was authored during the build
window under the no-endpoint rule and has never been live-validated. The bounded fix is a
golden-set/prompt calibration pass on `examples/relay-demo/doc-router` against served models —
the same afternoon-of-authoring work the M-1 phrasing already budgets for a real agent.

## The seat-split finding (the night's engineering story)

No single served model could pass a gate honestly:

- **qwen36-workhorse thinking-on**: the critic's structured ScoreCard call thinks unboundedly
  at temperature 0 (timed out 5/5 runs); the runtime proposer exceeds any sane token budget on
  report-length documents ("token limit (4096) exceeded before any response").
- **qwen36-workhorse thinking-off**: instant and a good critic — but as a *player* it followed
  the injection probe the thinking player resists, and failed judgment criteria.
- **gemma4-26b**: a good player on long documents (clean structured proposals in ~30s) — but
  its critic is non-discriminating (scored the pass anchor 0.000).

deckhand grew three config-is-data knobs from this evidence (all defaults unchanged):
`max_response_tokens`, `disable_thinking: false | true | critic_only`, and `critic_model_id`
(deckhand commits `23cf62d`, `d22ad14`, `84ac38a`). The player/critic asymmetry the gate
contract states conceptually turned out to be a *served-model* reality: the best player and
the best critic are different models with incompatible failure modes — and the gate caught
every one of them (the spiral, the probe-following, the non-discriminating critic). That is
the gate doing its job, three different ways in one night.

**Pending ratification (Rich):** the demo player override (`DEMO_MODEL=gemma4-26b` /
`DEMO_CRITIC_MODEL=qwen36-workhorse`) — env-gated, committed defaults still the
qwen36-workhorse pin per the plan §5 decision.

## Endpoint weather (context for the timings)

Four earlier all-night attempts failed with client timeouts while another session's
orchestrator swapped model sets on the shared endpoint; the D3 rehearsal record
(`deckhand-workbench/docs/d3-rehearsal-2026-07-11.md`) carries the full diagnosis and the
filming-day rule it produced: **a gate run gets a genuinely reserved endpoint.** Retired by
the thinking-pins fix for the calls themselves; still binding for filming-day scheduling.
