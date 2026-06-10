"""
OpenAI-compatible embedding provider — drop-in replacement for OnnxEmbedder.

Uses the OpenAI embeddings API (default: text-embedding-3-small) or any
OpenAI-compatible endpoint via ``base_url`` (e.g. oMLX serving MLX embedding
models on http://localhost:11434/v1). No local model download or ONNX
runtime required.
"""

import logging
import threading
import numpy as np
from typing import List, Union

logger = logging.getLogger("memories")

# Dimensions for well-known models — avoids a probe API call at startup
_KNOWN_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

# OpenAI SDK retries transient errors (429, 5xx) automatically.
# We add one defensive retry layer for network-level failures.
_MAX_RETRIES = 2


class OpenAIEmbedder:
    """
    Drop-in replacement for OnnxEmbedder using the OpenAI embeddings API.

    Thread safety is provided by the caller (MemoryEngine._embedder_lock),
    but we include a _closed flag for parity with OnnxEmbedder.

    Supports the same interface:
        model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        model.get_sentence_embedding_dimension()
    """

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str = None,
        base_url: str = None,
        dimension: int = None,
    ):
        import openai

        self._model = model
        client_kwargs = {}
        if base_url:
            client_kwargs["base_url"] = base_url
            if not api_key:
                # Local OpenAI-compatible endpoints (oMLX, LM Studio, vLLM)
                # usually ignore the key, but the SDK requires a non-empty one.
                api_key = "sk-local"
        self._client = openai.OpenAI(api_key=api_key, **client_kwargs)
        self._closed = False
        self._lock = threading.RLock()

        if dimension:
            self._dim = int(dimension)
        else:
            self._dim = _KNOWN_DIMENSIONS.get(model)
        if self._dim is None:
            probe = self._call_api(["probe"])
            self._dim = probe.shape[1]

        logger.info(
            "OpenAI embedder loaded: model=%s, dim=%d, base_url=%s",
            self._model,
            self._dim,
            base_url or "default",
        )

    def _call_api(self, texts: List[str]) -> np.ndarray:
        last_exc = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = self._client.embeddings.create(model=self._model, input=texts)
                vectors = [item.embedding for item in response.data]
                result = np.array(vectors, dtype=np.float32)
                if self._dim is not None and result.shape[1] != self._dim:
                    raise ValueError(
                        f"Embedding endpoint returned dimension {result.shape[1]} "
                        f"but {self._dim} was configured for model {self._model!r}. "
                        "Check EMBED_DIMENSION / the served model."
                    )
                return result
            except ValueError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "OpenAI embedding call failed (attempt %d/%d): %s",
                        attempt + 1,
                        _MAX_RETRIES + 1,
                        exc,
                    )
                    continue
        raise last_exc  # type: ignore[misc]

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(
        self,
        sentences: Union[str, List[str]],
        normalize_embeddings: bool = True,
        show_progress_bar: bool = False,
        batch_size: int = 128,
        **kwargs,
    ) -> np.ndarray:
        """
        Encode sentences to embeddings via the OpenAI API.

        Args:
            sentences: Single string or list of strings
            normalize_embeddings: L2-normalize output vectors
            show_progress_bar: Ignored (kept for API compatibility)
            batch_size: Max texts per API call (default 128; API limit is 2048
                        but lower values reduce per-request token pressure)

        Returns:
            np.ndarray of shape (n_sentences, embedding_dim)
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("Cannot encode: embedder has been closed")

        if isinstance(sentences, str):
            sentences = [sentences]

        all_embeddings = []
        for i in range(0, len(sentences), batch_size):
            batch = sentences[i : i + batch_size]
            all_embeddings.append(self._call_api(batch))

        result = np.vstack(all_embeddings)

        if normalize_embeddings:
            norms = np.linalg.norm(result, axis=1, keepdims=True)
            norms = np.clip(norms, a_min=1e-9, a_max=None)
            result = result / norms

        return result

    def close(self) -> None:
        """Mark embedder as closed. OpenAI client holds no local resources."""
        with self._lock:
            self._closed = True
