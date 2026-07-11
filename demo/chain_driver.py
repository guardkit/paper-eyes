#!/usr/bin/env python3
"""Composed-demo chain driver — the deckhand half of ``demo/verify_chain.sh --stub`` (master plan
§2.1). Run by DECKHAND's venv python (it imports ``deckhand``; paper-eyes never depends on it).

paper-eyes has already emitted a report into the shared agents root
(``doc-router/inbox/<report>.txt`` + its ``.extraction.json`` sidecar). This driver then exercises
deckhand's OWN watch / resolve / relay / board code in-process with deterministic stub proposers —
the same hermetic pattern deckhand's ``tests/test_relay_end_to_end.py`` uses, so NO model is ever
called — and asserts the §2.1 trace end-to-end:

  report emitted (sidecar-first)  ->  move proposed (fields.folder=reports, move_dest bound)
  ->  approved (resolve, confirm)  ->  handoff receipt beside the artifact in outbox/reports/
  ->  relay 'delivered' (+ provenance sidecar, source unlinked)
  ->  consumer proposal at ask tier (composition granted nothing)
  ->  both ledgers' event sequences exact
  ->  board pages render both ladders (producer move streak advanced; consumer title_tag at ask)
  ->  SM-4: the emitted report name passes the shipped workflows.yaml name_pattern.

Deterministic and hermetic; imports deckhand + stdlib only. This is BUILD EVIDENCE for CI — the
aired receipts come exclusively from the main session's live run (``demo/verify_chain.sh --live``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from deckhand.agents.filing_clerk import FilingProposal
from deckhand.config.loader import load_role_config
from deckhand.engine.identity import establish_operator
from deckhand.relay.loop import RelayState, run_relay_cycle
from deckhand.relay.models import load_workflows_config
from deckhand.relay.receipts import sha256_file
from deckhand.watch import PendingStore, resolve_pending, run_watch_cycle
from deckhand.watch.executor import HANDOFF_SUFFIX
from deckhand.web.board import BoardAssembler
from deckhand.web.board_render import render_agent_board

# The exact on-disk per-agent ledger sequences (deckhand's watch/resolve split records the human
# approval in a separate ledger open, AFTER the watch cycle's session_end — pinned in deckhand's
# own tests/test_relay_end_to_end.py).
PRODUCER_SEQ = ["session_start", "proposal", "session_end", "human_response"]
CONSUMER_SEQ = ["session_start", "proposal", "session_end"]


def say(step: str) -> None:
    print(f"\n--- {step} ---")


def fail(msg: str) -> None:
    print(f"CHAIN FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def check(cond: object, msg: str) -> None:
    if not cond:
        fail(msg)


def _operator():  # type: ignore[no-untyped-def]
    # The neutral demo operator, pinned everywhere (SM-6); a fixed fingerprint keeps the run
    # deterministic — no live-machine identity leaks into the ledger stamps.
    return establish_operator(
        "owner@localhost", os_user="owner", fingerprint="fp:chaindriver0000000000000000"
    )


def _sha_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _kinds(ledger_path: Path) -> list[str]:
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    return [str(json.loads(line)["event_type"]) for line in lines if line.strip()]


def _name_pattern(workflows_yaml: Path) -> re.Pattern[str]:
    match = re.search(r'name_pattern:\s*"([^"]+)"', workflows_yaml.read_text(encoding="utf-8"))
    check(match is not None, "no name_pattern in the agents root workflows.yaml")
    return re.compile(match.group(1))  # type: ignore[union-attr]


def _proposer_reports(name: str, text: str) -> FilingProposal:
    # The composed demo routes the CH2 report to outbox/reports/ (doc-router's declared enum).
    return FilingProposal(title=f"{name} route", folder="reports", tags=["reports"])


def _proposer_title(name: str, text: str) -> FilingProposal:
    return FilingProposal(title=f"{name} digest", folder="reports", tags=["digest"])


def run(agents_root: Path) -> None:
    producer = agents_root / "doc-router"
    consumer = agents_root / "digest-clerk"
    wf_path = agents_root / "workflows.yaml"
    check(producer.is_dir() and consumer.is_dir(), "agents root missing doc-router/digest-clerk")
    check(wf_path.is_file(), "agents root missing workflows.yaml")

    operator = _operator()
    producer_cfg = load_role_config(producer / "config.yaml")
    consumer_cfg = load_role_config(consumer / "config.yaml")
    pattern = _name_pattern(wf_path)

    # --- (2) the emitted report sits in the producer inbox, sidecar-first ----------------------
    say("report emitted into doc-router/inbox (sidecar-first)")
    reports = sorted((producer / "inbox").glob("*.txt"))
    check(len(reports) == 1, f"expected exactly one report .txt in the inbox, found {reports}")
    report = reports[0]
    report_name = report.name
    content_sha = _sha_bytes(report.read_bytes())
    extraction = producer / "inbox" / (report.stem + ".extraction.json")
    check(extraction.is_file(), "the .extraction.json sidecar is missing (sidecar-first emit)")
    check(bool(pattern.match(report_name)), f"SM-4: {report_name!r} fails the name_pattern")
    print(f"report {report_name}  content={content_sha[:23]}...  sidecar ok  SM-4 name ok")

    # --- (3) doc-router proposes a move -> outbox/reports/ -------------------------------------
    say("doc-router watch cycle: propose the move")
    run_watch_cycle(producer, config=producer_cfg, operator=operator, proposer=_proposer_reports)
    pending = PendingStore(producer).list_items()
    check(len(pending) == 1, f"expected exactly one pending move, found {len(pending)}")
    item = pending[0]
    check(item.capability_id == "move", f"producer proposed {item.capability_id!r}, not a move")
    check(item.folder == "reports", f"move folder is {item.folder!r}, not 'reports'")
    check(
        item.move_dest == f"outbox/reports/{report_name}",
        f"move_dest {item.move_dest!r} is not bound to outbox/reports/{report_name}",
    )
    print(f"proposal: move -> {item.move_dest}  (fields.folder=reports, move_dest bound)")

    # --- (4) approve (resolve, confirm) -> execute_move, receipt BEFORE artifact ---------------
    say("approve the move (resolve, confirm) — the receipt is written before the artifact")
    resolve_pending(
        producer,
        config=producer_cfg,
        operator=operator,
        doc_key=item.doc_key,
        decision="confirm",
        expected_payload_hash=item.payload_hash,
    )
    out_artifact = producer / "outbox" / "reports" / report_name
    out_receipt = producer / "outbox" / "reports" / (report_name + HANDOFF_SUFFIX)
    check(out_artifact.is_file(), "the approved report is not in outbox/reports/")
    # Receipt-before-artifact: execute_move fsyncs the handoff receipt, THEN renames the artifact in
    # (deckhand/watch/executor.py). Its presence beside the artifact is what the relay's anti-bypass
    # check requires; a receiptless artifact quarantines (deckhand's negative-golden test).
    check(out_receipt.is_file(), "no handoff receipt beside the artifact (receipt-before-artifact)")
    check(not report.exists(), "the report was not moved out of the inbox")
    receipt = json.loads(out_receipt.read_text(encoding="utf-8"))
    check(
        receipt.get("artifact_sha256") == content_sha,
        "handoff receipt artifact_sha256 does not match the report bytes",
    )
    check(
        receipt.get("auto") is False and receipt.get("proposal_id") == item.proposal_id,
        "handoff receipt does not name the confirmed proposal",
    )
    prod_kinds = _kinds(producer / "ledger.jsonl")
    check(prod_kinds == PRODUCER_SEQ, f"producer ledger sequence {prod_kinds} != {PRODUCER_SEQ}")
    print(f"moved -> outbox/reports/{report_name} (+ handoff receipt); inbox cleared; confirmed")

    # --- (5) the relay validates + verifies, then delivers the crossing ------------------------
    say("relay cycle: validate the contract + verify the receipt vs the ledger, then deliver")
    result = run_relay_cycle(agents_root, load_workflows_config(wf_path), RelayState())
    check(result.delivered() == 1, f"relay delivered {result.delivered()} (expected 1)")
    delivered = consumer / "inbox" / report_name
    provenance = consumer / "inbox" / (report_name + ".provenance.json")
    check(delivered.is_file(), "the report was not delivered into digest-clerk/inbox")
    check(provenance.is_file(), "no provenance sidecar beside the delivered report")
    check(
        not out_artifact.exists() and not out_receipt.exists(),
        "the relay did not unlink the source artifact + receipt after delivery",
    )
    prov = json.loads(provenance.read_text(encoding="utf-8"))
    log_lines = (agents_root / "relay" / "relay-log.jsonl").read_text(encoding="utf-8").splitlines()
    delivered_log = [json.loads(x) for x in log_lines if x.strip() and '"delivered"' in x]
    check(len(delivered_log) == 1, "expected exactly one 'delivered' relay-log line")
    # Follow the hash: identical at the receipt, the relay log, the provenance, the delivered file.
    check(prov.get("artifact_sha256") == content_sha, "provenance sha does not match the report")
    check(delivered_log[0].get("artifact_sha256") == content_sha, "relay-log sha does not match")
    check(sha256_file(delivered) == content_sha, "delivered file bytes drifted from the source")
    print(f"delivered -> digest-clerk/inbox/{report_name} (+ provenance); relay-log 'delivered'")

    # --- (6) digest-clerk proposes at ask tier — composition granted nothing -------------------
    say("digest-clerk watch cycle: a fresh title_tag proposal at ask tier")
    run_watch_cycle(consumer, config=consumer_cfg, operator=operator, proposer=_proposer_title)
    cons_pending = PendingStore(consumer).list_items()
    check(len(cons_pending) == 1, f"expected one consumer proposal, found {len(cons_pending)}")
    check(
        cons_pending[0].capability_id == "title_tag",
        f"consumer proposed {cons_pending[0].capability_id!r}, not title_tag",
    )
    cons_kinds = _kinds(consumer / "ledger.jsonl")
    check(cons_kinds == CONSUMER_SEQ, f"consumer ledger sequence {cons_kinds} != {CONSUMER_SEQ}")
    print("consumer: title_tag proposal at ask tier (composition granted nothing)")

    # --- (7) both board pages render their ladders + recorded egress ---------------------------
    say("board: both agents' pages render their ladders + recorded egress")
    board = BoardAssembler(agents_root, operator)
    p_page = board.page("doc-router")
    c_page = board.page("digest-clerk")
    check(p_page is not None and c_page is not None, "board could not assemble both agent pages")
    move_lad = next((lad for lad in p_page.ladders if lad.capability_id == "move"), None)
    title_lad = next((lad for lad in c_page.ladders if lad.capability_id == "title_tag"), None)
    check(move_lad is not None, "producer board has no move ladder")
    check(title_lad is not None, "consumer board has no title_tag ladder")
    check(move_lad.streak >= 1, f"producer move streak {move_lad.streak} did not advance")
    check(title_lad.tier == "ask", f"consumer title_tag tier is {title_lad.tier}, expected ask")
    check("move" in render_agent_board(p_page), "producer board HTML missing the move ladder")
    check("title_tag" in render_agent_board(c_page), "consumer board HTML missing title_tag")
    check(
        p_page.egress.loopback_only and c_page.egress.loopback_only,
        "recorded egress is not loopback-only on both boards",
    )
    print(f"board: doc-router move streak={move_lad.streak}; digest-clerk title_tag ask; loopback")

    say("STUB chain PASS")
    print("STUB chain PASS — the composed §2.1 trace is green end-to-end (deterministic, no model)")


def main() -> None:
    ap = argparse.ArgumentParser(description="composed-demo chain driver (deckhand half, stub)")
    ap.add_argument("--agents-root", required=True, help="the shared, seeded agents/ root")
    args = ap.parse_args()
    run(Path(args.agents_root))


if __name__ == "__main__":
    main()
