"""Entity-scoped lock manager for write serialization."""

from __future__ import annotations

from contextlib import contextmanager
import threading
from typing import Iterable, Iterator, List


class EntityLockManager:
    """Manages keyed locks with deterministic acquisition order."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def _get_lock(self, key: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    @contextmanager
    def acquire_many(self, keys: Iterable[str]) -> Iterator[None]:
        normalized: List[str] = sorted({k.strip() for k in keys if k and k.strip()})
        if not normalized:
            normalized = ["__default__"]

        held: List[threading.Lock] = []
        try:
            for key in normalized:
                lock = self._get_lock(key)
                lock.acquire()
                held.append(lock)
            yield
        finally:
            for lock in reversed(held):
                lock.release()

