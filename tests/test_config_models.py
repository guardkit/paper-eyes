"""Formpack + pipeline model validation (design spec §4)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from papereyes.config.loader import load_formpack, load_pipeline
from papereyes.config.models import (
    Formpack,
    HeadingSpanLocator,
    PictureSignatureLocator,
)
from papereyes.errors import ConfigError
from tests.support import REPO_ROOT, VALID_FORMPACK


def test_valid_formpack_loads_and_slugs() -> None:
    fp = load_formpack(VALID_FORMPACK)
    assert fp.formpack == "demo-form"
    assert fp.version == 1
    assert fp.slug() == "demo-form@1"
    assert len(fp.regions) == 2


def test_locator_discriminated_union_resolves_by_kind() -> None:
    fp = load_formpack(VALID_FORMPACK)
    kinds = {r.id: type(r.locate) for r in fp.regions}
    assert kinds["id-grid"] is PictureSignatureLocator
    assert kinds["details"] is HeadingSpanLocator


def test_extra_key_is_forbidden() -> None:
    with pytest.raises(ValidationError):
        Formpack.model_validate({"formpack": "x", "bogus": 1})


def test_pipeline_loads_with_pinned_decoding() -> None:
    pipeline = load_pipeline(REPO_ROOT / "pipeline.yaml")
    assert pipeline.decoding.temperature == 0.0
    assert pipeline.decoding.seed == 42
    assert pipeline.models.vlm == "granite-vision-4-1-4b"
    assert pipeline.models.extract == "qwen36-workhorse"


def test_loader_rejects_non_mapping(tmp_path: Path) -> None:
    bad = tmp_path / "formpack.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_formpack(bad)
