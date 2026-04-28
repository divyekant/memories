"""Shadow-mode LLM execution.

Fans out the same extraction/AUDN prompts to candidate LLM providers in
parallel with the primary model, dumping per-model outputs to JSONL logs
for offline A/B comparison. Fire-and-forget — never blocks or raises into
the primary path.

Configured via SHADOW_PROVIDERS env (comma-list, "provider[:model]"):
  SHADOW_PROVIDERS="omlx:fplv2,omlx:gemma26b-3bit"

Logs are written to /tmp/memories-shadow-<model>.log (one line per call)
unless SHADOW_LOG_DIR overrides the directory.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from llm_provider import LLMProvider, OllamaProvider, OMLXProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShadowProviderConfig:
    provider_name: str
    model: str | None


def parse_shadow_providers(env_value: str) -> list[ShadowProviderConfig]:
    """Parse a SHADOW_PROVIDERS env value into a list of provider configs.

    Format: "provider[:model],provider[:model],..."
    """
    if not env_value:
        return []
    configs: list[ShadowProviderConfig] = []
    for raw in env_value.split(","):
        entry = raw.strip()
        if not entry:
            continue
        if ":" in entry:
            provider, model = entry.split(":", 1)
            configs.append(
                ShadowProviderConfig(
                    provider_name=provider.strip(),
                    model=model.strip() or None,
                )
            )
        else:
            configs.append(ShadowProviderConfig(provider_name=entry, model=None))
    return configs


def _build_one(cfg: ShadowProviderConfig) -> LLMProvider:
    """Construct a single shadow provider. Raises ValueError for unsupported types."""
    if cfg.provider_name == "omlx":
        base_url = os.environ.get("SHADOW_OMLX_URL", "").strip() or None
        api_key = os.environ.get("SHADOW_OMLX_API_KEY", "").strip() or None
        return OMLXProvider(base_url=base_url, api_key=api_key, model=cfg.model)
    if cfg.provider_name == "ollama":
        base_url = (
            os.environ.get("SHADOW_OLLAMA_URL", "").strip()
            or os.environ.get("OLLAMA_URL", "").strip()
            or None
        )
        return OllamaProvider(base_url=base_url, model=cfg.model)
    raise ValueError(
        f"Shadow provider '{cfg.provider_name}' not supported in v1 "
        "(supported: omlx, ollama)"
    )


# Per-file locks so concurrent writes to different model logs don't serialize
# against each other.
_log_locks: dict[str, threading.Lock] = {}
_log_locks_guard = threading.Lock()


def _get_log_lock(path: str) -> threading.Lock:
    with _log_locks_guard:
        if path not in _log_locks:
            _log_locks[path] = threading.Lock()
        return _log_locks[path]


_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_model_name(model: str) -> str:
    return _FILENAME_SAFE.sub("_", model)


def write_shadow_log(log_dir: str, model: str, record: dict) -> None:
    """Append a single JSONL line for `model` to <log_dir>/memories-shadow-<model>.log.

    Thread-safe: writes are serialized per-file by an internal lock so concurrent
    writers from different shadow threads cannot produce torn lines.
    """
    safe_model = _sanitize_model_name(model)
    path = str(Path(log_dir) / f"memories-shadow-{safe_model}.log")
    line = json.dumps(record, default=str) + "\n"
    lock = _get_log_lock(path)
    with lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)


def build_shadow_providers() -> list[LLMProvider]:
    """Build all shadow providers from SHADOW_PROVIDERS env. Skips failures."""
    env = os.environ.get("SHADOW_PROVIDERS", "").strip()
    configs = parse_shadow_providers(env)
    providers: list[LLMProvider] = []
    for cfg in configs:
        try:
            providers.append(_build_one(cfg))
        except Exception as e:
            logger.warning(
                "Failed to build shadow provider %s:%s — %s",
                cfg.provider_name, cfg.model, e,
            )
    return providers
