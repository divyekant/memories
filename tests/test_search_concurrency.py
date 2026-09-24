"""POST /search must not block the event loop while the engine searches."""

import asyncio
import importlib
import os
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest


@pytest.fixture
def app_module():
    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": ""}):
        import app as module

        importlib.reload(module)
        engine = MagicMock()

        def slow_search(**_kwargs):
            time.sleep(0.4)
            return []

        engine.hybrid_search.side_effect = slow_search
        engine.search.side_effect = slow_search
        module.memory = engine
        yield module


@pytest.mark.parametrize("hybrid", [True, False])
def test_concurrent_searches_run_in_parallel(app_module, hybrid):
    async def run():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            start = time.monotonic()
            responses = await asyncio.gather(*[
                client.post(
                    "/search",
                    json={"query": f"q{i}", "k": 3, "hybrid": hybrid},
                    headers={"X-API-Key": "test-key"},
                )
                for i in range(4)
            ])
            return time.monotonic() - start, responses

    elapsed, responses = asyncio.run(run())
    assert all(r.status_code == 200 for r in responses)
    # Serial execution takes 4 x 0.4s = 1.6s.
    assert elapsed < 1.0


def test_batch_items_run_in_parallel(app_module):
    async def run():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            start = time.monotonic()
            response = await client.post(
                "/search/batch",
                json={"queries": [{"query": f"q{i}", "k": 3} for i in range(4)]},
                headers={"X-API-Key": "test-key"},
            )
            return time.monotonic() - start, response

    elapsed, response = asyncio.run(run())
    assert response.status_code == 200
    assert [item["query"] for item in response.json()["results"]] == ["q0", "q1", "q2", "q3"]
    assert elapsed < 1.0
