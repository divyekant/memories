# ---- Build stage: core deps + model cache ----
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder-core

ARG ENABLE_CLOUD_SYNC=false
ARG PRELOAD_MODEL=false

WORKDIR /app

COPY pyproject.toml uv.lock ./

# Install core deps; optionally add cloud extra
RUN uv sync --frozen --no-install-project --no-dev \
    $( [ "$ENABLE_CLOUD_SYNC" = "true" ] && echo "--extra cloud" || true )

# Copy embedder so we can optionally pre-download the ONNX model
COPY onnx_embedder.py .

# Optional: bake model files into image cache. Default keeps image smaller.
RUN mkdir -p /opt/model-cache && \
    if [ "$PRELOAD_MODEL" = "true" ]; then \
        .venv/bin/python -c "from onnx_embedder import OnnxEmbedder; OnnxEmbedder('all-MiniLM-L6-v2', cache_dir='/opt/model-cache')"; \
    fi

# Strip test/doc bloat from installed packages
RUN find /app/.venv/lib/python3.11/site-packages \
    \( -type d -name "tests" -o -name "test" -o -name "__pycache__" \) \
    -exec rm -rf {} + 2>/dev/null; \
    find /app/.venv/lib/python3.11/site-packages -name "*.pyc" -delete 2>/dev/null; \
    true

# ---- Build stage: extraction deps ----
FROM builder-core AS builder-extract

RUN uv sync --frozen --no-install-project --no-dev --extra extract

# Strip test/doc bloat from extraction SDK installs
RUN find /app/.venv/lib/python3.11/site-packages \
    \( -type d -name "tests" -o -name "test" -o -name "__pycache__" \) \
    -exec rm -rf {} + 2>/dev/null; \
    find /app/.venv/lib/python3.11/site-packages -name "*.pyc" -delete 2>/dev/null; \
    true

# ---- Runtime stage: shared base ----
FROM python:3.11-slim AS runtime-base

WORKDIR /app

ENV MODEL_CACHE_DIR=/data/model-cache \
    PRELOADED_MODEL_CACHE_DIR=/opt/model-cache \
    PATH="/app/.venv/bin:$PATH"

# Copy application code
COPY onnx_embedder.py .
COPY openai_embedder.py .
COPY embedder_reloader.py .
COPY memory_engine.py .
COPY entity_locks.py .
COPY qdrant_config.py .
COPY qdrant_store.py .
COPY cloud_sync.py .
COPY runtime_memory.py .
COPY chatgpt_oauth.py .
COPY memories_auth.py .
COPY __main__.py .
COPY llm_provider.py .
COPY llm_extract.py .
COPY app.py .
COPY webui ./webui

# Create non-root user for runtime
RUN useradd -r -m -s /bin/false memories && \
    mkdir -p /data/backups /data/model-cache /opt/model-cache && \
    chown -R memories:memories /data /opt/model-cache /app

USER memories

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD python -c "import sys,urllib.request; sys.exit(0 if 200 <= urllib.request.urlopen('http://localhost:8000/health', timeout=3).getcode() < 400 else 1)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

# ---- Runtime target: extract (includes Anthropic/OpenAI SDKs) ----
FROM runtime-base AS extract

COPY --from=builder-extract /app/.venv /app/.venv
COPY --from=builder-extract /opt/model-cache /opt/model-cache

# ---- Runtime target: core (default, no extraction SDKs) ----
FROM runtime-base AS core

COPY --from=builder-core /app/.venv /app/.venv
COPY --from=builder-core /opt/model-cache /opt/model-cache
