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

import logging
import os
from dataclasses import dataclass

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
