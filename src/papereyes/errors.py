"""Paper Eyes' exception hierarchy.

Config faults are loud: rather than run a pipeline against a formpack it cannot
validate as data, Paper Eyes raises and refuses. Every error carries a legible message
naming the offending file/field.
"""

from __future__ import annotations


class PaperEyesError(Exception):
    """Base class for every Paper Eyes error."""


class ConfigError(PaperEyesError):
    """A formpack or pipeline config is malformed, or is not config-is-data safe.

    Raised when a YAML file is not ``safe_load``-able, is not a mapping, or fails its
    Pydantic schema (``extra='forbid'`` everywhere — an unknown key in a file meant to be
    audited by its owner is a smell, not a convenience).
    """


class SynthError(PaperEyesError):
    """The synthetic corpus generator could not produce a scan or its ground truth.

    Raised when a required system tool (``pdftoppm``) is absent, a render step fails, or a
    rasterised scan is not in fact image-only.
    """


class FetchError(PaperEyesError):
    """A blank-form fetch failed its integrity or licence check (Stage 1 ``fetch-forms``).

    Raised when the download fails, the sha256 does not match the formpack's pinned value,
    or the licence-page probe does not find the declared licence text. The fetched blank is
    never committed (OGL v3 excludes departmental crests/logos/Royal Arms) — only the URL
    and sha256 pin live in the repo.
    """
