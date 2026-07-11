"""The drop-folder watch daemon (design spec §3, §6 Stage 4) — hermetic, stub models, no endpoint.

Drives :func:`papereyes.watch.daemon.run_watch_cycle` with the deterministic stub client over a real
synthetic CH2 scan (rasterised by poppler), and asserts the Stage-4 daemon behaviour:

- a dropped scan produces the report + sidecar in the agent inbox and is archived to ``processed/``,
  with a sha-keyed line in ``processed.jsonl``;
- the SAME scan re-dropped is skipped (no second emit) — the paper-eyes-side idempotency receipt;
- a pipeline failure routes the scan to ``failed/`` with a ``<name>.error.txt`` note;
- an emit collision (a same-named report already present with differing bytes) also routes to
  ``failed/`` — the never-overwrite rule (§3.1 #4) — and ``--force`` re-runs a skipped scan.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from papereyes.config.loader import load_formpack, load_pipeline
from papereyes.synth.generator import synth_corpus
from papereyes.watch.daemon import WatchContext, run_watch_cycle
from tests.pipeline_support import StubModelClient
from tests.support import REPO_ROOT

UK_CH2 = REPO_ROOT / "formpacks" / "uk-ch2"

pytestmark = pytest.mark.skipif(
    shutil.which("pdftoppm") is None, reason="pdftoppm (poppler-utils) not installed"
)


def _persona01_scan(tmp_path: Path) -> Path:
    golden = tmp_path / "golden"
    synth_corpus(golden, base_seed=7, count=1, dpi=200)
    return golden / "persona-01.pdf"


def _make_ctx(tmp_path: Path, client: StubModelClient) -> WatchContext:
    return WatchContext(
        formpack=load_formpack(UK_CH2),
        formpack_dir=UK_CH2,
        pipeline_cfg=load_pipeline(REPO_ROOT / "pipeline.yaml"),
        client=client,
        drop_dir=tmp_path / "drop",
        inbox_dir=tmp_path / "agents" / "paper-clerk" / "inbox",
        workdir=tmp_path / "work",
    )


def _drop(scan: Path, ctx: WatchContext) -> Path:
    ctx.drop_dir.mkdir(parents=True, exist_ok=True)
    dest = ctx.drop_dir / scan.name
    shutil.copy2(scan, dest)
    return dest


def test_drop_emits_report_and_archives(tmp_path: Path) -> None:
    scan = _persona01_scan(tmp_path)
    ctx = _make_ctx(tmp_path, StubModelClient())
    _drop(scan, ctx)

    result = run_watch_cycle(ctx)

    assert result.emitted() == 1
    outcome = result.outcomes[0]
    assert outcome.status == "emitted"
    assert outcome.report_name is not None

    report = ctx.inbox_dir / f"{outcome.report_name}.txt"
    sidecar = ctx.inbox_dir / f"{outcome.report_name}.extraction.json"
    assert report.is_file() and sidecar.is_file()
    assert "Brandon Thomas" in report.read_text(encoding="utf-8")[:320]

    # the scan left drop/ for processed/, and processed.jsonl carries its sha
    assert not (ctx.drop_dir / scan.name).exists()
    assert (ctx.processed_dir / scan.name).is_file()
    assert outcome.scan_sha256 in ctx.processed_log.seen_shas()


def test_redropping_same_scan_does_not_emit_twice(tmp_path: Path) -> None:
    scan = _persona01_scan(tmp_path)
    ctx = _make_ctx(tmp_path, StubModelClient())

    _drop(scan, ctx)
    first = run_watch_cycle(ctx)
    report_name = first.outcomes[0].report_name
    assert report_name is not None
    reports_after_first = sorted(ctx.inbox_dir.glob("*.txt"))

    # re-drop the identical scan bytes
    _drop(scan, ctx)
    second = run_watch_cycle(ctx)

    assert second.outcomes[0].status == "skipped"
    # no second emit — same single report in the inbox (the idempotency receipt)
    assert sorted(ctx.inbox_dir.glob("*.txt")) == reports_after_first
    # the re-drop was archived out of drop/ (de-duplicated), so the loop stops re-seeing it
    assert not (ctx.drop_dir / scan.name).exists()


def test_pipeline_failure_routes_to_failed_tree(tmp_path: Path) -> None:
    scan = _persona01_scan(tmp_path)
    # a stub whose pages carry no CH2 markers -> identify refuses -> the scan must fail loudly
    ctx = _make_ctx(
        tmp_path, StubModelClient(convert_pages=["<loc_1><loc_2><loc_3><loc_4>Some other form"])
    )
    _drop(scan, ctx)

    result = run_watch_cycle(ctx)

    assert result.failed() == 1
    assert result.outcomes[0].status == "failed"
    assert (ctx.failed_dir / scan.name).is_file()
    err = ctx.failed_dir / f"{scan.name}.error.txt"
    assert err.is_file() and "identify" in err.read_text(encoding="utf-8").lower()
    # nothing emitted into the inbox on failure
    assert not any(ctx.inbox_dir.glob("*.txt"))


def test_collision_routes_to_failed_and_force_reruns(tmp_path: Path) -> None:
    scan = _persona01_scan(tmp_path)
    ctx = _make_ctx(tmp_path, StubModelClient())

    _drop(scan, ctx)
    first = run_watch_cycle(ctx)
    report_name = first.outcomes[0].report_name
    assert report_name is not None
    report = ctx.inbox_dir / f"{report_name}.txt"

    # Corrupt the emitted report in place, then force a re-run of the same scan: the byte-identical
    # re-emit now differs from the corrupted file -> collision -> the scan is routed to failed/.
    report.write_bytes(b"CORRUPTED BYTES\n")
    _drop(scan, ctx)
    forced = run_watch_cycle(ctx, force=True)

    assert forced.outcomes[0].status == "collision"
    assert (ctx.failed_dir / scan.name).is_file()
    assert report.read_bytes() == b"CORRUPTED BYTES\n"  # never overwritten (§3.1 #4)
