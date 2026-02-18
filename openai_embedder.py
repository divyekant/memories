"""
OpenAI embedding provider — drop-in replacement for OnnxEmbedder.

Uses the OpenAI embeddings API (default: text-embedding-3-small).
No local model download or ONNX runtime required.
"""

import logging
import numpy as np
from typing import List, Union

logger = logging.getLogger("memories")

# Dimensions for well-known models — avoids a probe API call at startup
_KNOWN_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class OpenAIEmbedder:
    """
    Drop-in replacement for OnnxEmbedder using the OpenAI embeddings API.

    Supports the same interface:
        model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        model.get_sentence_embedding_dimension()
    """

    def __init__(self, model: str = "text-embedding-3-small", api_key: str = None):
        from openai import OpenAI

        self._model = model
        self._client = OpenAI(api_key=api_key)

        self._dim = _KNOWN_DIMENSIONS.get(model)
        if self._dim is None:
            probe = self._call_api(["probe"])
            self._dim = probe.shape[1]

        logger.info("OpenAI embedder loaded: model=%s, dim=%d", self._model, self._dim)

    def _call_api(self, texts: List[str]) -> np.ndarray:
        response = self._client.embeddings.create(model=self._model, input=texts)
        vectors = [item.embedding for item in response.data]
        return np.array(vectors, dtype=np.float32)

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(
        self,
        sentences: Union[str, List[str]],
        normalize_embeddings: bool = True,
        show_progress_bar: bool = False,
        batch_size: int = 512,
        **kwargs,
    ) -> np.ndarray:
        """
        Encode sentences to embeddings via the OpenAI API.

        Args:
            sentences: Single string or list of strings
            normalize_embeddings: L2-normalize output vectors
            show_progress_bar: Ignored (kept for API compatibility)
            batch_size: Max texts per API call

        Returns:
            np.ndarray of shape (n_sentences, embedding_dim)
        """
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
        """No-op: OpenAI client holds no local GPU/ONNX resources to release."""
        pass
