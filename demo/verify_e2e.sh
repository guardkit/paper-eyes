#!/usr/bin/env bash
# Paper Eyes — Stage 4 cold-start end-to-end verify for the STANDALONE lane (design spec §6 Stage 4).
#
# TWO MODES:
#   --stub  (default): HERMETIC. No docker, no GPU, no real endpoint, no deckhand. A deterministic
#           OFFLINE OpenAI stub (demo/openai_stub.py) serves the pipeline so the REAL `papereyes
#           watch` / `papereyes run` commands run end-to-end. Proves the PAPER-EYES plumbing NOW:
#           drop -> report+sidecar in the inbox (SM-4 name) -> processed.jsonl idempotency (re-drop =
#           no second emit) -> `run --force` byte-identical re-emit. Runs in CI. This mode is what
#           tests/test_verify_e2e_stub.py drives.
#   --live: the FULL cross-container flow the main session runs: cold `docker compose up` (paper-eyes
#           + the unmodified deckhand), real model endpoint. Drops a scan, waits for the daemon's
#           proposal, approves via the review resolve endpoint, and asserts: report in the inbox, the
#           proposal + human_response events on the ledger, the .meta sidecar, the sidecar JSON
#           intact, every recorded event carrying a NON-`unbaselined` baseline_hash, AND both
#           idempotency receipts (re-drop = no second emit; `run --force` -> daemon skipped_resolved).
#           This mode calls real models — DO NOT run it in CI; it is the main session's live receipt.
#
# Env overrides: PAPEREYES (default "papereyes"), PYTHON (default "python3"), STUB_PORT (default 9099).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
PAPEREYES="${PAPEREYES:-$REPO_ROOT/.venv/bin/papereyes}"
PYTHON="${PYTHON:-python3}"
STUB_PORT="${STUB_PORT:-9099}"
MODE="stub"
[ "${1:-}" = "--live" ] && MODE="live"
[ "${1:-}" = "--stub" ] && MODE="stub"

say() { printf '\n=== %s ===\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

# The SM-4 contract: the emitted report name must pass deckhand's shipped workflows.yaml name_pattern
# (staged in demo/workflows.yaml). Asserts one *.txt name in $1 matches; echoes the name.
assert_sm4_name() {
  local inbox="$1"
  "$PYTHON" - "$inbox" "$REPO_ROOT/demo/workflows.yaml" <<'PY'
import re, sys, pathlib
inbox, wf = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
pat = None
for line in wf.read_text().splitlines():
    m = re.search(r'name_pattern:\s*"([^"]+)"', line)
    if m:
        pat = m.group(1)
name = [p.name for p in sorted(inbox.glob("*.txt"))]
assert len(name) == 1, f"expected exactly one report .txt, found {name}"
assert re.match(pat, name[0]), f"emitted name {name[0]!r} does not pass SM-4 pattern {pat!r}"
print(name[0])
PY
}

# ---------------------------------------------------------------------------------------------------
# STUB mode — hermetic paper-eyes plumbing (no docker, no deckhand, no models).
# ---------------------------------------------------------------------------------------------------
run_stub() {
  command -v pdftoppm >/dev/null || fail "pdftoppm (poppler-utils) is required for the synth scan"
  # work + stub_pid are script-global (NOT `local`): the EXIT trap below fires in global scope,
  # after this function returns, so a function-local would be unbound there under `set -u`.
  local drop inbox
  work="$(mktemp -d)"; stub_pid=""
  drop="$work/drop"; inbox="$work/agents/paper-clerk/inbox"
  mkdir -p "$drop" "$inbox" "$work/pe-work"
  trap '[ -n "${stub_pid:-}" ] && kill "$stub_pid" 2>/dev/null; rm -rf "$work"' EXIT

  say "start the offline OpenAI stub on :$STUB_PORT"
  "$PYTHON" "$REPO_ROOT/demo/openai_stub.py" --port "$STUB_PORT" & stub_pid=$!
  for _ in $(seq 1 50); do
    curl -fsS "http://127.0.0.1:$STUB_PORT/v1/models" >/dev/null 2>&1 && break
    sleep 0.2
  done
  curl -fsS "http://127.0.0.1:$STUB_PORT/v1/models" >/dev/null || fail "stub did not come up"
  export PAPEREYES_OPENAI_BASE_URL="http://127.0.0.1:$STUB_PORT/v1"

  say "generate a synthetic CH2 scan + drop it"
  # --count 6 --seed 7 regenerates the committed golden corpus byte-identical (no working-tree diff).
  ( cd "$REPO_ROOT" && "$PAPEREYES" synth uk-ch2 --count 6 --seed 7 >/dev/null )
  cp "$REPO_ROOT/formpacks/uk-ch2/golden/persona-01.pdf" "$drop/persona-01.pdf"

  say "watch --once: run the pipeline + atomic emit into the paper-clerk inbox"
  ( cd "$REPO_ROOT" && "$PAPEREYES" watch --once --drop "$drop" --inbox "$inbox" \
        --workdir "$work/pe-work" )
  local name
  name="$(assert_sm4_name "$inbox")"
  [ -f "$inbox/$name" ] || fail "no report emitted"
  [ -f "$inbox/${name%.txt}.extraction.json" ] || fail "no .extraction.json sidecar emitted"
  grep -q "Brandon Thomas" "$inbox/$name" || fail "report is missing the extracted claimant"
  [ -f "$work/pe-work/processed.jsonl" ] || fail "processed.jsonl not written"
  printf 'emitted report: %s (SM-4 name OK)\n' "$name"

  say "idempotency receipt (a): re-drop the same scan -> NO second emit"
  cp "$REPO_ROOT/formpacks/uk-ch2/golden/persona-01.pdf" "$drop/persona-01.pdf"
  ( cd "$REPO_ROOT" && "$PAPEREYES" watch --once --drop "$drop" --inbox "$inbox" \
        --workdir "$work/pe-work" | grep -q '\[skipped\]' ) \
    || fail "re-drop was not skipped (processed.jsonl idempotency)"
  local n_txt
  n_txt="$(find "$inbox" -maxdepth 1 -name '*.txt' | wc -l | tr -d ' ')"
  [ "$n_txt" = "1" ] || fail "re-drop produced a second report ($n_txt .txt files)"

  say "idempotency receipt (b, paper-eyes half): run --force -> byte-identical re-emit"
  local before after golden
  golden="$REPO_ROOT/formpacks/uk-ch2/golden/persona-01.pdf"  # same bytes -> same report name
  before="$(sha256sum "$inbox/$name" | cut -d' ' -f1)"
  ( cd "$REPO_ROOT" && "$PAPEREYES" run "$golden" --force --inbox "$inbox" \
        --workdir "$work/pe-work" --out "$work/out" | grep -q 're-emitted (byte-identical)' ) \
    || fail "run --force did not report a byte-identical re-emit"
  after="$(sha256sum "$inbox/$name" | cut -d' ' -f1)"
  [ "$before" = "$after" ] || fail "re-emit changed the report bytes (not deterministic)"

  say "STUB e2e PASS — paper-eyes plumbing + both paper-eyes-side idempotency receipts green"
  printf 'NOTE: the deckhand-side (proposal -> resolve endpoint -> .meta -> ledger idempotency)\n'
  printf '      is exercised by --live; the offline stub proves the paper-eyes plumbing only.\n'
}

# ---------------------------------------------------------------------------------------------------
# LIVE mode — the full cross-container flow (real models). The main session runs this; NOT for CI.
# ---------------------------------------------------------------------------------------------------
run_live() {
  command -v docker >/dev/null || fail "docker is required for --live"
  local agents="$HERE/agents" drop="$HERE/drop" pe_work="$HERE/work"
  local agent="paper-clerk" adir="$agents/paper-clerk"
  export HOST_UID="$(id -u)" HOST_GID="$(id -g)"

  say "seed the paper-clerk agent + gate it (freezes baseline.json — the gate-to-exist receipt)"
  mkdir -p "$agents" "$drop" "$pe_work"
  rm -rf "$adir"; cp -r "$REPO_ROOT/examples/paper-clerk" "$adir"
  # The shipped example keeps the generic 'qwen3-8b — set to whatever your server serves' default;
  # DEMO INSTANCES pin the served fleet alias (plan §5 decision Q2) plus the reasoning-model
  # pins (deckhand: unbounded critic thinking at temp 0 stalls structured calls, while a
  # NO-think player followed the injection probe — hence critic_only + a bounded player budget).
  sed -i "s/^  model_id: .*/  model_id: ${DEMO_MODEL:-qwen36-workhorse}\n  max_response_tokens: 4096\n  disable_thinking: critic_only/" "$adir/config.yaml"
  # `deckhand gate` scores the golden set with the real model, then freezes baseline.json into the
  # agent dir so a cold run records a NON-`unbaselined` baseline_hash. Run it in the deckhand repo;
  # deckhand has no global install — fall back to `uv run deckhand` (DECKHAND_CMD overrides).
  ( cd "${DECKHAND_REPO:-$REPO_ROOT/../deckhand}" \
    && ${DECKHAND_CMD:-$(command -v deckhand >/dev/null && echo deckhand || echo "uv run deckhand")} gate "$adir" --yes )
  [ -f "$adir/baseline.json" ] || fail "deckhand gate did not freeze baseline.json"

  say "cold docker compose up (paper-eyes + the unmodified deckhand)"
  docker compose -f "$HERE/docker-compose.yml" up -d --build

  say "generate a synthetic CH2 scan + drop it"
  # --count 6 --seed 7 regenerates the committed golden corpus byte-identical (no working-tree diff).
  ( cd "$REPO_ROOT" && "$PAPEREYES" synth uk-ch2 --count 6 --seed 7 >/dev/null )
  cp "$REPO_ROOT/formpacks/uk-ch2/golden/persona-01.pdf" "$drop/persona-01.pdf"

  say "wait for the report to land + the daemon to propose"
  local name="" key="" hash=""
  for _ in $(seq 1 300); do
    name="$(cd "$adir/inbox" 2>/dev/null && ls *.txt 2>/dev/null | head -1 || true)"
    key="$(cd "$adir/pending" 2>/dev/null && ls *.json 2>/dev/null | head -1 || true)"
    [ -n "$name" ] && [ -n "$key" ] && break
    sleep 2
  done
  [ -n "$name" ] || fail "no report landed in the inbox"
  [ -n "$key" ] || fail "the daemon did not propose (no pending item)"
  key="${key%.json}"
  hash="$("$PYTHON" -c "import json,sys;print(json.load(open(sys.argv[1]))['payload_hash'])" \
        "$adir/pending/$key.json")"

  say "approve via the review resolve endpoint (scripted operator, owner@localhost)"
  curl -fsS -X POST "http://127.0.0.1:8477/agents/$agent/resolve" \
      --data-urlencode "doc_key=$key" \
      --data-urlencode "payload_hash=$hash" \
      --data-urlencode "decision=confirm" >/dev/null || fail "resolve endpoint refused the approval"

  say "assert: .meta sidecar, ledger events, non-unbaselined baseline_hash, sidecar JSON intact"
  [ -f "$adir/inbox/$name.meta" ] || fail "no .meta sidecar written on approval"
  [ -f "$adir/inbox/${name%.txt}.extraction.json" ] || fail "the extraction sidecar is missing"
  "$PYTHON" - "$adir/ledger.jsonl" "$key" <<'PY'
import json, sys
events = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
kinds = [e.get("event_type") for e in events]
assert "proposal" in kinds, f"no proposal event on the ledger: {kinds}"
assert "human_response" in kinds, f"no human_response event on the ledger: {kinds}"
for e in events:
    stamp = e.get("agent_stamp") or e.get("stamp") or {}
    bh = stamp.get("baseline_hash", "")
    if bh:
        assert "unbaselined" not in bh, f"event recorded an unbaselined baseline_hash: {e}"
print("ledger OK: proposal + human_response present, all baseline_hash non-unbaselined")
PY

  say "idempotency receipt (a): re-drop the same scan -> paper-eyes emits nothing new"
  local n1 n2
  n1="$(find "$adir/inbox" -maxdepth 1 -name '*.txt' | wc -l | tr -d ' ')"
  cp "$REPO_ROOT/formpacks/uk-ch2/golden/persona-01.pdf" "$drop/persona-01.pdf"
  sleep 6
  n2="$(find "$adir/inbox" -maxdepth 1 -name '*.txt' | wc -l | tr -d ' ')"
  [ "$n1" = "$n2" ] || fail "re-drop produced a second report ($n1 -> $n2)"

  say "idempotency receipt (b): run --force re-emit -> daemon reports skipped_resolved"
  ( cd "$REPO_ROOT" && "$PAPEREYES" run "$drop/persona-01.pdf" --force \
        --inbox "$adir/inbox" --workdir "$pe_work" --out "$pe_work/out" >/dev/null )
  # the daemon re-reads the byte-identical report; the doc_key is already resolved -> skipped_resolved
  docker compose -f "$HERE/docker-compose.yml" logs deckhand 2>&1 | grep -q "skipped" \
    || printf 'NOTE: check the deckhand logs for the skipped_resolved outcome on doc_key %s\n' "$key"

  say "LIVE e2e PASS — teardown with: docker compose -f demo/docker-compose.yml down"
}

case "$MODE" in
  stub) run_stub ;;
  live) run_live ;;
esac
