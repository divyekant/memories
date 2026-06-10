"""Tests for OpenAIEmbedder configuration: base_url, api key handling, dimensions."""

from types import SimpleNamespace

import numpy as np
import pytest

import openai_embedder


class FakeEmbeddingsAPI:
    def __init__(self, dim, calls):
        self._dim = dim
        self._calls = calls

    def create(self, model, input):
        self._calls.append({"model": model, "input": list(input)})
        data = [SimpleNamespace(embedding=[0.5] * self._dim) for _ in input]
        return SimpleNamespace(data=data)


class FakeOpenAI:
    """Stands in for openai.OpenAI; records constructor kwargs."""

    instances = []

    def __init__(self, api_key=None, base_url=None, **kwargs):
        self.api_key = api_key
        self.base_url = base_url
        self.calls = []
        self.embeddings = FakeEmbeddingsAPI(FakeOpenAI.response_dim, self.calls)
        FakeOpenAI.instances.append(self)

    response_dim = 8


@pytest.fixture(autouse=True)
def fake_openai(monkeypatch):
    import openai

    FakeOpenAI.instances = []
    FakeOpenAI.response_dim = 8
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    yield FakeOpenAI


def test_base_url_forwarded_to_client(fake_openai):
    embedder = openai_embedder.OpenAIEmbedder(
        model="qwen3-embedding-0.6b",
        api_key="key",
        base_url="http://localhost:11434/v1",
        dimension=8,
    )
    client = fake_openai.instances[0]
    assert client.base_url == "http://localhost:11434/v1"
    assert embedder.get_sentence_embedding_dimension() == 8


def test_local_base_url_defaults_placeholder_key(fake_openai):
    openai_embedder.OpenAIEmbedder(
        model="m", base_url="http://localhost:11434/v1", dimension=8
    )
    client = fake_openai.instances[0]
    assert client.api_key  # OpenAI SDK requires a non-empty key even for local endpoints


def test_declared_dimension_skips_probe(fake_openai):
    embedder = openai_embedder.OpenAIEmbedder(model="unknown-model", api_key="k", dimension=8)
    client = fake_openai.instances[0]
    assert client.calls == []  # no probe API call
    assert embedder.get_sentence_embedding_dimension() == 8


def test_unknown_model_without_dimension_probes(fake_openai):
    embedder = openai_embedder.OpenAIEmbedder(model="unknown-model", api_key="k")
    client = fake_openai.instances[0]
    assert len(client.calls) == 1
    assert embedder.get_sentence_embedding_dimension() == 8


def test_known_model_dimension_still_mapped(fake_openai):
    embedder = openai_embedder.OpenAIEmbedder(model="text-embedding-3-small", api_key="k")
    client = fake_openai.instances[0]
    assert client.calls == []
    assert embedder.get_sentence_embedding_dimension() == 1536


def test_response_dimension_mismatch_raises(fake_openai):
    embedder = openai_embedder.OpenAIEmbedder(model="m", api_key="k", dimension=16)
    with pytest.raises(ValueError, match="dimension"):
        embedder.encode(["hello"])


def test_encode_normalizes(fake_openai):
    embedder = openai_embedder.OpenAIEmbedder(model="m", api_key="k", dimension=8)
    out = embedder.encode(["a", "b"], normalize_embeddings=True)
    assert out.shape == (2, 8)
    norms = np.linalg.norm(out, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)
