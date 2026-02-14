# ---- Build stage: install deps + download model ----
FROM python:3.11-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy embedder so we can pre-download the ONNX model
COPY onnx_embedder.py .

# Pre-download the ONNX model + tokenizer so first startup is fast
RUN python -c "from onnx_embedder import OnnxEmbedder; OnnxEmbedder('all-MiniLM-L6-v2')"

# Strip test/doc bloat from installed packages
RUN find /usr/local/lib/python3.11/site-packages \
    \( -type d -name "tests" -o -name "test" -o -name "__pycache__" \) \
    -exec rm -rf {} + 2>/dev/null; \
    find /usr/local/lib/python3.11/site-packages -name "*.pyc" -delete 2>/dev/null; \
    true

# ---- Runtime stage: copy only what we need ----
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy cached HuggingFace model
COPY --from=builder /root/.cache/huggingface /root/.cache/huggingface

# Copy application code
COPY onnx_embedder.py .
COPY memory_engine.py .
COPY app.py .

RUN mkdir -p /data/backups

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
