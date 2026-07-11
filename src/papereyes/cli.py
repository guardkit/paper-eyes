"""The ``papereyes`` command line.

Stage 0-1 surface (design spec §2 IN, §6):

- ``papereyes version`` — print the version.
- ``papereyes check [TARGET]`` — load a formpack dir (or a ``pipeline.yaml``) as data and
  report whether it is well-formed. Exit 0 on OK, 1 on a malformed config.
- ``papereyes init DIR [--name NAME]`` — scaffold a new formpack (formpack.yaml + schema.json
  + golden/) as data, valid on creation.
- ``papereyes synth FORMPACK [--seed S --count N --dpi D --expected-only]`` — (re)generate the
  synthetic golden corpus: seeded personas -> rendered form -> image-only scan + expected JSON.
- ``papereyes fetch-forms FORMPACK [--url URL --dest DIR --no-licence-check]`` — fetch the blank
  public form by URL, verify its sha256 pin, and probe its licence. Optional (render mode needs
  no blank); the fetched blank is written to a gitignored build dir, never committed.
- ``papereyes run SCAN [--formpack FP --workdir DIR --out DIR --region-model M]`` — run the
  pipeline on one scan against the real served models: report + ``.extraction.json`` + provenance.
- ``papereyes gate FORMPACK [--workdir DIR --region-model M --no-baseline]`` — the extraction
  gate: score every golden scan end-to-end, print the diff table, freeze ``formpack.baseline.json``.
- ``papereyes watch [--once] [--drop DIR --inbox DIR --workdir DIR ...]`` — the drop-folder daemon:
  watch ``drop/`` for scanned PDFs, run the pipeline, and atomically emit the report + sidecar into
  the configured deckhand agent ``inbox/`` (design spec §3, §6 Stage 4).
- ``papereyes run SCAN --emit [--force]`` — additionally emit the report + sidecar atomically into
  the configured agent ``inbox/``; ``--force`` re-emits a byte-identical report for an
  already-emitted scan (the deckhand-side ledger-idempotency receipt, §6 Stage 4 gate (b)).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from papereyes import __version__
from papereyes.config.loader import load_formpack
from papereyes.errors import PaperEyesError
from papereyes.formpack.check import check_target
from papereyes.formpack.scaffold import scaffold_formpack

FORMPACKS_ROOT = "formpacks"


def _resolve_formpack_dir(target: str) -> Path:
    """Resolve a formpack argument: a path to a dir, or a bare name under ``formpacks/``."""
    direct = Path(target)
    if (direct / "formpack.yaml").is_file():
        return direct
    under_root = Path(FORMPACKS_ROOT) / target
    if (under_root / "formpack.yaml").is_file():
        return under_root
    raise PaperEyesError(
        f"no formpack found for {target!r} (looked at {direct} and {under_root})"
    )


def _cmd_version() -> int:
    print(f"papereyes {__version__}")
    return 0


def _cmd_check(target: str | None) -> int:
    resolved = target or "pipeline.yaml"
    report = check_target(resolved)
    print(report.render(), file=sys.stdout if report.ok else sys.stderr)
    return 0 if report.ok else 1


def _cmd_init(dest: str, name: str | None) -> int:
    formpack_name = name or Path(dest).name or "my-formpack"
    try:
        written = scaffold_formpack(dest, name=formpack_name)
    except (PaperEyesError, OSError) as exc:
        print(f"init FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"scaffolded formpack {formpack_name!r} in {dest}:")
    for path in written:
        print(f"    {path}")
    print("next:")
    print(f"    1. edit the TODOs in {dest}/formpack.yaml and {dest}/schema.json")
    print(f"    2. papereyes check {dest}")
    return 0


def _cmd_synth(
    target: str, *, seed: int | None, count: int | None, dpi: int | None, expected_only: bool
) -> int:
    from papereyes.synth.generator import synth_corpus

    try:
        formpack_dir = _resolve_formpack_dir(target)
        formpack = load_formpack(formpack_dir)
    except (PaperEyesError, OSError) as exc:
        print(f"synth FAILED: {exc}", file=sys.stderr)
        return 1

    base_seed = seed if seed is not None else formpack.synth.base_seed
    n = count if count is not None else formpack.synth.count
    d = dpi if dpi is not None else formpack.synth.dpi
    try:
        result = synth_corpus(
            formpack_dir / "golden",
            base_seed=base_seed,
            count=n,
            dpi=d,
            expected_only=expected_only,
        )
    except (PaperEyesError, OSError) as exc:
        print(f"synth FAILED: {exc}", file=sys.stderr)
        return 1

    mode = "expected-only" if expected_only else f"scans@{result.dpi}dpi + expected"
    print(
        f"synth {formpack.slug()}: {result.count} doc(s) from base seed {result.base_seed} "
        f"({mode})"
    )
    for doc in result.docs:
        scan = doc.scan if doc.scan_written else "(scan skipped)"
        print(f"    {doc.id} seed={doc.seed} -> {doc.expected} [{doc.expected_sha256[:12]}] {scan}")
    print("    scans are NOT committed (regenerate from seeds); expected JSONs + seeds.json are.")
    return 0


def _cmd_fetch_forms(
    target: str, *, url: str | None, dest: str, check_licence: bool
) -> int:
    from papereyes.fetch.forms import fetch_form

    try:
        formpack_dir = _resolve_formpack_dir(target)
        formpack = load_formpack(formpack_dir)
        result = fetch_form(formpack, dest, url=url, check_licence=check_licence)
    except (PaperEyesError, OSError) as exc:
        print(f"fetch-forms FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"fetched blank for {formpack.slug()} from {result.url}")
    print(f"    -> {result.dest} (gitignored; never committed)")
    print(f"    sha256: {result.sha256}")
    pin_note = "matched" if result.pin_matched else "UNSET — pin this sha in formpack.yaml"
    print(f"    pin: {pin_note}")
    print(f"    licence probe: {'found' if result.licence_ok else 'not checked'}")
    return 0


def _build_client(pipeline_path: str, region_model: str | None):  # type: ignore[no-untyped-def]
    from papereyes.config.loader import load_pipeline
    from papereyes.pipeline.client import HttpModelClient

    pipeline_cfg = load_pipeline(pipeline_path)
    client = HttpModelClient(
        pipeline_cfg.endpoint.base_url,
        pipeline_cfg.models,
        pipeline_cfg.decoding,
        region_model=region_model,
    )
    return pipeline_cfg, client


def _resolve_inbox(pipeline_cfg: Any, inbox: str | None) -> Path:
    """The agent inbox the emit lands in: an explicit ``--inbox`` override, else the pipeline's
    ``emit.agent_inbox`` (the standalone default is ``examples/paper-clerk``'s inbox; the composed
    demo repoints this at a move-first producer with a config value, no code change — SM-1)."""
    return Path(inbox) if inbox else Path(pipeline_cfg.emit.agent_inbox)


def _cmd_run(
    scan: str, target: str, *, workdir: str, out: str | None, region_model: str | None,
    pipeline_path: str, emit: bool, force: bool, inbox: str | None,
) -> int:
    import json

    from papereyes.pipeline.run import run_pipeline
    from papereyes.watch.emit import atomic_emit

    run_workdir = Path(workdir) / Path(scan).stem
    try:
        pipeline_cfg, client = _build_client(pipeline_path, region_model)
        formpack_dir = _resolve_formpack_dir(target)
        formpack = load_formpack(formpack_dir)
        result = run_pipeline(
            scan, formpack, formpack_dir, pipeline_cfg, client,
            workdir=run_workdir, region_model=region_model,
        )
    except (PaperEyesError, OSError) as exc:
        print(f"run FAILED: {exc}", file=sys.stderr)
        return 1

    out_dir = Path(out) if out else run_workdir
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"{result.report_name}.txt"
    extraction_path = out_dir / f"{result.report_name}.extraction.json"
    report_path.write_text(result.report_text, encoding="utf-8")
    extraction_path.write_text(
        json.dumps(result.extraction, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"run {formpack.slug()}: {Path(scan).name}")
    print(f"    report     -> {report_path}")
    print(f"    extraction -> {extraction_path}")
    print(f"    provenance -> {run_workdir / 'provenance.json'}")
    print(f"    regions triggered: {', '.join(result.provenance['regions_triggered']) or 'none'}")
    print(f"    total time: {result.provenance['timings_s'].get('total')}s")

    if emit or force:
        inbox_dir = _resolve_inbox(pipeline_cfg, inbox)
        outcome = atomic_emit(
            inbox_dir, result.report_name, result.report_text, result.extraction
        )
        if outcome.status == "collision":
            # Never overwrite an existing report with differing bytes (§3.1 #4).
            print(f"emit REFUSED: {outcome.detail}", file=sys.stderr)
            return 1
        verb = {"emitted": "emitted", "identical": "re-emitted (byte-identical)"}[outcome.status]
        print(f"    {verb} -> {outcome.report_path}")
        print(f"    sidecar    -> {outcome.sidecar_path}")
    return 0


def _cmd_gate(
    target: str, *, workdir: str, region_model: str | None, freeze: bool, pipeline_path: str,
) -> int:
    from papereyes.gate import render_diff_table, run_gate, write_baseline

    try:
        pipeline_cfg, client = _build_client(pipeline_path, region_model)
        formpack_dir = _resolve_formpack_dir(target)
        formpack = load_formpack(formpack_dir)
        result = run_gate(
            formpack, formpack_dir, pipeline_cfg, client,
            workdir=Path(workdir), region_model=region_model,
        )
    except (PaperEyesError, OSError) as exc:
        print(f"gate FAILED: {exc}", file=sys.stderr)
        return 1

    print(render_diff_table(result))
    if result.passed and freeze:
        path = write_baseline(formpack_dir, result)
        print(f"baseline frozen -> {path}")
    elif not result.passed:
        print("gate FAILED: floor not met or a required field missing — baseline NOT frozen",
              file=sys.stderr)
    return 0 if result.passed else 1


def _cmd_watch(
    target: str, *, drop: str | None, inbox: str | None, workdir: str, region_model: str | None,
    pipeline_path: str, once: bool,
) -> int:
    from papereyes.watch.daemon import (
        DEFAULT_POLL_SECONDS,
        WatchContext,
        run_watch_cycle,
        watch_forever,
    )

    try:
        pipeline_cfg, client = _build_client(pipeline_path, region_model)
        formpack_dir = _resolve_formpack_dir(target)
        formpack = load_formpack(formpack_dir)
    except (PaperEyesError, OSError) as exc:
        print(f"watch FAILED: {exc}", file=sys.stderr)
        return 1

    drop_dir = Path(drop) if drop else Path(pipeline_cfg.watch.drop_dir)
    inbox_dir = _resolve_inbox(pipeline_cfg, inbox)
    poll = pipeline_cfg.watch.poll_seconds or DEFAULT_POLL_SECONDS
    ctx = WatchContext(
        formpack=formpack,
        formpack_dir=formpack_dir,
        pipeline_cfg=pipeline_cfg,
        client=client,
        drop_dir=drop_dir,
        inbox_dir=inbox_dir,
        workdir=Path(workdir),
        region_model=region_model,
    )
    print(f"watch {formpack.slug()}: {drop_dir} -> {inbox_dir}")
    if once:
        result = run_watch_cycle(ctx)
        for o in result.outcomes:
            detail = f" — {o.detail}" if o.detail else ""
            name = o.report_name or "-"
            print(f"    [{o.status}] {o.scan_name} -> {name}{detail}")
        print(
            f"cycle: {result.emitted()} emitted, {result.skipped()} skipped, "
            f"{result.failed()} failed"
        )
        return 0
    try:
        watch_forever(ctx, poll_seconds=poll)
    except KeyboardInterrupt:  # pragma: no cover - interactive stop
        print("watch stopped.", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="papereyes",
        description="papereyes — scanned public-form intake into deterministic JSON.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", help="print the papereyes version")

    check = sub.add_parser("check", help="validate a formpack dir or a pipeline.yaml as data")
    check.add_argument(
        "target",
        nargs="?",
        default=None,
        help="a formpack directory or a pipeline.yaml (default: ./pipeline.yaml)",
    )

    init = sub.add_parser("init", help="scaffold a new formpack (formpack.yaml + schema.json)")
    init.add_argument("dir", help="destination directory for the new formpack")
    init.add_argument(
        "--name", default=None, help="the new formpack's name (default: the dir's name)"
    )

    synth = sub.add_parser("synth", help="(re)generate the synthetic golden corpus for a formpack")
    synth.add_argument("formpack", help="a formpack dir, or a bare name under formpacks/")
    synth.add_argument("--seed", type=int, default=None, help="base seed override")
    synth.add_argument("--count", type=int, default=None, help="doc count override")
    synth.add_argument("--dpi", type=int, default=None, help="raster DPI override")
    synth.add_argument(
        "--expected-only",
        action="store_true",
        help="write only the expected JSONs (no rasteriser / poppler needed)",
    )

    fetch = sub.add_parser("fetch-forms", help="fetch the blank public form, pinned by URL+sha256")
    fetch.add_argument("formpack", help="a formpack dir, or a bare name under formpacks/")
    fetch.add_argument("--url", default=None, help="override the formpack's source_form.url")
    fetch.add_argument(
        "--dest", default="build/forms", help="download dir (gitignored; default: build/forms)"
    )
    fetch.add_argument(
        "--no-licence-check", dest="check_licence", action="store_false", help="skip licence probe"
    )

    run = sub.add_parser("run", help="run the pipeline on one scan (real served models)")
    run.add_argument("scan", help="path to a scanned PDF")
    run.add_argument("--formpack", default="uk-ch2", help="formpack dir or name (default: uk-ch2)")
    run.add_argument("--workdir", default="work", help="pipeline workdir (default: work)")
    run.add_argument("--out", default=None, help="where to write the report + extraction JSON")
    run.add_argument(
        "--region-model", default=None,
        help="override the per-region VLM (e.g. when the pinned VLM is unavailable on the fleet)",
    )
    run.add_argument("--pipeline", default="pipeline.yaml", help="pipeline config path")
    run.add_argument(
        "--emit", action="store_true",
        help="atomically emit the report + sidecar into the configured agent inbox",
    )
    run.add_argument(
        "--force", action="store_true",
        help="re-emit a byte-identical report for an already-emitted scan (implies --emit; the "
             "ledger-idempotency receipt). Never overwrites a report that differs in bytes.",
    )
    run.add_argument(
        "--inbox", default=None,
        help="override the agent inbox to emit into (default: pipeline emit.agent_inbox)",
    )

    gate = sub.add_parser("gate", help="score every golden scan end-to-end; freeze the baseline")
    gate.add_argument("formpack", help="a formpack dir, or a bare name under formpacks/")
    gate.add_argument("--workdir", default="work/gate", help="gate workdir (default: work/gate)")
    gate.add_argument(
        "--region-model", default=None, help="override the per-region VLM (fleet fallback)"
    )
    gate.add_argument(
        "--no-baseline", dest="freeze", action="store_false", help="do not freeze the baseline"
    )
    gate.add_argument("--pipeline", default="pipeline.yaml", help="pipeline config path")

    watch = sub.add_parser(
        "watch", help="watch a drop folder for scans; emit reports into a deckhand agent inbox"
    )
    watch.add_argument("--formpack", default="uk-ch2", help="formpack dir/name (default: uk-ch2)")
    watch.add_argument("--drop", default=None, help="drop dir to watch (default: watch.drop_dir)")
    watch.add_argument(
        "--inbox", default=None, help="agent inbox to emit into (default: emit.agent_inbox)"
    )
    watch.add_argument("--workdir", default="work", help="pipeline workdir (default: work)")
    watch.add_argument(
        "--region-model", default=None, help="override the per-region VLM (fleet fallback)"
    )
    watch.add_argument("--pipeline", default="pipeline.yaml", help="pipeline config path")
    watch.add_argument(
        "--once", action="store_true", help="process the drop folder once and exit (no polling)"
    )

    args = parser.parse_args(argv)
    if args.command == "version":
        return _cmd_version()
    if args.command == "check":
        return _cmd_check(args.target)
    if args.command == "init":
        return _cmd_init(args.dir, args.name)
    if args.command == "synth":
        return _cmd_synth(
            args.formpack,
            seed=args.seed,
            count=args.count,
            dpi=args.dpi,
            expected_only=args.expected_only,
        )
    if args.command == "fetch-forms":
        return _cmd_fetch_forms(
            args.formpack, url=args.url, dest=args.dest, check_licence=args.check_licence
        )
    if args.command == "run":
        return _cmd_run(
            args.scan, args.formpack, workdir=args.workdir, out=args.out,
            region_model=args.region_model, pipeline_path=args.pipeline,
            emit=args.emit, force=args.force, inbox=args.inbox,
        )
    if args.command == "gate":
        return _cmd_gate(
            args.formpack, workdir=args.workdir, region_model=args.region_model,
            freeze=args.freeze, pipeline_path=args.pipeline,
        )
    if args.command == "watch":
        return _cmd_watch(
            args.formpack, drop=args.drop, inbox=args.inbox, workdir=args.workdir,
            region_model=args.region_model, pipeline_path=args.pipeline, once=args.once,
        )
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
