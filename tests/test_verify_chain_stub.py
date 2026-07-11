"""The stub-mode ``verify_chain.sh`` as a hermetic gate (master plan §2.1 trace, §4 half-day 4).

Drives ``demo/verify_chain.sh --stub`` end-to-end: the offline OpenAI stub serves paper-eyes'
pipeline (which emits a report into the shared ``doc-router/inbox``), and ``demo/chain_driver.py``
drives deckhand's OWN watch / resolve / relay / board code in-process with deterministic stub
proposers (no model) to verify the composed §2.1 crossing — move proposed, approved, handoff receipt
before the artifact, relay ``delivered``, consumer proposal at ask tier, exact ledger sequences,
both board pages, SM-4 name.

The deckhand half needs a sibling deckhand checkout with an importable venv. paper-eyes CI checks
out paper-eyes ONLY (deckhand is a data-only reference, never a dependency — the one-way law), so
this test SKIPS there and is exercised where both repos live (assembly box + main session). That
mirrors ``verify_e2e.sh --live`` staying out of CI: the composed drive is not a paper-eyes unit.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support import REPO_ROOT

SCRIPT = REPO_ROOT / "demo" / "verify_chain.sh"
DECKHAND_REPO = Path(os.environ.get("DECKHAND_REPO", str(REPO_ROOT.parent / "deckhand")))
DECKHAND_PYTHON = Path(
    os.environ.get("DECKHAND_PYTHON", str(DECKHAND_REPO / ".venv" / "bin" / "python"))
)

pytestmark = pytest.mark.skipif(
    shutil.which("pdftoppm") is None or shutil.which("bash") is None,
    reason="needs bash + pdftoppm (poppler-utils)",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _deckhand_available() -> bool:
    """A sibling deckhand checkout whose venv python can import deckhand (else: skip, not fail)."""
    if not (DECKHAND_REPO / "examples" / "relay-demo").is_dir() or not DECKHAND_PYTHON.exists():
        return False
    probe = subprocess.run(
        [str(DECKHAND_PYTHON), "-c", "import deckhand"], capture_output=True
    )
    return probe.returncode == 0


def test_verify_chain_stub_exits_zero() -> None:
    if not _deckhand_available():
        pytest.skip(
            "no importable sibling deckhand checkout (set DECKHAND_REPO / DECKHAND_PYTHON) — "
            "the composed chain drive runs where both repos live, like verify_e2e.sh --live"
        )
    papereyes = shutil.which("papereyes")
    if papereyes is None:
        pytest.skip("papereyes console script not on PATH")

    env = {
        "PATH": f"{Path(papereyes).parent}:{os.environ.get('PATH', '')}",
        "PAPEREYES": papereyes,
        "PYTHON": sys.executable,
        "STUB_PORT": str(_free_port()),
        "HOME": os.environ.get("HOME", "/tmp"),
        "DECKHAND_REPO": str(DECKHAND_REPO),
        "DECKHAND_PYTHON": str(DECKHAND_PYTHON),
    }
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--stub"],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"verify_chain.sh --stub failed:\n{combined}"
    assert "STUB chain PASS" in proc.stdout, f"missing PASS marker:\n{combined}"
