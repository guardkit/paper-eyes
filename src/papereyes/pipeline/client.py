"""The model-call seam (design spec §4.2, §6 Stage 2).

Every model call in the pipeline goes through a :class:`ModelClient`. The real client
(:class:`HttpModelClient`) posts to the one OpenAI-compatible endpoint; unit tests inject a
deterministic stub, so the whole pipeline runs hermetically with no endpoint and no GPU —
deckhand's own stub-injection discipline.

Three call kinds, three served models (pins in ``pipeline.yaml`` ``models:``):

- ``convert_page`` — the bulk document conversion ("Docling"): a page raster in, located-text
  DocTags out. Served by the Docling VLM (``granite-docling``).
- ``read_region`` — the per-region re-read: a cropped region raster + a prompt in, a plain
  transcription out. Served by the region VLM.
- ``extract_json`` — the deterministic text->JSON extraction, ``response_format`` = a JSON
  Schema, reasoning disabled, one re-parse retry handled by the caller. Served by the extractor.

**Decoding is pinned for EVERY call** from the global ``decoding:`` block (``temperature=0``,
fixed ``seed``) — a determinism receipt is only as good as the pins it proves. All calls are
issued strictly sequentially by the orchestrator; the determinism receipt additionally assumes
no other client shares the endpoint (the serving assumption recorded in ``pipeline.yaml``).

Swap patience: llama-swap loads/unloads models per request's ``model`` field, and a cold swap
can stall for minutes; an HTTP 500 during a swap is transient. The client therefore uses long
per-call timeouts and retries with a growing backoff rather than concluding failure — all three
pinned models were verified live on this endpoint (2026-07-11).
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from papereyes.config.models import Decoding, PipelineModels
from papereyes.errors import PaperEyesError


class ModelError(PaperEyesError):
    """A model call failed (transport error, HTTP error, or empty completion)."""


@dataclass(frozen=True)
class ModelResult:
    """One completed model call: the text, the model id actually used, and its latency."""

    text: str
    model: str
    latency_s: float


class ModelClient(Protocol):
    """The seam every pipeline model call goes through (real or stub)."""

    def convert_page(self, image_png: bytes, *, max_tokens: int) -> ModelResult: ...

    def read_region(self, prompt: str, image_png: bytes, *, max_tokens: int) -> ModelResult: ...

    def extract_json(
        self, system_prompt: str, user_text: str, json_schema: dict[str, Any], *, max_tokens: int
    ) -> ModelResult: ...


_ENV_TEMPLATE = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")


def resolve_base_url(base_url: str) -> str:
    """Resolve a ``${VAR:-default}`` template in a pipeline ``endpoint.base_url``."""

    def sub(m: re.Match[str]) -> str:
        return os.environ.get(m.group(1), m.group(2) or "")

    return _ENV_TEMPLATE.sub(sub, base_url).rstrip("/")


# The standard granite-docling conversion instruction (image -> located-text DocTags).
DOCLING_PROMPT = "Convert this page to docling."


class HttpModelClient:
    """The real client: sequential, pinned-decoding calls to the OpenAI-compatible endpoint.

    Reasoning is disabled on the extractor (``chat_template_kwargs.enable_thinking=false``) —
    the served extractor is a thinking model, and reasoning tokens would both blow the token
    budget and add non-determinism to the emitted receipt. All three call kinds send the global
    ``temperature``/``seed`` pins.
    """

    #: Backoff (seconds) between retries — a cold llama-swap load can stall for minutes, and an
    #: HTTP 500 mid-swap is transient; patience beats a false "model is broken" conclusion.
    RETRY_BACKOFF_S: tuple[float, ...] = (5.0, 15.0, 45.0, 90.0)

    def __init__(
        self,
        base_url: str,
        models: PipelineModels,
        decoding: Decoding,
        *,
        region_model: str | None = None,
        timeout_s: float = 900.0,
        retries: int = 4,
    ) -> None:
        self.base_url = resolve_base_url(base_url)
        self.models = models
        self.decoding = decoding
        # The region re-read model defaults to the PINNED `models.vlm`. An explicit substitute
        # (an escape hatch for a fleet where the pin is unavailable) is recorded per call and
        # surfaces as a deviation in provenance.json — never a silent swap.
        self.region_model = region_model or models.vlm
        self.timeout_s = timeout_s
        self.retries = retries

    # ── transport ────────────────────────────────────────────────────────────────────

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    parsed: dict[str, Any] = json.load(resp)
                    return parsed
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:300]
                last_exc = ModelError(
                    f"{body['model']}: HTTP {exc.code} {detail!r} (attempt {attempt + 1})"
                )
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_exc = ModelError(
                    f"{body['model']}: transport error {exc} (attempt {attempt + 1})"
                )
            if attempt < self.retries:
                backoff = self.RETRY_BACKOFF_S[min(attempt, len(self.RETRY_BACKOFF_S) - 1)]
                time.sleep(backoff)
        assert last_exc is not None
        raise last_exc

    def _chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        extra: dict[str, Any] | None = None,
    ) -> ModelResult:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self.decoding.temperature,
            "seed": self.decoding.seed,
        }
        if extra:
            body.update(extra)
        t0 = time.perf_counter()
        parsed = self._post(body)
        latency = time.perf_counter() - t0
        try:
            text = parsed["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError) as exc:
            raise ModelError(f"{model}: malformed completion {parsed!r}") from exc
        return ModelResult(text=text, model=model, latency_s=latency)

    # ── helpers ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _image_message(prompt: str, image_png: bytes) -> list[dict[str, Any]]:
        data_url = "data:image/png;base64," + base64.b64encode(image_png).decode("ascii")
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]

    # ── the three call kinds ───────────────────────────────────────────────────────────

    def convert_page(self, image_png: bytes, *, max_tokens: int) -> ModelResult:
        return self._chat(
            self.models.docling,
            self._image_message(DOCLING_PROMPT, image_png),
            max_tokens=max_tokens,
        )

    def read_region(self, prompt: str, image_png: bytes, *, max_tokens: int) -> ModelResult:
        return self._chat(
            self.region_model, self._image_message(prompt, image_png), max_tokens=max_tokens
        )

    def extract_json(
        self, system_prompt: str, user_text: str, json_schema: dict[str, Any], *, max_tokens: int
    ) -> ModelResult:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ]
        extra = {
            # Reasoning off: clean JSON, bounded tokens, deterministic bytes.
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "extraction", "schema": json_schema, "strict": True},
            },
        }
        return self._chat(self.models.extract, messages, max_tokens=max_tokens, extra=extra)
