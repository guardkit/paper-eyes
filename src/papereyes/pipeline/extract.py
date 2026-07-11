"""Deterministic text->JSON extraction (design spec §3 step 5, §4.1 extraction, §6 Stage 2).

One extractor call: the formpack's ``system_prompt`` + the spliced markdown, with
``response_format`` set to the formpack's JSON Schema, pinned decoding, reasoning disabled. The
served extractor returns clean JSON; ``<md>``/code-fence wrappers are stripped defensively and a
single salvage re-parse recovers a JSON object embedded in stray prose before the call is
considered failed.
"""

from __future__ import annotations

import json
import re
from typing import Any

from papereyes.errors import PaperEyesError
from papereyes.pipeline.client import ModelClient, ModelResult

# Meta keys some OpenAI-compatible servers reject inside a json_schema `schema`.
_SCHEMA_META = ("$schema", "$id", "title", "description")

_FENCE_OPEN = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n?")
_FENCE_CLOSE = re.compile(r"\n?```\s*$")
_MD_WRAP = re.compile(r"</?md>", re.IGNORECASE)
_FIRST_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class ExtractionError(PaperEyesError):
    """The extractor returned text that could not be parsed as a JSON object."""


def sanitize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop top-level meta keys so the schema is accepted as an OpenAI ``json_schema``."""
    return {k: v for k, v in schema.items() if k not in _SCHEMA_META}


def strip_wrapper(text: str) -> str:
    """Remove ``<md>`` tags and a single markdown code fence around a JSON body."""
    body = _MD_WRAP.sub("", text).strip()
    body = _FENCE_OPEN.sub("", body)
    body = _FENCE_CLOSE.sub("", body)
    return body.strip()


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from ``text``; one salvage re-parse before giving up."""
    body = strip_wrapper(text)
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        m = _FIRST_OBJECT.search(body)
        if m is None:
            raise ExtractionError(f"no JSON object in extractor output: {text[:200]!r}") from None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"unparseable extractor output: {text[:200]!r}") from exc
    if not isinstance(obj, dict):
        raise ExtractionError(f"extractor output was not a JSON object: {text[:200]!r}")
    return obj


def extract_fields(
    client: ModelClient,
    *,
    system_prompt: str,
    markdown: str,
    schema: dict[str, Any],
    max_tokens: int,
) -> tuple[dict[str, Any], ModelResult]:
    """Run the extraction call and return ``(parsed_json, model_result)``."""
    result = client.extract_json(
        system_prompt, markdown, sanitize_schema(schema), max_tokens=max_tokens
    )
    return parse_json_object(result.text), result
