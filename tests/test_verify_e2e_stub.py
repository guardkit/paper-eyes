"""The stub-mode ``verify_e2e.sh`` as a hermetic gate (design spec §6 Stage 4).

Drives ``demo/verify_e2e.sh --stub`` end-to-end: the deterministic OFFLINE OpenAI stub serves the
pipeline, and the REAL ``papereyes watch`` / ``papereyes run`` commands prove the standalone-lane
plumbing — drop -> report + sidecar in the paper-clerk inbox (SM-4 name) -> processed.jsonl
idempotency (re-drop = no second emit) -> ``run --force`` byte-identical re-emit. No docker, no GPU,
no real endpoint, no deckhand — so it runs in CI. (The deckhand-side of the e2e — daemon proposal,
resolve endpoint, ``.meta``, ledger idempotency — is the ``--live`` path the main session runs.)
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

SCRIPT = REPO_ROOT / "demo" / "verify_e2e.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("pdftoppm") is None or shutil.which("bash") is None,
    reason="needs bash + pdftoppm (poppler-utils)",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_verify_e2e_stub_exits_zero() -> None:
    papereyes = shutil.which("papereyes")
    if papereyes is None:
        pytest.skip("papereyes console script not on PATH")

    env = {
        "PATH": f"{Path(papereyes).parent}:{os.environ.get('PATH', '')}",
        "PAPEREYES": papereyes,
        "PYTHON": sys.executable,
        "STUB_PORT": str(_free_port()),
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--stub"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"verify_e2e.sh --stub failed:\n{combined}"
    assert "STUB e2e PASS" in proc.stdout, f"missing PASS marker:\n{combined}"
    assert "SM-4 name OK" in proc.stdout
