"""``papereyes fetch-forms`` — fetch a blank public form, pinned by URL + sha256 (design spec §6).

Fetch-at-build is the standard: no blank form is ever vendored into the repository (a public
-form licence may exclude departmental crests / logos / the Royal Arms). Only the URL and the
sha256 pin live in the formpack; this command downloads the blank into a **gitignored** build
dir, verifies its sha256 against the pin (or reports the sha to pin on a first fetch), and probes
its extracted text for the declared licence.

The default synthetic corpus is rendered from scratch (``synth.mode: render``) and needs no
fetched blank, so this command is optional for the shipped goldens — it exists for the
higher-fidelity ``overlay`` mode and for keeping the pin honest.
"""

from __future__ import annotations

import hashlib
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from papereyes.config.models import Formpack
from papereyes.errors import FetchError
from papereyes.sandbox import (
    PDFTOTEXT_ADDRESS_SPACE_BYTES,
    PDFTOTEXT_CPU_SECONDS,
    PDFTOTEXT_MAX_OUTPUT_BYTES,
    PDFTOTEXT_TIMEOUT_S,
    decode_capped,
    run_bounded,
)


def sha256_hex(data: bytes) -> str:
    """The lowercase hex sha256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def verify_sha(data: bytes, pinned: str | None) -> tuple[str, bool]:
    """Return ``(computed_sha, matches_pin)``. When ``pinned`` is None the pin is 'unset'.

    Raises :class:`~papereyes.errors.FetchError` when a pin is present and does not match.
    """
    computed = sha256_hex(data)
    if pinned is None:
        return computed, False
    if computed.lower() != pinned.lower():
        raise FetchError(
            f"sha256 mismatch: pinned {pinned}, downloaded {computed} — the upstream blank "
            f"changed (or the pin is wrong). Re-pin deliberately, never silently."
        )
    return computed, True


def extract_text(pdf_path: str | Path) -> str:
    """Extract text from a PDF via poppler ``pdftotext``; '' if the tool is absent.

    poppler runs at arm's length through the bounded-resource wrapper (rlimits + wall-clock
    timeout, fixed argv, never a shell — see :mod:`papereyes.sandbox`). The probe is tolerant of
    a non-zero exit (``check=False`` — a licence probe on a partial read is fine), but a
    wall-clock timeout on a hung parser is still loud; the stdout pipe is capped after read.
    """
    exe = shutil.which("pdftotext")
    if exe is None:
        return ""
    proc = run_bounded(
        [exe, str(pdf_path), "-"],
        error_cls=FetchError,
        what="pdftotext",
        timeout_s=PDFTOTEXT_TIMEOUT_S,
        cpu_seconds=PDFTOTEXT_CPU_SECONDS,
        address_space_bytes=PDFTOTEXT_ADDRESS_SPACE_BYTES,
        fsize_bytes=PDFTOTEXT_MAX_OUTPUT_BYTES,
        check=False,
    )
    return decode_capped(proc.stdout, PDFTOTEXT_MAX_OUTPUT_BYTES)


def licence_probe_from(licence: str) -> str:
    """Derive a lowercase probe substring from a formpack's declared licence string."""
    lowered = licence.lower()
    if "open government licence" in lowered:
        return "open government licence"
    # Fall back to the first few words of the declared licence.
    return " ".join(lowered.split()[:3])


def verify_licence(text: str, probe: str) -> bool:
    """Whether ``probe`` appears (case-insensitively) in the extracted ``text``."""
    return probe.lower() in text.lower()


def download(url: str, *, timeout: float = 30.0) -> bytes:
    """Download ``url`` and return its bytes. Raises :class:`FetchError` on any transport error."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data: bytes = resp.read()
    except (OSError, ValueError) as exc:
        raise FetchError(f"could not fetch {url}: {exc}") from exc
    return data


@dataclass(frozen=True)
class FetchResult:
    """The outcome of a blank-form fetch."""

    url: str
    dest: Path
    sha256: str
    pin_matched: bool
    licence_ok: bool


def fetch_form(
    formpack: Formpack,
    dest_dir: str | Path,
    *,
    url: str | None = None,
    check_licence: bool = True,
    timeout: float = 30.0,
) -> FetchResult:
    """Fetch the blank for ``formpack`` into ``dest_dir`` (created; gitignored); verify + probe.

    ``url`` overrides ``formpack.source_form.url`` (e.g. the direct PDF asset behind a landing
    page). Raises :class:`FetchError` on a transport error, a sha256 pin mismatch, or (when
    ``check_licence``) a missing licence probe.
    """
    target_url = url or formpack.source_form.url
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{formpack.formpack}.blank.pdf"

    data = download(target_url, timeout=timeout)
    out.write_bytes(data)
    computed, matched = verify_sha(data, formpack.source_form.sha256)

    licence_ok = True
    if check_licence:
        probe = licence_probe_from(formpack.source_form.licence)
        licence_ok = verify_licence(extract_text(out), probe)
        if not licence_ok:
            raise FetchError(
                f"licence probe {probe!r} not found in the fetched blank — refusing to treat "
                f"an unverified document as {formpack.source_form.licence!r}."
            )
    return FetchResult(
        url=target_url, dest=out, sha256=computed, pin_matched=matched, licence_ok=licence_ok
    )
