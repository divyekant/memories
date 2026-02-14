"""
ONNX-based sentence embedder — drop-in replacement for SentenceTransformer.

Uses onnxruntime for inference (~50MB) instead of PyTorch (~800MB).
Same model (all-MiniLM-L6-v2), same output, ~4x smaller Docker image.
"""

import logging
import numpy as np
import onnxruntime as ort
from typing import List, Union
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

logger = logging.getLogger("faiss-memory")

# Map of short names to HuggingFace repo IDs
MODEL_MAP = {
    "all-MiniLM-L6-v2": "sentence-transformers/all-MiniLM-L6-v2",
}


class OnnxEmbedder:
    """
    Drop-in replacement for SentenceTransformer using ONNX Runtime.

    Supports the same interface:
        model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        model.get_sentence_embedding_dimension()
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", cache_dir: str = None):
        repo_id = MODEL_MAP.get(model_name, model_name)
        self._model_name = model_name

        # Download ONNX model + tokenizer from HuggingFace
        onnx_path = hf_hub_download(
            repo_id=repo_id,
            filename="onnx/model.onnx",
            cache_dir=cache_dir,
        )
        tokenizer_path = hf_hub_download(
            repo_id=repo_id,
            filename="tokenizer.json",
            cache_dir=cache_dir,
        )

        # Load ONNX model
        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_options.intra_op_num_threads = 4
        self.session = ort.InferenceSession(onnx_path, sess_options)

        # Load tokenizer (fast Rust-based from HuggingFace tokenizers lib)
        self.tokenizer = Tokenizer.from_file(tokenizer_path)
        self.tokenizer.enable_padding()
        self.tokenizer.enable_truncation(max_length=256)

        # Detect expected input names from model
        self._input_names = [inp.name for inp in self.session.get_inputs()]

        # Get output dimension from a test encode
        self._dim = self._get_dim()
        logger.info(
            "ONNX embedder loaded: model=%s, dim=%d", self._model_name, self._dim
        )

    def _get_dim(self) -> int:
        """Determine embedding dimension from model output"""
        test = self.encode(["test"], normalize_embeddings=False)
        return test.shape[1]

    def get_sentence_embedding_dimension(self) -> int:
        """Compatible with SentenceTransformer API"""
        return self._dim

    def encode(
        self,
        sentences: Union[str, List[str]],
        normalize_embeddings: bool = True,
        show_progress_bar: bool = False,
        batch_size: int = 64,
        **kwargs,
    ) -> np.ndarray:
        """
        Encode sentences to embeddings.

        Args:
            sentences: Single string or list of strings
            normalize_embeddings: L2-normalize output vectors
            show_progress_bar: Ignored (kept for API compatibility)
            batch_size: Batch size for encoding

        Returns:
            np.ndarray of shape (n_sentences, embedding_dim)
        """
        if isinstance(sentences, str):
            sentences = [sentences]

        all_embeddings = []

        for i in range(0, len(sentences), batch_size):
            batch = sentences[i : i + batch_size]

            # Tokenize
            encoded = self.tokenizer.encode_batch(batch)

            input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
            attention_mask = np.array(
                [e.attention_mask for e in encoded], dtype=np.int64
            )

            # Build feed dict based on what the model expects
            feed = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
            }
            if "token_type_ids" in self._input_names:
                feed["token_type_ids"] = np.zeros_like(input_ids, dtype=np.int64)

            # Run ONNX inference
            outputs = self.session.run(None, feed)

            # Mean pooling over token embeddings (same as sentence-transformers)
            token_embeddings = outputs[0]  # (batch, seq_len, hidden_dim)
            mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
            sum_embeddings = np.sum(token_embeddings * mask_expanded, axis=1)
            sum_mask = np.clip(mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
            embeddings = sum_embeddings / sum_mask

            all_embeddings.append(embeddings)

        result = np.vstack(all_embeddings).astype(np.float32)

        if normalize_embeddings:
            norms = np.linalg.norm(result, axis=1, keepdims=True)
            norms = np.clip(norms, a_min=1e-9, a_max=None)
            result = result / norms

        return result
