"""Base adapter interface for graph eval datasets."""

from abc import ABC, abstractmethod
from typing import Any


class DatasetAdapter(ABC):
    """Interface for graph eval dataset adapters.

    Each adapter knows how to load, seed, link, search, and score
    a specific benchmark dataset.
    """

    @abstractmethod
    def load_questions(self, path: str, max_questions: int = 0, **kwargs) -> list[dict]:
        """Load questions from dataset file. Returns list of question dicts."""
        ...

    def seed_corpus(self, client) -> dict:
        """Seed shared corpus (for shared-corpus mode). Returns id_map.

        Override for datasets where all questions share a common passage pool
        (e.g., 2WikiMultiHopQA). Default: no-op (isolated mode).
        """
        return {}

    @abstractmethod
    def seed_window(self, client, questions: list[dict], corpus_ids: dict) -> dict:
        """Seed a window of questions. Returns {qid: {para_idx: real_id}}.

        For isolated mode: seeds passages + links.
        For shared-corpus mode: only seeds links (corpus already loaded).
        """
        ...

    @abstractmethod
    def cleanup_window(self, client, questions: list[dict], corpus_ids: dict):
        """Clean up after a window. Remove per-window data."""
        ...

    def cleanup_corpus(self, client):
        """Remove shared corpus (for shared-corpus mode). Default: no-op."""
        pass

    @abstractmethod
    def search_scope(self, question: dict) -> str:
        """Return source_prefix for scoping search to this question's memories."""
        ...

    @abstractmethod
    def score(self, question: dict, results_off: list, results_on: list, id_map: dict) -> dict:
        """Score results for a single question. Returns metrics dict."""
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @property
    def mode(self) -> str:
        """'isolated' or 'shared_corpus'."""
        return "isolated"
