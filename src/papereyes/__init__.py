"""Paper Eyes — a local-first scanned-document intake pipeline for deckhand.

Turns an image-only PDF of a known public form into deterministic structured JSON and
drops a human-legible extraction report into a deckhand agent's ``inbox/``. Per-form-type
calibration (region locators, prompts, extraction schema, golden docs) is pure YAML data
called a *formpack*; adding a form family is a data change, never a code change.

This package imports **zero deckhand code** — the integration seam is files on disk
(the one-way dependency law).
"""

from __future__ import annotations

__version__ = "0.1.0"
