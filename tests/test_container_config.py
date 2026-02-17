"""Container configuration regression tests."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_runtime_stage_does_not_install_curl() -> None:
    dockerfile = _read("Dockerfile")
    marker = "# ---- Runtime stage: shared base ----"
    assert marker in dockerfile

    runtime_stage = dockerfile.split(marker, 1)[1]
    assert "curl" not in runtime_stage


def test_dockerfile_healthcheck_uses_python_probe() -> None:
    dockerfile = _read("Dockerfile")
    assert "HEALTHCHECK" in dockerfile
    assert 'CMD python -c "import sys,urllib.request;' in dockerfile
    assert "http://localhost:8000/health" in dockerfile


def test_dockerfile_has_core_and_extract_targets() -> None:
    dockerfile = _read("Dockerfile")
    extract_idx = dockerfile.rfind("FROM runtime-base AS extract")
    core_idx = dockerfile.rfind("FROM runtime-base AS core")
    assert extract_idx > 0
    assert core_idx > extract_idx


def test_dockerfile_uses_first_run_model_cache_defaults() -> None:
    dockerfile = _read("Dockerfile")
    assert "ARG PRELOAD_MODEL=false" in dockerfile
    assert "ENV MODEL_CACHE_DIR=/data/model-cache" in dockerfile
    assert "PRELOADED_MODEL_CACHE_DIR=/opt/model-cache" in dockerfile
    assert "COPY --from=builder-core /opt/model-cache /opt/model-cache" in dockerfile
    assert "COPY --from=builder-extract /opt/model-cache /opt/model-cache" in dockerfile
    assert "/root/.cache/huggingface" not in dockerfile


def test_dockerfile_copies_web_ui_assets() -> None:
    dockerfile = _read("Dockerfile")
    assert "COPY webui ./webui" in dockerfile


def test_compose_healthchecks_use_python_probe() -> None:
    for compose_file in ("docker-compose.yml", "docker-compose.snippet.yml"):
        contents = _read(compose_file)
        assert "healthcheck:" in contents
        assert 'test: ["CMD", "python", "-c", "import sys,urllib.request;' in contents
        assert '"curl"' not in contents


def test_compose_defaults_to_core_target() -> None:
    for compose_file in ("docker-compose.yml", "docker-compose.snippet.yml"):
        contents = _read(compose_file)
        assert "target: ${MEMORIES_IMAGE_TARGET:-core}" in contents
        assert "image: memories:${MEMORIES_IMAGE_TARGET:-core}" in contents
        assert "PRELOAD_MODEL: ${PRELOAD_MODEL:-false}" in contents


def test_compose_sets_memory_and_allocator_guardrails() -> None:
    for compose_file in ("docker-compose.yml", "docker-compose.snippet.yml"):
        contents = _read(compose_file)
        assert "mem_limit: ${MEMORIES_MEM_LIMIT:-3g}" in contents
        assert "MALLOC_ARENA_MAX=${MALLOC_ARENA_MAX:-2}" in contents
        assert "MALLOC_TRIM_THRESHOLD_=${MALLOC_TRIM_THRESHOLD_:-131072}" in contents
        assert "MALLOC_MMAP_THRESHOLD_=${MALLOC_MMAP_THRESHOLD_:-131072}" in contents


def test_compose_supports_extraction_env_passthrough() -> None:
    for compose_file in ("docker-compose.yml", "docker-compose.snippet.yml"):
        contents = _read(compose_file)
        assert "EXTRACT_PROVIDER=${EXTRACT_PROVIDER:-}" in contents
        assert "EXTRACT_MODEL=${EXTRACT_MODEL:-}" in contents
        assert "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}" in contents
        assert "OPENAI_API_KEY=${OPENAI_API_KEY:-}" in contents
        assert "OLLAMA_URL=${OLLAMA_URL:-}" in contents
