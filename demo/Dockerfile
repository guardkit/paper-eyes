# syntax=docker/dockerfile:1
#
# Paper Eyes — the watch/emit daemon container (design spec §4.6, §6 Stage 4). One slim image that
# runs `papereyes watch` over a mounted drop/ and atomically emits reports into a mounted deckhand
# agent inbox/. Built with uv from the pinned lockfile so the image carries exactly the audited
# dependency set — nothing floats.
#
# poppler-utils (pdftoppm/pdftotext) is the ONLY system package: the pipeline rasterises each scan
# page with pdftoppm. There is NO local Docling/Tesseract stack in the image — the bulk conversion
# and the region re-reads are served by the configured OpenAI-compatible model endpoint (recorded in
# every provenance sidecar). Build context is the repo root (see demo/docker-compose.yml).

# --- build stage: resolve + install the locked deps and the project into a venv ---------------
# Base + tool images are pinned by digest, not by a floating tag, so a supply-chain swap upstream
# cannot land silently: the tag stays human-readable, the @sha256 is what actually resolves. Bump
# both deliberately (re-inspect the tag's digest) — never let them drift. (SECURITY.md / MA-37.)
FROM python:3.11-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba AS build

COPY --from=ghcr.io/astral-sh/uv:0.11@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Layer 1 — dependencies only (changes rarely): resolve from the frozen lock, no project, no dev.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Layer 2 — the project source, then install the project itself into the same venv.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# --- runtime stage: slim image, poppler installed, the venv + source + formpacks copied in -----
FROM python:3.11-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba AS runtime

# poppler-utils = pdftoppm (the raster step) + pdftotext. The ONLY system package the runtime needs.
# Pinned to an exact bookworm version so the parser toolchain cannot be swapped underneath the build
# without a visible diff. This version is the candidate in the pinned base above; when Debian ships a
# new deb12uN security update, bump it deliberately (never float to whatever the mirror serves).
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils=22.12.0-2+deb12u2 \
    && rm -rf /var/lib/apt/lists/*

# A non-root default user. Compose overrides `user:` to the host uid so bind-mounted drop/ / work/ /
# agents/ are owned correctly; either way the process never needs root.
RUN useradd --create-home --uid 10001 papereyes

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src
# The formpack data + the pipeline config the daemon reads (drop/emit/watch dirs, model pins).
COPY formpacks /app/formpacks
COPY pipeline.yaml /app/pipeline.yaml

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    USER=papereyes

USER papereyes

# The mounted trees: /drop (scans arrive), /agents (the shared deckhand agents root — reports land in
# /agents/<agent>/inbox), /work (crops + provenance + processed.jsonl).
VOLUME ["/drop", "/agents", "/work"]

# The daemon: watch /drop, emit into the inbox named by pipeline.yaml emit.agent_inbox. The endpoint
# is a URL (PAPEREYES_OPENAI_BASE_URL); compose points it at the host's model server or the stub.
ENTRYPOINT ["papereyes", "watch", "--workdir", "/work"]
