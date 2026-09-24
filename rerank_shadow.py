"""Shadow reranker for /search.

Scores the top search candidates with a small cross-encoder in one background
thread and logs the comparison. It never changes a response and never writes
memory state. Off unless RERANK_SHADOW_ENABLED=true. See
docs/decisions/2026-09-23-memory-orchestrator-small-model.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import math
import os
import platform
import random
import re
import resource
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import numpy as np

logger = logging.getLogger("memories.rerank_shadow")

DEFAULT_MODEL = "cross-encoder/ettin-reranker-68m-v1"
DEFAULT_REVISION = "d166fa88ddde3c42bc3ee92f7df476d941c8204a"


def _safetensors(path: str) -> dict[str, np.ndarray]:
    raw = Path(path).read_bytes()
    size = struct.unpack("<Q", raw[:8])[0]
    header = json.loads(raw[8 : 8 + size])
    body = raw[8 + size :]
    out = {}
    for name, info in header.items():
        if name == "__metadata__":
            continue
        if info["dtype"] != "F32":
            raise ValueError(f"unsupported dtype {info['dtype']}")
        start, end = info["data_offsets"]
        out[name] = np.frombuffer(body[start:end], dtype=np.float32).reshape(info["shape"])
    return out


_erf = np.vectorize(math.erf)


class EttinReranker:
    """ONNX transformer + numpy head: CLS -> Dense+GELU -> LayerNorm -> Dense."""

    def __init__(
        self,
        repo: str = DEFAULT_MODEL,
        revision: str | None = None,
        onnx_file: str | None = None,
        max_len: int = 1024,
        threads: int = 0,
        cache_dir: str | None = None,
    ):
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        if onnx_file is None:
            arm = platform.machine().lower() in ("arm64", "aarch64")
            onnx_file = "onnx/model_qint8_arm64.onnx" if arm else "onnx/model_quint8_avx2.onnx"
        get = lambda f: hf_hub_download(repo, f, revision=revision, cache_dir=cache_dir)
        opts = ort.SessionOptions()
        if threads:
            opts.intra_op_num_threads = threads
            opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(get(onnx_file), opts, providers=["CPUExecutionProvider"])
        self.tok = Tokenizer.from_file(get("tokenizer.json"))
        self.tok.enable_truncation(max_len)
        d2, ln, d4 = (_safetensors(get(f"{m}/model.safetensors")) for m in ("2_Dense", "3_LayerNorm", "4_Dense"))
        self.w2 = d2["linear.weight"]
        self.ln_w, self.ln_b = ln["norm.weight"], ln["norm.bias"]
        self.w4, self.b4 = d4["linear.weight"], d4["linear.bias"]

    def score(self, query: str, docs: list[str]) -> np.ndarray:
        scores = []
        for doc in docs:  # batch of 1 avoids padding cost on uneven lengths
            enc = self.tok.encode(query, doc)
            ids = np.array([enc.ids], dtype=np.int64)
            mask = np.array([enc.attention_mask], dtype=np.int64)
            cls = self.session.run(None, {"input_ids": ids, "attention_mask": mask})[0][0, 0]
            h = cls @ self.w2.T
            h = 0.5 * h * (1 + _erf(h / math.sqrt(2)))
            h = (h - h.mean()) / np.sqrt(h.var() + 1e-5) * self.ln_w + self.ln_b
            scores.append(float((h @ self.w4.T + self.b4)[0]))
        return np.array(scores)


def query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


class RerankShadow:
    """One worker thread, one task in flight; extra requests are dropped."""

    def __init__(
        self,
        scorer_factory: Callable[[], Any],
        log_dir: str,
        model: str = DEFAULT_MODEL,
        top_n: int = 20,
        sample_rate: float = 0.2,
        max_bytes: int = 10 * 1024 * 1024,
        backups: int = 5,
    ):
        self.model = model
        self.top_n = top_n
        self.sample_rate = sample_rate
        self.dropped = 0
        self.errors = 0
        self.disabled = False
        self._factory = scorer_factory
        self._scorer = None
        self._scorer_lock = threading.Lock()
        self._slot = threading.BoundedSemaphore(1)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rerank-shadow")
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9._-]", "_", model)
        self._log = logging.getLogger(f"memories.rerank_shadow.log.{name}")
        self._log.propagate = False
        self._log.setLevel(logging.INFO)
        for old in list(self._log.handlers):
            self._log.removeHandler(old)
            old.close()
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, f"rerank-shadow-{name}.jsonl"), maxBytes=max_bytes, backupCount=backups
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._log.addHandler(handler)

    def _get_scorer(self):
        with self._scorer_lock:
            if self._scorer is None:
                self._scorer = self._factory()
            return self._scorer

    def warm(self) -> None:
        """Load (and download, if missing) the model off the request path."""
        if self._slot.acquire(blocking=False):
            self._executor.submit(self._warm).add_done_callback(lambda _: self._slot.release())

    def _warm(self) -> None:
        if self._load():
            logger.info("Rerank shadow model ready: %s", self.model)

    def _load(self):
        """Return the scorer, or None after a load failure. One failure disables the shadow."""
        try:
            return self._get_scorer()
        except Exception:
            if not self.disabled:
                self.disabled = True
                self.errors += 1
                logger.exception("Rerank shadow model load failed; shadow disabled until restart")
            return None

    def observe(self, route: str, query: str, k: int, primary_ids: list, fetch: Callable[[], list]) -> bool:
        if self.disabled or random.random() >= self.sample_rate:
            return False
        if not self._slot.acquire(blocking=False):
            self.dropped += 1
            return False
        future = self._executor.submit(self._run, route, query, k, list(primary_ids), fetch)
        future.add_done_callback(lambda _: self._slot.release())
        return True

    def _run(self, route: str, query: str, k: int, primary_ids: list, fetch: Callable[[], list]) -> None:
        record: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "route": route,
            "model": self.model,
            "query_sha256": query_hash(query),
            "query_chars": len(query),
            "k": k,
            "primary_ids": primary_ids,
            "error": None,
        }
        scorer = self._load()
        if scorer is None:
            return  # load failure already logged once; skip the search cost
        try:
            t0 = time.perf_counter()
            candidates = [c for c in fetch() if "id" in c][: self.top_n]
            t1 = time.perf_counter()
            texts = [str(c.get("text", "")) for c in candidates]
            scores = scorer.score(query, texts) if candidates else np.array([])
            t2 = time.perf_counter()
            order = np.argsort(-scores, kind="stable")
            record.update(
                candidate_ids=[c["id"] for c in candidates],
                reranked_ids=[candidates[i]["id"] for i in order],
                scores=[round(float(s), 4) for s in scores],
                text_chars=[len(t) for t in texts],
                retrieve_ms=round((t1 - t0) * 1000, 1),
                rerank_ms=round((t2 - t1) * 1000, 1),
            )
        except Exception as exc:  # never raise into the worker; record the class only
            self.errors += 1
            record["error"] = type(exc).__name__
        record.update(
            dropped_total=self.dropped,
            errors_total=self.errors,
            peak_rss_kb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        )
        self._log.info(json.dumps(record))

    def close(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=not wait)


def _env_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def from_env() -> RerankShadow | None:
    if not _env_true("RERANK_SHADOW_ENABLED"):
        return None
    model = os.getenv("RERANK_SHADOW_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    revision = os.getenv("RERANK_SHADOW_REVISION", "").strip() or (DEFAULT_REVISION if model == DEFAULT_MODEL else None)
    max_tokens = int(os.getenv("RERANK_SHADOW_MAX_TOKENS", "512"))
    cache_dir = os.getenv("MODEL_CACHE_DIR", "").strip() or None
    return RerankShadow(
        scorer_factory=lambda: EttinReranker(model, revision=revision, max_len=max_tokens, threads=1, cache_dir=cache_dir),
        log_dir=os.getenv("SHADOW_LOG_DIR", "/data/shadow-logs"),
        model=model,
        top_n=int(os.getenv("RERANK_SHADOW_TOP_N", "20")),
        sample_rate=float(os.getenv("RERANK_SHADOW_SAMPLE_RATE", "0.2")),
    )
