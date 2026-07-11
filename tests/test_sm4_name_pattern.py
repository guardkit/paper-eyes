"""SM-4: the emitted report name must pass deckhand's shipped workflows.yaml ``name_pattern``.

The composed end-to-end demo ferries paper-eyes' report through deckhand's relay, whose contract
constrains the crossing artifact's filename (master plan §2.2 SM-4). This test asserts paper-eyes'
emitted name ``<stem>--<scanSha8>--fp<ver>.txt`` passes that pattern — read from the in-repo copy at
``demo/workflows.yaml`` — for the real pipeline output AND for every synth persona stem. A
drift-guard checks the in-repo copy against a sibling deckhand checkout when present (else skipped).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from papereyes.config.loader import load_formpack, load_pipeline
from papereyes.pipeline.run import run_pipeline
from papereyes.synth.generator import synth_corpus
from tests.pipeline_support import StubModelClient
from tests.support import REPO_ROOT

WORKFLOWS = REPO_ROOT / "demo" / "workflows.yaml"
UK_CH2 = REPO_ROOT / "formpacks" / "uk-ch2"
DECKHAND_SHIPPED = REPO_ROOT.parent / "deckhand" / "examples" / "relay-demo" / "workflows.yaml"


def _name_pattern() -> re.Pattern[str]:
    text = WORKFLOWS.read_text(encoding="utf-8")
    m = re.search(r'name_pattern:\s*"([^"]+)"', text)
    assert m is not None, "no name_pattern in demo/workflows.yaml"
    return re.compile(m.group(1))


def test_synth_persona_report_names_pass_the_contract() -> None:
    pat = _name_pattern()
    fp = load_formpack(UK_CH2)
    # Every committed synth persona stem, formatted as an emitted report name, must pass the pattern
    # (and stay well under the 121-char ceiling — synth stems are kept short, SM-4).
    for i in range(1, 7):
        name = f"persona-{i:02d}--deadbeef--fp{fp.version}.txt"
        assert pat.match(name), f"{name!r} does not pass the SM-4 name_pattern"
        assert len(name) <= 121, f"{name!r} exceeds the contract's 121-char ceiling"


@pytest.mark.skipif(
    shutil.which("pdftoppm") is None, reason="pdftoppm (poppler-utils) not installed"
)
def test_real_emitted_name_passes_the_contract(tmp_path: Path) -> None:
    pat = _name_pattern()
    golden = tmp_path / "golden"
    synth_corpus(golden, base_seed=7, count=1, dpi=200)
    fp = load_formpack(UK_CH2)
    pipeline_cfg = load_pipeline(REPO_ROOT / "pipeline.yaml")
    result = run_pipeline(
        golden / "persona-01.pdf", fp, UK_CH2, pipeline_cfg, StubModelClient(),
        workdir=tmp_path / "work",
    )
    emitted = f"{result.report_name}.txt"
    assert pat.match(emitted), f"the real emitted name {emitted!r} does not pass the SM-4 pattern"


def test_demo_workflows_matches_shipped_deckhand_copy() -> None:
    """Drift-guard: the in-repo contract copy must equal deckhand's shipped one (when present)."""
    if not DECKHAND_SHIPPED.is_file():
        pytest.skip("no sibling deckhand checkout — drift-guard runs only where both repos exist")

    def _body(path: Path) -> str:
        lines = path.read_text(encoding="utf-8").splitlines()
        start = next(i for i, ln in enumerate(lines) if ln.startswith("schema_version:"))
        return "\n".join(lines[start:]).strip()

    assert _body(WORKFLOWS) == _body(DECKHAND_SHIPPED), (
        "demo/workflows.yaml has drifted from deckhand's shipped relay-demo/workflows.yaml — "
        "re-copy it so the SM-4 assertion checks the real shipped contract"
    )
