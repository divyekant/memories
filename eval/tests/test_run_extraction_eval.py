"""Tests for the extraction model comparison harness."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "run_extraction_eval.py"
    spec = importlib.util.spec_from_file_location("run_extraction_eval", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_get_models_includes_small_local_candidates():
    module = _load_module()

    models = module.get_models()

    assert "qwen3:4b" in models
    assert "qwen3.5:4b" in models
    assert "gemma3:4b" in models
    assert "memex:1.7b" in models


def test_get_models_respects_filter():
    module = _load_module()

    models = module.get_models(["qwen3.5:4b", "memex:1.7b"])

    assert set(models.keys()) == {"qwen3.5:4b", "memex:1.7b"}
