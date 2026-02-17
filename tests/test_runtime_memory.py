"""Tests for runtime memory trimming helpers."""

from unittest.mock import patch

from runtime_memory import MemoryTrimmer


class TestMemoryTrimmer:
    def test_disabled_trimmer_noops(self):
        trimmer = MemoryTrimmer(enabled=False, cooldown_sec=0)
        result = trimmer.maybe_trim(reason="test")
        assert result["trimmed"] is False
        assert result["reason"] == "disabled"

    def test_trim_runs_gc_and_malloc_trim(self):
        trimmer = MemoryTrimmer(enabled=True, cooldown_sec=0)
        trimmer._malloc_trim = lambda _n: 1
        with patch("runtime_memory.gc.collect", return_value=7):
            result = trimmer.maybe_trim(reason="extract:stop")
        assert result["trimmed"] is True
        assert result["gc_collected"] == 7

    def test_cooldown_skips_repeated_trim(self):
        trimmer = MemoryTrimmer(enabled=True, cooldown_sec=60)
        trimmer._malloc_trim = lambda _n: 1
        with patch("runtime_memory.gc.collect", return_value=1):
            first = trimmer.maybe_trim(reason="first")
            second = trimmer.maybe_trim(reason="second")

        assert first["reason"] == "first"
        assert second["reason"] == "cooldown"
        assert second["trimmed"] is False
