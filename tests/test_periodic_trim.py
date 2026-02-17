"""Tests for periodic memory trim task behavior in app.py."""

import asyncio
import importlib
import os
from unittest.mock import patch


def test_periodic_memory_trim_calls_trimmer():
    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": ""}):
        import app as app_module

        importlib.reload(app_module)

    with patch.object(app_module, "MEMORY_TRIM_PERIODIC_SEC", 0.01), patch.object(
        app_module.memory_trimmer, "maybe_trim", return_value={"trimmed": False, "reason": "cooldown"}
    ) as trim_mock:

        async def _run_once() -> None:
            task = asyncio.create_task(app_module._periodic_memory_trim())
            await asyncio.sleep(0.04)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        asyncio.run(_run_once())

    assert trim_mock.call_count >= 1
