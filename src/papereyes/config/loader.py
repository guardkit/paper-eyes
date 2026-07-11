"""Load formpack and pipeline configs as validated data (design spec §4).

YAML is parsed with ``safe_load`` only — no ``!!python/object`` construction, no binary
object deserialization, no code execution. The result is a validated
:class:`~papereyes.config.models.Formpack` / :class:`~papereyes.config.models.Pipeline`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from papereyes.config.models import Formpack, Pipeline
from papereyes.errors import ConfigError

FORMPACK_FILENAME = "formpack.yaml"


def _safe_load_mapping(path: str | Path, what: str) -> dict[str, Any]:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{what} not readable at {p}: {exc}") from exc
    try:
        # safe_load ONLY — the config-is-data invariant.
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{what} at {p} is not safe-loadable YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{what} at {p} must be a YAML mapping at the top level")
    return data


def load_formpack(path: str | Path) -> Formpack:
    """Read and validate the ``formpack.yaml`` at ``path`` (a file or its parent dir).

    Raises :class:`~papereyes.errors.ConfigError` if the file is not safe-loadable YAML,
    is not a mapping, or fails the :class:`Formpack` schema.
    """
    p = Path(path)
    if p.is_dir():
        p = p / FORMPACK_FILENAME
    data = _safe_load_mapping(p, "formpack")
    try:
        return Formpack.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"formpack at {p} failed validation:\n{exc}") from exc


def load_pipeline(path: str | Path) -> Pipeline:
    """Read and validate a ``pipeline.yaml`` at ``path``.

    Raises :class:`~papereyes.errors.ConfigError` on the same failure modes as
    :func:`load_formpack`.
    """
    p = Path(path)
    data = _safe_load_mapping(p, "pipeline")
    try:
        return Pipeline.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"pipeline at {p} failed validation:\n{exc}") from exc
