"""Tests for CLI config resolution and layered precedence."""

import json
import os

import pytest

from cli.config import resolve_config, write_config, DEFAULT_URL


class TestDefaults:
    def test_default_url(self):
        cfg = resolve_config(config_dir="/nonexistent")
        assert cfg["url"] == DEFAULT_URL

    def test_default_api_key_is_none(self):
        cfg = resolve_config(config_dir="/nonexistent")
        assert cfg["api_key"] is None

    def test_sources_are_default(self):
        cfg = resolve_config(config_dir="/nonexistent")
        assert cfg["_sources"]["url"] == "default"
        assert cfg["_sources"]["api_key"] == "default"


class TestEnvVars:
    def test_env_url_overrides_default(self, monkeypatch):
        monkeypatch.setenv("MEMORIES_URL", "http://env:9999")
        cfg = resolve_config(config_dir="/nonexistent")
        assert cfg["url"] == "http://env:9999"
        assert cfg["_sources"]["url"] == "env"

    def test_env_api_key_overrides_default(self, monkeypatch):
        monkeypatch.setenv("MEMORIES_API_KEY", "env-key")
        cfg = resolve_config(config_dir="/nonexistent")
        assert cfg["api_key"] == "env-key"
        assert cfg["_sources"]["api_key"] == "env"


class TestConfigFile:
    def test_file_overrides_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEMORIES_URL", "http://env:9999")
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"url": "http://file:8888"}))
        cfg = resolve_config(config_dir=str(tmp_path))
        assert cfg["url"] == "http://file:8888"
        assert cfg["_sources"]["url"] == "file"

    def test_file_api_key(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"api_key": "file-key"}))
        cfg = resolve_config(config_dir=str(tmp_path))
        assert cfg["api_key"] == "file-key"
        assert cfg["_sources"]["api_key"] == "file"

    def test_missing_config_file_is_ok(self, tmp_path):
        cfg = resolve_config(config_dir=str(tmp_path))
        assert cfg["url"] == DEFAULT_URL


class TestFlags:
    def test_flag_url_overrides_all(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEMORIES_URL", "http://env:9999")
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"url": "http://file:8888"}))
        cfg = resolve_config(config_dir=str(tmp_path), flag_url="http://flag:7777")
        assert cfg["url"] == "http://flag:7777"
        assert cfg["_sources"]["url"] == "flag"

    def test_flag_api_key_overrides_all(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEMORIES_API_KEY", "env-key")
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"api_key": "file-key"}))
        cfg = resolve_config(config_dir=str(tmp_path), flag_api_key="flag-key")
        assert cfg["api_key"] == "flag-key"
        assert cfg["_sources"]["api_key"] == "flag"


class TestWriteConfig:
    def test_creates_config_file(self, tmp_path):
        path = write_config(config_dir=str(tmp_path), url="http://x:1234")
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["url"] == "http://x:1234"

    def test_merges_with_existing(self, tmp_path):
        write_config(config_dir=str(tmp_path), url="http://x:1234")
        write_config(config_dir=str(tmp_path), api_key="key123")
        data = json.loads((tmp_path / "config.json").read_text())
        assert data["url"] == "http://x:1234"
        assert data["api_key"] == "key123"
