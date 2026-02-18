"""Tests for entity-level lock behavior."""

import threading
import time

from entity_locks import EntityLockManager


def test_same_entity_serialized():
    manager = EntityLockManager()
    events = []

    def worker(name: str):
        with manager.acquire_many(["default:carto/poet-pads/db"]):
            events.append((name, "enter", time.perf_counter()))
            time.sleep(0.08)
            events.append((name, "exit", time.perf_counter()))

    t1 = threading.Thread(target=worker, args=("t1",))
    t2 = threading.Thread(target=worker, args=("t2",))

    start = time.perf_counter()
    t1.start()
    time.sleep(0.01)
    t2.start()
    t1.join()
    t2.join()
    elapsed = time.perf_counter() - start

    # Serialized critical sections should take roughly sum of both sleeps.
    assert elapsed >= 0.14
    first_exit = next(ts for name, ev, ts in events if name == "t1" and ev == "exit")
    second_enter = next(ts for name, ev, ts in events if name == "t2" and ev == "enter")
    assert second_enter >= first_exit


def test_different_entities_parallel():
    manager = EntityLockManager()
    barrier = threading.Barrier(3)

    def worker(key: str):
        barrier.wait()
        with manager.acquire_many([key]):
            time.sleep(0.10)

    t1 = threading.Thread(target=worker, args=("default:carto/poet-pads/db",))
    t2 = threading.Thread(target=worker, args=("default:carto/poet-pads/notes",))

    t1.start()
    t2.start()
    start = time.perf_counter()
    barrier.wait()
    t1.join()
    t2.join()
    elapsed = time.perf_counter() - start

    # Parallel lock domains should complete near single-sleep duration.
    assert elapsed < 0.18

