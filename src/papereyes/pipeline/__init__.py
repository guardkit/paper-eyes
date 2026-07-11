"""The Paper Eyes pipeline (design spec §3, §6 Stage 2): identify -> convert -> locate ->
re-read -> splice -> extract -> report + provenance. Every model call goes through the
injectable :class:`~papereyes.pipeline.client.ModelClient` seam so the pipeline runs hermetically
under test."""

from __future__ import annotations

from papereyes.pipeline.client import HttpModelClient, ModelClient, ModelResult
from papereyes.pipeline.run import RunResult, run_pipeline

__all__ = ["HttpModelClient", "ModelClient", "ModelResult", "RunResult", "run_pipeline"]
