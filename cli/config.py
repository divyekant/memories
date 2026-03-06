"""Layered config resolution: flags > config file > env vars > defaults."""

import json
import os
from pathlib import Path

DEFAULT_URL = "http://localhost:8900"
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "memories"


def resolve_config(config_dir=None, flag_url=None, flag_api_key=None) -> dict:
    """Resolve configuration with layered precedence.

    Priority: flags > config file > env vars > defaults.
    Returns dict with url, api_key, and _sources attribution.
    """
    config_dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    config_file = config_dir / "config.json"

    # Start with defaults
    url = DEFAULT_URL
    api_key = None
    sources = {"url": "default", "api_key": "default"}

    # Layer: env vars
    env_url = os.environ.get("MEMORIES_URL")
    env_key = os.environ.get("MEMORIES_API_KEY")
    if env_url:
        url = env_url
        sources["url"] = "env"
    if env_key:
        api_key = env_key
        sources["api_key"] = "env"

    # Layer: config file
    if config_file.exists():
        try:
            with open(config_file) as f:
                file_cfg = json.load(f)
            if "url" in file_cfg:
                url = file_cfg["url"]
                sources["url"] = "file"
            if "api_key" in file_cfg:
                api_key = file_cfg["api_key"]
                sources["api_key"] = "file"
        except (json.JSONDecodeError, OSError):
            pass

    # Layer: flags (highest priority)
    if flag_url is not None:
        url = flag_url
        sources["url"] = "flag"
    if flag_api_key is not None:
        api_key = flag_api_key
        sources["api_key"] = "flag"

    return {"url": url, "api_key": api_key, "_sources": sources}


def write_config(config_dir=None, **kwargs) -> Path:
    """Write configuration to config.json."""
    config_dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"

    existing = {}
    if config_file.exists():
        try:
            with open(config_file) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    existing.update(kwargs)
    with open(config_file, "w") as f:
        json.dump(existing, f, indent=2)

    return config_file
