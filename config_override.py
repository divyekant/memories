"""Optional YAML config override support.

When CONFIG_DIR env var is set and YAML files exist in that directory,
loads prompts and search config from YAML instead of using hardcoded defaults.
When files don't exist, returns None so callers fall back to defaults.
"""
import logging
import os
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


def _load_yaml(config_dir: Optional[str], filename: str) -> Optional[dict]:
    """Load a YAML file from config_dir, returning None on any failure."""
    if config_dir is None:
        config_dir = os.environ.get("CONFIG_DIR")
    if not config_dir:
        return None

    path = Path(config_dir) / filename
    if not path.is_file():
        return None

    try:
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict):
            logger.warning("Config %s is not a dict, ignoring", path)
            return None
        return data
    except Exception as e:
        logger.warning("Failed to load config %s: %s", path, e)
        return None


def load_extraction_config(config_dir: Optional[str] = None) -> Optional[dict]:
    """Load extraction.yaml config.

    Keys: fact_extraction_prompt, fact_extraction_prompt_aggressive,
          categories, max_facts
    """
    return _load_yaml(config_dir, "extraction.yaml")


def load_audn_config(config_dir: Optional[str] = None) -> Optional[dict]:
    """Load audn.yaml config.

    Keys: audn_prompt, actions, similar_per_fact
    """
    return _load_yaml(config_dir, "audn.yaml")


def load_search_config(config_dir: Optional[str] = None) -> Optional[dict]:
    """Load search.yaml config.

    Keys: vector_weight, rrf_k, default_k, oversample_multiplier,
          novelty_threshold
    """
    return _load_yaml(config_dir, "search.yaml")
