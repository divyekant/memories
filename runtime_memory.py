"""Runtime memory reclamation helpers for long-lived API processes."""

from __future__ import annotations

import ctypes
import gc
import logging
import platform
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger("faiss-memory.runtime")


def _load_malloc_trim() -> Optional[Callable[[int], int]]:
    """Return libc.malloc_trim if available (Linux/glibc), else None."""
    if platform.system() != "Linux":
        return None

    for libc_name in ("libc.so.6", "libc.so"):
        try:
            libc = ctypes.CDLL(libc_name)
            trim = libc.malloc_trim
            trim.argtypes = [ctypes.c_size_t]
            trim.restype = ctypes.c_int
            return trim
        except Exception:
            continue
    return None


class MemoryTrimmer:
    """Runs GC + malloc_trim with cooldown to release allocator high-water marks."""

    def __init__(self, enabled: bool = True, cooldown_sec: float = 15.0):
        self.enabled = enabled
        self.cooldown_sec = max(0.0, float(cooldown_sec))
        self._lock = threading.Lock()
        self._last_trim_monotonic = 0.0
        self._malloc_trim = _load_malloc_trim() if enabled else None

        if enabled and self._malloc_trim is None:
            logger.info("malloc_trim unavailable; only gc.collect() will run")

    def maybe_trim(self, reason: str = "") -> dict:
        """Attempt memory trim unless disabled or cooldown window is active."""
        if not self.enabled:
            return {"trimmed": False, "reason": "disabled"}

        now = time.monotonic()
        with self._lock:
            elapsed = now - self._last_trim_monotonic
            if elapsed < self.cooldown_sec:
                return {
                    "trimmed": False,
                    "reason": "cooldown",
                    "seconds_until_next": round(self.cooldown_sec - elapsed, 3),
                }
            self._last_trim_monotonic = now

        collected = gc.collect()
        trimmed = False

        if self._malloc_trim is not None:
            try:
                trimmed = bool(self._malloc_trim(0))
            except Exception as exc:
                logger.debug("malloc_trim failed: %s", exc)

        logger.debug(
            "memory trim: reason=%s gc_collected=%d malloc_trim=%s",
            reason or "unspecified",
            collected,
            trimmed,
        )
        return {
            "trimmed": trimmed,
            "reason": reason or "unspecified",
            "gc_collected": collected,
        }
