"""`fetch-forms` integrity + licence helpers (design spec §6 Stage 1) — no network.

The download itself is a build-time network action, exercised by hand; here we prove the
integrity logic: sha256 pinning fails loudly on a mismatch, and the licence probe is derived
and checked correctly.
"""

from __future__ import annotations

import pytest

from papereyes.errors import FetchError
from papereyes.fetch.forms import (
    licence_probe_from,
    sha256_hex,
    verify_licence,
    verify_sha,
)


def test_sha256_hex_is_stable() -> None:
    assert sha256_hex(b"hello") == sha256_hex(b"hello")
    assert sha256_hex(b"hello") != sha256_hex(b"world")


def test_verify_sha_unset_pin_reports_computed() -> None:
    computed, matched = verify_sha(b"data", None)
    assert computed == sha256_hex(b"data")
    assert matched is False


def test_verify_sha_matching_pin() -> None:
    data = b"the blank form bytes"
    computed, matched = verify_sha(data, sha256_hex(data))
    assert matched is True
    assert computed == sha256_hex(data)


def test_verify_sha_mismatch_fails_loudly() -> None:
    with pytest.raises(FetchError):
        verify_sha(b"data", "0" * 64)


def test_licence_probe_and_verify() -> None:
    probe = licence_probe_from("Open Government Licence v3 (Crown copyright)")
    assert probe == "open government licence"
    assert verify_licence("... released under the Open Government Licence v3 ...", probe)
    assert not verify_licence("no licence statement here", probe)
