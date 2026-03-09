"""Tests for config_override module."""
import os
import pytest
import yaml
from pathlib import Path


@pytest.fixture
def config_dir(tmp_path):
    """Temporary config directory."""
    return tmp_path


@pytest.fixture
def extraction_yaml(config_dir):
    """Create extraction.yaml in config dir."""
    data = {
        "fact_extraction_prompt": "Custom extraction prompt for {project}.",
        "fact_extraction_prompt_aggressive": "Custom aggressive prompt for {project}.",
        "categories": ["DECISION", "LEARNING", "DETAIL", "CUSTOM"],
        "max_facts": 50,
    }
    path = config_dir / "extraction.yaml"
    path.write_text(yaml.dump(data))
    return path


@pytest.fixture
def audn_yaml(config_dir):
    """Create audn.yaml in config dir."""
    data = {
        "audn_prompt": "Custom AUDN prompt.\n\n{facts_json}\n\n{similar_json}",
        "actions": ["ADD", "UPDATE", "DELETE", "NOOP", "MERGE"],
        "similar_per_fact": 10,
    }
    path = config_dir / "audn.yaml"
    path.write_text(yaml.dump(data))
    return path


@pytest.fixture
def search_yaml(config_dir):
    """Create search.yaml in config dir."""
    data = {
        "vector_weight": 0.8,
        "rrf_k": 45,
        "default_k": 10,
        "oversample_multiplier": 5,
        "novelty_threshold": 0.92,
    }
    path = config_dir / "search.yaml"
    path.write_text(yaml.dump(data))
    return path


class TestLoadExtractionConfig:
    """Test load_extraction_config."""

    def test_loads_valid_yaml(self, config_dir, extraction_yaml):
        from config_override import load_extraction_config

        cfg = load_extraction_config(str(config_dir))
        assert cfg is not None
        assert cfg["fact_extraction_prompt"] == "Custom extraction prompt for {project}."
        assert cfg["fact_extraction_prompt_aggressive"] == "Custom aggressive prompt for {project}."
        assert cfg["max_facts"] == 50
        assert "CUSTOM" in cfg["categories"]

    def test_returns_none_when_file_missing(self, config_dir):
        from config_override import load_extraction_config

        cfg = load_extraction_config(str(config_dir))
        assert cfg is None

    def test_returns_none_when_dir_missing(self):
        from config_override import load_extraction_config

        cfg = load_extraction_config("/nonexistent/path/that/does/not/exist")
        assert cfg is None


class TestLoadAudnConfig:
    """Test load_audn_config."""

    def test_loads_valid_yaml(self, config_dir, audn_yaml):
        from config_override import load_audn_config

        cfg = load_audn_config(str(config_dir))
        assert cfg is not None
        assert "Custom AUDN prompt" in cfg["audn_prompt"]
        assert cfg["similar_per_fact"] == 10
        assert "MERGE" in cfg["actions"]

    def test_returns_none_when_file_missing(self, config_dir):
        from config_override import load_audn_config

        cfg = load_audn_config(str(config_dir))
        assert cfg is None


class TestLoadSearchConfig:
    """Test load_search_config."""

    def test_loads_valid_yaml(self, config_dir, search_yaml):
        from config_override import load_search_config

        cfg = load_search_config(str(config_dir))
        assert cfg is not None
        assert cfg["vector_weight"] == 0.8
        assert cfg["rrf_k"] == 45
        assert cfg["default_k"] == 10
        assert cfg["oversample_multiplier"] == 5
        assert cfg["novelty_threshold"] == 0.92

    def test_returns_none_when_file_missing(self, config_dir):
        from config_override import load_search_config

        cfg = load_search_config(str(config_dir))
        assert cfg is None


class TestEnvVarIntegration:
    """Test that CONFIG_DIR env var is used when path not provided."""

    def test_loads_from_config_dir_env(self, config_dir, search_yaml, monkeypatch):
        monkeypatch.setenv("CONFIG_DIR", str(config_dir))
        from config_override import load_search_config

        cfg = load_search_config()
        assert cfg is not None
        assert cfg["rrf_k"] == 45

    def test_returns_none_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("CONFIG_DIR", raising=False)
        from config_override import load_search_config

        cfg = load_search_config()
        assert cfg is None

    def test_explicit_path_overrides_env(self, config_dir, search_yaml, monkeypatch, tmp_path):
        # Set env to a dir without the file
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        monkeypatch.setenv("CONFIG_DIR", str(other_dir))
        from config_override import load_search_config

        # Explicit path with the file should work
        cfg = load_search_config(str(config_dir))
        assert cfg is not None
        assert cfg["rrf_k"] == 45

        # Env path (without file) should return None
        cfg2 = load_search_config(str(other_dir))
        assert cfg2 is None


class TestMalformedYaml:
    """Test handling of malformed YAML files."""

    def test_returns_none_on_invalid_yaml(self, config_dir):
        bad_yaml = config_dir / "search.yaml"
        bad_yaml.write_text(": invalid: yaml: {{[")
        from config_override import load_search_config

        cfg = load_search_config(str(config_dir))
        assert cfg is None

    def test_returns_none_on_non_dict_yaml(self, config_dir):
        bad_yaml = config_dir / "extraction.yaml"
        bad_yaml.write_text("- just\n- a\n- list\n")
        from config_override import load_extraction_config

        cfg = load_extraction_config(str(config_dir))
        assert cfg is None
