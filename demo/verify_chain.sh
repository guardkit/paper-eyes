#!/usr/bin/env bash
# Paper Eyes — the COMPOSED end-to-end chain verify (master plan §2.1 trace, §4 half-day 4).
#
# The §2.1 artifact path, cold, in one script: a scan drops -> paper-eyes emits a report (sidecar
# first) into doc-router/inbox -> doc-router proposes a MOVE -> a human approves -> the move executor
# writes a handoff receipt BEFORE the artifact into outbox/reports/ -> the deterministic, model-free
# relay ferries it into digest-clerk/inbox with a provenance sidecar -> digest-clerk proposes at ask
# tier (composition granted nothing). Both ledgers' event sequences are pinned; both board pages
# render; the emitted name passes the relay contract's name_pattern (SM-4).
#
# TWO MODES:
#   --stub  (default): HERMETIC, deterministic, CI-runnable. NO docker, NO GPU, NO real endpoint.
#           The offline OpenAI stub (demo/openai_stub.py) serves paper-eyes' pipeline; the deckhand
#           half is driven IN-PROCESS by demo/chain_driver.py through deckhand's OWN watch / resolve
#           / relay / board code with deterministic stub proposers (no model). Needs a sibling
#           deckhand checkout with an importable venv — set DECKHAND_REPO / DECKHAND_PYTHON. This is
#           the exit gate for the assembly (tests/test_verify_chain_stub.py drives it).
#   --live: the FULL cross-container flow the main session films: cold `docker compose up` on
#           demo/docker-compose.chain.yml (paper-eyes + the unmodified deckhand + the relay), real
#           models, scripted approvals via the review /resolve endpoint. Calls real models — DO NOT
#           run in CI. This is the main session's live receipt.
#
# Env overrides: PAPEREYES (default "papereyes"), PYTHON (default "python3"), STUB_PORT (default
# 9098), DECKHAND_REPO (default ../deckhand), DECKHAND_PYTHON (default $DECKHAND_REPO/.venv/bin/python).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
PAPEREYES="${PAPEREYES:-$REPO_ROOT/.venv/bin/papereyes}"
PYTHON="${PYTHON:-python3}"
STUB_PORT="${STUB_PORT:-9098}"
DECKHAND_REPO="${DECKHAND_REPO:-$REPO_ROOT/../deckhand}"
DECKHAND_PYTHON="${DECKHAND_PYTHON:-$DECKHAND_REPO/.venv/bin/python}"
MODE="stub"
[ "${1:-}" = "--live" ] && MODE="live"
[ "${1:-}" = "--stub" ] && MODE="stub"

say() { printf '\n=== %s ===\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

# Seed the shared agents root by COPYING deckhand's shipped relay-demo agents + workflows.yaml — the
# composed demo reuses them verbatim, it never forks their configs (master plan §3 debt flag a). The
# producer inbox is started clean (the relay-demo ships a sample .txt we don't want in the chain).
seed_agents() {
  local agents="$1"
  cp -r "$DECKHAND_REPO/examples/relay-demo/doc-router" "$agents/doc-router"
  cp -r "$DECKHAND_REPO/examples/relay-demo/digest-clerk" "$agents/digest-clerk"
  cp "$DECKHAND_REPO/examples/relay-demo/workflows.yaml" "$agents/workflows.yaml"
  rm -f "$agents/doc-router/inbox/"* 2>/dev/null || true
  mkdir -p "$agents/doc-router/inbox"
}

# ---------------------------------------------------------------------------------------------------
# STUB mode — hermetic: offline stub for paper-eyes, in-process deckhand drive. No docker, no models.
# ---------------------------------------------------------------------------------------------------
run_stub() {
  command -v pdftoppm >/dev/null || fail "pdftoppm (poppler-utils) is required for the synth scan"
  [ -d "$DECKHAND_REPO/examples/relay-demo" ] \
    || fail "no deckhand checkout at $DECKHAND_REPO (set DECKHAND_REPO to a guardkit/deckhand clone)"
  [ -x "$DECKHAND_PYTHON" ] \
    || fail "no deckhand venv python at $DECKHAND_PYTHON (set DECKHAND_PYTHON)"
  "$DECKHAND_PYTHON" -c "import deckhand" >/dev/null 2>&1 \
    || fail "$DECKHAND_PYTHON cannot import deckhand — point DECKHAND_PYTHON at deckhand's venv"

  # work + stub_pid are script-global (NOT `local`): the EXIT trap fires in global scope.
  local agents drop inbox
  work="$(mktemp -d)"; stub_pid=""
  agents="$work/agents"; drop="$work/drop"; inbox="$agents/doc-router/inbox"
  mkdir -p "$agents" "$drop" "$work/pe-work"
  trap '[ -n "${stub_pid:-}" ] && kill "$stub_pid" 2>/dev/null; rm -rf "$work"' EXIT

  say "seed the shared agents root (copy-at-build from deckhand's relay-demo — no fork)"
  seed_agents "$agents"

  say "start the offline OpenAI stub on :$STUB_PORT"
  "$PYTHON" "$REPO_ROOT/demo/openai_stub.py" --port "$STUB_PORT" & stub_pid=$!
  for _ in $(seq 1 50); do
    curl -fsS "http://127.0.0.1:$STUB_PORT/v1/models" >/dev/null 2>&1 && break
    sleep 0.2
  done
  curl -fsS "http://127.0.0.1:$STUB_PORT/v1/models" >/dev/null || fail "stub did not come up"
  export PAPEREYES_OPENAI_BASE_URL="http://127.0.0.1:$STUB_PORT/v1"

  say "generate a synthetic CH2 scan + drop it"
  # --count 6 --seed 7 regenerates the committed golden corpus byte-identical (no working-tree diff);
  # the rendered PDF itself is never committed (OGL crest exclusion), it is produced here from seeds.
  ( cd "$REPO_ROOT" && "$PAPEREYES" synth uk-ch2 --count 6 --seed 7 >/dev/null )
  cp "$REPO_ROOT/formpacks/uk-ch2/golden/persona-01.pdf" "$drop/persona-01.pdf"

  say "paper-eyes: run the pipeline + atomic emit into doc-router/inbox (SM-1 composed pipeline)"
  # The composed demo/pipeline.yaml carries the model pins + emit.agent_inbox=/agents/doc-router/inbox
  # (SM-1); --inbox retargets that to the host tmp path for this hermetic run.
  ( cd "$REPO_ROOT" && "$PAPEREYES" watch --once --pipeline "$REPO_ROOT/demo/pipeline.yaml" \
        --drop "$drop" --inbox "$inbox" --workdir "$work/pe-work" )
  local n_txt
  n_txt="$(find "$inbox" -maxdepth 1 -name '*.txt' | wc -l | tr -d ' ')"
  [ "$n_txt" = "1" ] || fail "paper-eyes did not emit exactly one report into doc-router/inbox"

  # The offline stub's job is done (the deckhand drive uses stub proposers, never a model).
  kill "$stub_pid" 2>/dev/null || true; stub_pid=""

  say "deckhand: drive the §2.1 chain in-process (watch -> resolve -> relay -> board)"
  "$DECKHAND_PYTHON" "$REPO_ROOT/demo/chain_driver.py" --agents-root "$agents"

  say "STUB chain PASS — paper-eyes emit + the deckhand crossing verified from cold, hermetically"
}

# ---------------------------------------------------------------------------------------------------
# LIVE mode — the full cross-container flow (real models). The main session runs this; NOT for CI.
# ---------------------------------------------------------------------------------------------------
run_live() {
  command -v docker >/dev/null || fail "docker is required for --live"
  [ -d "$DECKHAND_REPO/examples/relay-demo" ] || fail "no deckhand checkout at $DECKHAND_REPO"
  local agents="$HERE/agents" drop="$HERE/drop" compose="$HERE/docker-compose.chain.yml"
  export HOST_UID="$(id -u)" HOST_GID="$(id -g)"

  say "seed the shared agents root + gate doc-router (freeze its baseline — the gate-to-exist scene)"
  mkdir -p "$agents" "$drop"
  rm -rf "$agents/doc-router" "$agents/digest-clerk" "$agents/workflows.yaml"
  seed_agents "$agents"
  # §2.1 step 0: doc-router is drafted from examples/filed-history-routes/ in the workbench, corrected,
  # mv'd to live names, then gated — this IS that gate. It scores the golden set with the REAL model
  # and freezes baseline.json so cold-run ledger events carry a NON-`unbaselined` baseline_hash. Only
  # the producer (the clerk the chain runs) is gated here; digest-clerk runs honestly unbaselined and
  # still proposes at ask tier on arrival (composition granted nothing).
  ( cd "$DECKHAND_REPO" && deckhand gate "$agents/doc-router" --yes )
  [ -f "$agents/doc-router/baseline.json" ] || fail "doc-router gate did not freeze baseline.json"

  say "cold docker compose up (paper-eyes + the unmodified deckhand + the relay)"
  docker compose -f "$compose" down --remove-orphans >/dev/null 2>&1 || true
  docker compose -f "$compose" up -d --build

  say "generate a synthetic CH2 scan + drop it"
  ( cd "$REPO_ROOT" && "$PAPEREYES" synth uk-ch2 --count 6 --seed 7 >/dev/null )
  cp "$REPO_ROOT/formpacks/uk-ch2/golden/persona-01.pdf" "$drop/persona-01.pdf"

  say "wait for the report to land in doc-router/inbox + the daemon to propose the move"
  local name="" key="" hash=""
  for _ in $(seq 1 120); do
    name="$(cd "$agents/doc-router/inbox" 2>/dev/null && ls ./*.txt 2>/dev/null | head -1 || true)"
    key="$(cd "$agents/doc-router/pending" 2>/dev/null && ls ./*.json 2>/dev/null | head -1 || true)"
    [ -n "$name" ] && [ -n "$key" ] && break
    sleep 2
  done
  [ -n "$name" ] || fail "no report landed in doc-router/inbox"
  [ -n "$key" ] || fail "the doc-router daemon did not propose (no pending item)"
  name="$(basename "$name")"; key="$(basename "${key%.json}")"
  hash="$("$PYTHON" -c "import json,sys;print(json.load(open(sys.argv[1]))['payload_hash'])" \
        "$agents/doc-router/pending/$key.json")"

  say "approve the move via the review resolve endpoint (owner@localhost)"
  curl -fsS -X POST "http://127.0.0.1:8477/agents/doc-router/resolve" \
      --data-urlencode "doc_key=$key" --data-urlencode "payload_hash=$hash" \
      --data-urlencode "decision=confirm" >/dev/null || fail "doc-router resolve refused the approval"

  say "wait for the relay to deliver into digest-clerk/inbox + its daemon to propose"
  local ckey=""
  for _ in $(seq 1 120); do
    [ -f "$agents/digest-clerk/inbox/$name" ] || { sleep 2; continue; }
    ckey="$(cd "$agents/digest-clerk/pending" 2>/dev/null && ls ./*.json 2>/dev/null | head -1 || true)"
    [ -n "$ckey" ] && break
    sleep 2
  done
  [ -f "$agents/digest-clerk/inbox/$name" ] || fail "the relay did not deliver into digest-clerk/inbox"
  [ -f "$agents/digest-clerk/inbox/$name.provenance.json" ] || fail "no provenance sidecar delivered"
  [ -n "$ckey" ] || fail "digest-clerk did not propose at ask tier"; ckey="$(basename "${ckey%.json}")"
  local chash
  chash="$("$PYTHON" -c "import json,sys;print(json.load(open(sys.argv[1]))['payload_hash'])" \
        "$agents/digest-clerk/pending/$ckey.json")"

  say "approve the consumer title_tag (second approval — composition granted nothing)"
  curl -fsS -X POST "http://127.0.0.1:8477/agents/digest-clerk/resolve" \
      --data-urlencode "doc_key=$ckey" --data-urlencode "payload_hash=$chash" \
      --data-urlencode "decision=confirm" >/dev/null || fail "digest-clerk resolve refused"

  say "assert: relay-log 'delivered', both board pages render, egress recorded"
  grep -q '"delivered"' "$agents/relay/relay-log.jsonl" || fail "no 'delivered' line in the relay log"
  curl -fsS "http://127.0.0.1:8477/agents/doc-router/board"  >/dev/null || fail "doc-router board 500"
  curl -fsS "http://127.0.0.1:8477/agents/digest-clerk/board" >/dev/null || fail "digest board 500"
  curl -fsS "http://127.0.0.1:8477/board" >/dev/null || fail "fleet board 500"

  say "LIVE chain PASS — teardown with: docker compose -f demo/docker-compose.chain.yml down"
}

case "$MODE" in
  stub) run_stub ;;
  live) run_live ;;
esac
