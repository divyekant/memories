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

from dataclasses import dataclass


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
