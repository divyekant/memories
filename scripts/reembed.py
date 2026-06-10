#!/usr/bin/env python3
"""Blue/green re-embedding migration for the Memories Qdrant index.

Builds a NEW collection from the payload text of an existing collection,
embedding with a new model — the source collection is only ever read.
Cutover is a config re-point (env file), done separately and only with
--execute; rollback is pointing the config back at the old collection.

Subcommands:
    migrate   stream source points -> embed text -> upsert into target
              (resumable via a cursor state file; rate-limitable)
    verify    sample N points and compare old-vs-new top-k neighbor overlap
    cutover   re-point EMBED_* config in an env file (dry-run by default)
    status    print migration state

Examples (oMLX serving an MLX embedding model on the Mac host):
    uv run python scripts/reembed.py migrate \
        --url http://localhost:6333 --source memories \
        --provider openai --model Qwen3-Embedding-0.6B \
        --base-url http://localhost:11434/v1 --dimension 1024 \
        --batch-size 32 --max-rps 4

    uv run python scripts/reembed.py verify \
        --url http://localhost:6333 --source memories \
        --target memories__qwen3_embedding_0_6b_1024d --samples 25 --k 10

    uv run python scripts/reembed.py cutover \
        --url http://localhost:6333 --source memories \
        --target memories__qwen3_embedding_0_6b_1024d \
        --provider openai --model Qwen3-Embedding-0.6B \
        --base-url http://host.docker.internal:11434/v1 --dimension 1024 \
        --env-file .env            # dry-run: prints the plan
    ... --execute                  # actually rewrites the env file

The source collection is never written to. Safe to run against a live
Qdrant, but prefer quiet periods: migration adds read + embed load.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from embedding_space import EmbedderSettings, embedding_signature, model_slug  # noqa: E402

logger = logging.getLogger("reembed")

ENV_KEYS_MANAGED = (
    "EMBED_PROVIDER",
    "EMBED_MODEL",
    "EMBED_BASE_URL",
    "EMBED_DIMENSION",
    "EMBED_COLLECTION",
    "EMBED_QUERY_PREFIX",
    "EMBED_DOC_PREFIX",
)


class ReembedError(RuntimeError):
    pass


class RateLimiter:
    """Simple call-rate limiter (embed requests per second)."""

    def __init__(self, max_rps: float = 0.0, clock=time.monotonic, sleep=time.sleep):
        self.min_interval = (1.0 / max_rps) if max_rps and max_rps > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._last = 0.0

    def wait(self) -> None:
        if not self.min_interval:
            return
        now = self._clock()
        delta = now - self._last
        if delta < self.min_interval:
            self._sleep(self.min_interval - delta)
        self._last = self._clock()


def _serialize_offset(offset: Any) -> Any:
    if offset is None:
        return None
    if isinstance(offset, int):
        return {"kind": "int", "value": offset}
    return {"kind": "str", "value": str(offset)}


def _deserialize_offset(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return int(raw["value"]) if raw.get("kind") == "int" else raw.get("value")
    return raw


class ReembedMigrator:
    """Streams a source collection into a new embedding space.

    Only reads from ``source``; creates/writes ``target``. Resumable via a
    JSON state file holding the scroll cursor.
    """

    def __init__(
        self,
        client,
        embedder,
        source: str,
        target: str,
        *,
        batch_size: int = 64,
        state_path: Path | str,
        signature: str = "",
        max_rps: float = 0.0,
        text_field: str = "text",
        log=None,
    ):
        if source == target:
            raise ReembedError("source and target collections must differ")
        self.client = client
        self.embedder = embedder
        self.source = source
        self.target = target
        self.batch_size = max(1, int(batch_size))
        self.state_path = Path(state_path)
        self.signature = signature
        self.text_field = text_field
        self.rate_limiter = RateLimiter(max_rps)
        self.log = log or logger.info
        self.dim = embedder.get_sentence_embedding_dimension()

    # -- state ----------------------------------------------------------

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {
                "source": self.source,
                "target": self.target,
                "signature": self.signature,
                "offset": None,
                "migrated": 0,
                "skipped": 0,
                "done": False,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if state.get("source") != self.source or state.get("target") != self.target:
            raise ReembedError(
                f"Existing state file {self.state_path} tracks "
                f"{state.get('source')!r} -> {state.get('target')!r}, not "
                f"{self.source!r} -> {self.target!r}. Use a fresh --state-file "
                "or delete the stale one."
            )
        if self.signature and state.get("signature") not in ("", None, self.signature):
            raise ReembedError(
                f"State file {self.state_path} was written for embedding space "
                f"{state.get('signature')!r}, not {self.signature!r}."
            )
        return state

    def _save_state(self, state: Dict[str, Any]) -> None:
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    # -- target collection ----------------------------------------------

    def _ensure_target(self) -> None:
        from qdrant_client import models

        try:
            info = self.client.get_collection(collection_name=self.target)
        except Exception:
            info = None

        if info is not None:
            vectors = info.config.params.vectors
            size = getattr(vectors, "size", None)
            if size is None and isinstance(vectors, dict):
                size = next(
                    (getattr(v, "size", None) for v in vectors.values()), None
                )
            if size is not None and int(size) != self.dim:
                raise ReembedError(
                    f"Target collection {self.target!r} has dimension {size}, "
                    f"but the embedder produces {self.dim}-d vectors. Refusing "
                    "to mix embedding spaces."
                )
            return

        self.client.create_collection(
            collection_name=self.target,
            vectors_config=models.VectorParams(
                size=self.dim, distance=models.Distance.COSINE
            ),
        )
        self._ensure_target_payload_indexes()

    def _ensure_target_payload_indexes(self) -> None:
        """Mirror the engine's payload indexes (qdrant_store.ensure_payload_indexes)."""
        from qdrant_client import models

        for field, schema in (
            ("source", models.PayloadSchemaType.KEYWORD),
            ("archived", models.PayloadSchemaType.BOOL),
            ("document_at", models.PayloadSchemaType.KEYWORD),
            ("is_latest", models.PayloadSchemaType.BOOL),
        ):
            try:
                self.client.create_payload_index(
                    collection_name=self.target, field_name=field, field_schema=schema
                )
            except Exception:
                pass  # already exists / local mode quirk — same policy as QdrantStore

    # -- migration --------------------------------------------------------

    def _source_count(self) -> int:
        try:
            return int(self.client.count(collection_name=self.source, exact=True).count)
        except Exception:
            return 0

    def run(self, max_batches: Optional[int] = None) -> Dict[str, Any]:
        """Migrate (or resume migrating) source -> target. Returns final state."""
        from qdrant_client import models

        state = self._load_state()
        if state.get("done"):
            self.log(f"Migration already complete ({state['migrated']} points). Nothing to do.")
            return state

        self._ensure_target()
        total = self._source_count()
        offset = _deserialize_offset(state.get("offset"))
        batches = 0
        started = time.monotonic()
        migrated_this_run = 0

        while True:
            points, next_offset = self.client.scroll(
                collection_name=self.source,
                offset=offset,
                limit=self.batch_size,
                with_payload=True,
                with_vectors=False,
            )
            if not points:
                state["done"] = True
                state["offset"] = None
                self._save_state(state)
                break

            batch: List[Tuple[Any, Dict[str, Any]]] = []
            for point in points:
                payload = dict(getattr(point, "payload", {}) or {})
                text = payload.get(self.text_field)
                if not isinstance(text, str) or not text.strip():
                    state["skipped"] += 1
                    continue
                batch.append((getattr(point, "id"), payload))

            if batch:
                self.rate_limiter.wait()
                vectors = self.embedder.encode(
                    [payload[self.text_field] for _, payload in batch],
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                structs = [
                    models.PointStruct(
                        id=point_id,
                        vector=vectors[i].astype("float32").tolist()
                        if hasattr(vectors[i], "astype")
                        else list(vectors[i]),
                        payload=payload,
                    )
                    for i, (point_id, payload) in enumerate(batch)
                ]
                self.client.upsert(collection_name=self.target, points=structs, wait=True)
                state["migrated"] += len(batch)
                migrated_this_run += len(batch)

            state["offset"] = _serialize_offset(next_offset)
            if next_offset is None:
                state["done"] = True
            self._save_state(state)

            elapsed = max(time.monotonic() - started, 1e-6)
            rate = migrated_this_run / elapsed
            remaining = max(total - state["migrated"], 0)
            eta = f"{remaining / rate:,.0f}s" if rate > 0 else "?"
            self.log(
                f"[migrate] {state['migrated']}/{total} points "
                f"(skipped {state['skipped']}) — {rate:.1f} pts/s, ETA {eta}"
            )

            batches += 1
            offset = next_offset
            if state["done"]:
                break
            if max_batches is not None and batches >= max_batches:
                self.log(f"[migrate] pausing after {batches} batch(es); state saved, resumable.")
                break

        return state

    # -- verification ----------------------------------------------------

    def _all_ids(self, collection: str, cap: int = 200_000) -> List[Any]:
        ids: List[Any] = []
        offset = None
        while len(ids) < cap:
            points, offset = self.client.scroll(
                collection_name=collection,
                offset=offset,
                limit=1000,
                with_payload=False,
                with_vectors=False,
            )
            if not points:
                break
            ids.extend(getattr(p, "id") for p in points)
            if offset is None:
                break
        return ids

    def _top_k_neighbors(self, collection: str, point_id: Any, k: int) -> List[Any]:
        records = self.client.retrieve(
            collection_name=collection, ids=[point_id], with_vectors=True
        )
        if not records or getattr(records[0], "vector", None) is None:
            return []
        vector = records[0].vector
        response = self.client.query_points(
            collection_name=collection,
            query=vector,
            limit=k + 1,
            with_payload=False,
        )
        points = getattr(response, "points", response)
        return [getattr(p, "id") for p in points if getattr(p, "id") != point_id][:k]

    def verify(self, samples: int = 20, k: int = 10, seed: Optional[int] = None) -> Dict[str, Any]:
        """Old-vs-new top-k neighbor overlap on N random points.

        Overlap < 1.0 is EXPECTED (a better model ranks differently); this
        is a sanity check, not a quality score. Near-zero overlap usually
        means wrong payload text, a broken endpoint, or shuffled ids.
        Retrieval quality should be judged with the eval harness instead.
        """
        source_ids = self._all_ids(self.source)
        target_ids = set(self._all_ids(self.target))
        common = [pid for pid in source_ids if pid in target_ids]
        rng = random.Random(seed)
        sample_ids = rng.sample(common, min(samples, len(common)))

        per_sample = []
        for pid in sample_ids:
            old_neighbors = set(self._top_k_neighbors(self.source, pid, k))
            new_neighbors = set(self._top_k_neighbors(self.target, pid, k))
            denom = max(1, min(k, len(old_neighbors), len(new_neighbors)))
            overlap = len(old_neighbors & new_neighbors) / denom
            per_sample.append({"id": pid, "overlap": round(overlap, 4)})

        overlaps = sorted(row["overlap"] for row in per_sample)
        n = len(overlaps)
        report = {
            "samples": n,
            "k": k,
            "source_count": len(source_ids),
            "target_count": len(target_ids),
            "mean_overlap": round(sum(overlaps) / n, 4) if n else 0.0,
            "median_overlap": overlaps[n // 2] if n else 0.0,
            "min_overlap": overlaps[0] if n else 0.0,
            "per_sample": per_sample,
        }
        self.log(
            f"[verify] {n} samples, top-{k} neighbor overlap: "
            f"mean={report['mean_overlap']:.2f} median={report['median_overlap']:.2f} "
            f"min={report['min_overlap']:.2f} "
            f"(counts: source={report['source_count']} target={report['target_count']})"
        )
        return report


# -- cutover --------------------------------------------------------------


def check_cutover_ready(client, source: str, target: str) -> Tuple[bool, str]:
    """Counts must match before re-pointing config at the new collection."""
    src = int(client.count(collection_name=source, exact=True).count)
    dst = int(client.count(collection_name=target, exact=True).count)
    if src != dst:
        return False, f"point count mismatch: source={src} target={dst}"
    return True, f"counts match ({src} points)"


def build_env_updates(
    provider: str,
    model: str,
    target_collection: str,
    base_url: str = "",
    dimension: Optional[int] = None,
    query_prefix: str = "",
    document_prefix: str = "",
) -> Dict[str, str]:
    """EMBED_* lines for cutover. Never includes secrets (EMBED_API_KEY)."""
    updates = {
        "EMBED_PROVIDER": provider,
        "EMBED_MODEL": model,
        "EMBED_COLLECTION": target_collection,
    }
    if base_url:
        updates["EMBED_BASE_URL"] = base_url
    if dimension:
        updates["EMBED_DIMENSION"] = str(int(dimension))
    if query_prefix:
        updates["EMBED_QUERY_PREFIX"] = query_prefix
    if document_prefix:
        updates["EMBED_DOC_PREFIX"] = document_prefix
    return updates


def _parse_env_lines(lines: List[str]) -> Dict[str, str]:
    values = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value
    return values


def apply_cutover(env_path: Path | str, updates: Dict[str, str], execute: bool = False) -> Dict[str, Any]:
    """Re-point EMBED_* config in an env file. Dry-run unless execute=True.

    Returns a plan dict including a ``rollback`` map (the previous values) —
    rollback is restoring those keys and restarting the service; the old
    collection is never modified.
    """
    env_path = Path(env_path)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    current = _parse_env_lines(lines)
    rollback = {key: current.get(key, "") for key in ENV_KEYS_MANAGED}

    new_lines: List[str] = []
    seen = set()
    for line in lines:
        stripped = line.strip()
        key = stripped.partition("=")[0].strip() if "=" in stripped else None
        if key in updates and not stripped.startswith("#"):
            new_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            new_lines.append(line)
    missing = [key for key in updates if key not in seen]
    if missing:
        new_lines.append("# --- embedding cutover (scripts/reembed.py) ---")
        new_lines.extend(f"{key}={updates[key]}" for key in missing)

    plan = {
        "env_file": str(env_path),
        "executed": False,
        "updates": dict(updates),
        "rollback": rollback,
    }
    if not execute:
        return plan

    if env_path.exists():
        backup = env_path.with_name(
            f"{env_path.name}.bak-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy2(env_path, backup)
        plan["backup"] = str(backup)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    plan["executed"] = True
    return plan


# -- CLI -------------------------------------------------------------------


def _build_embedder(args):
    if args.provider == "onnx":
        from onnx_embedder import OnnxEmbedder

        return OnnxEmbedder(args.model)
    if args.provider == "openai":
        from openai_embedder import OpenAIEmbedder

        return OpenAIEmbedder(
            args.model,
            api_key=args.api_key or os.getenv("EMBED_API_KEY") or os.getenv("OPENAI_API_KEY"),
            base_url=args.base_url or None,
            dimension=args.dimension,
        )
    raise ReembedError(f"Unknown provider {args.provider!r} (use onnx or openai)")


def _build_client(args):
    from qdrant_client import QdrantClient

    if args.qdrant_path:
        # Embedded/local mode holds an exclusive lock — stop the service first.
        return QdrantClient(path=args.qdrant_path)
    kwargs = {}
    api_key = os.getenv("QDRANT_API_KEY", "").strip()
    if api_key:
        kwargs["api_key"] = api_key
    return QdrantClient(url=args.url, **kwargs)


def _default_target(args) -> str:
    dim = args.dimension
    if not dim:
        raise ReembedError(
            "--dimension is required to derive the target collection name "
            "(or pass --target explicitly)"
        )
    return f"{args.source}__{model_slug(args.model)}_{int(dim)}d"


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", default=os.getenv("QDRANT_URL", "http://localhost:6333"),
                        help="Qdrant URL (default: $QDRANT_URL or http://localhost:6333)")
    parser.add_argument("--qdrant-path", default="",
                        help="Embedded Qdrant path instead of --url (service must be stopped)")
    parser.add_argument("--source", default=os.getenv("QDRANT_COLLECTION", "memories"),
                        help="Source collection (read-only; default: memories)")
    parser.add_argument("--target", default="",
                        help="Target collection (default: derived as <source>__<model>_<dim>d)")
    parser.add_argument("--provider", default=os.getenv("EMBED_PROVIDER", "openai"),
                        choices=["onnx", "openai"])
    parser.add_argument("--model", default=os.getenv("EMBED_MODEL", ""),
                        help="New embedding model name")
    parser.add_argument("--base-url", default=os.getenv("EMBED_BASE_URL", ""),
                        help="OpenAI-compatible endpoint (e.g. http://localhost:11434/v1 for oMLX)")
    parser.add_argument("--api-key", default="", help="API key for the embedding endpoint")
    parser.add_argument("--dimension", type=int, default=int(os.getenv("EMBED_DIMENSION", "0") or 0),
                        help="Expected embedding dimension (recommended; avoids a probe call)")
    parser.add_argument("--query-prefix", default=os.getenv("EMBED_QUERY_PREFIX", ""))
    parser.add_argument("--doc-prefix", default=os.getenv("EMBED_DOC_PREFIX", ""))
    parser.add_argument("--state-file", default="",
                        help="Cursor state file (default: ./.reembed-state-<source>__<target>.json)")


def _resolve_state_path(args, target: str) -> Path:
    if args.state_file:
        return Path(args.state_file)
    return Path(f".reembed-state-{args.source}__{target}.json")


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_migrate = sub.add_parser("migrate", help="re-embed source into a new target collection")
    _add_common(p_migrate)
    p_migrate.add_argument("--batch-size", type=int, default=64)
    p_migrate.add_argument("--max-rps", type=float, default=0.0,
                           help="Max embedding calls per second (0 = unlimited)")
    p_migrate.add_argument("--max-batches", type=int, default=0,
                           help="Stop after N batches (resume later); 0 = run to completion")

    p_verify = sub.add_parser("verify", help="old-vs-new neighbor overlap sampling")
    _add_common(p_verify)
    p_verify.add_argument("--samples", type=int, default=25)
    p_verify.add_argument("--k", type=int, default=10)
    p_verify.add_argument("--seed", type=int, default=None)
    p_verify.add_argument("--report", default="", help="Write the JSON report here")

    p_cutover = sub.add_parser("cutover", help="re-point EMBED_* config at the new collection")
    _add_common(p_cutover)
    p_cutover.add_argument("--env-file", default=".env")
    p_cutover.add_argument("--execute", action="store_true",
                           help="Apply the change (default is dry-run)")
    p_cutover.add_argument("--force", action="store_true",
                           help="Skip the point-count equality check")

    p_status = sub.add_parser("status", help="print migration state file")
    _add_common(p_status)

    args = parser.parse_args(argv)
    if not args.model and args.command != "status":
        parser.error("--model is required")

    target = args.target or _default_target(args)
    state_path = _resolve_state_path(args, target)

    if args.command == "status":
        if state_path.exists():
            print(state_path.read_text(encoding="utf-8"))
        else:
            print(f"No state file at {state_path}")
        return 0

    client = _build_client(args)

    if args.command == "migrate":
        embedder = _build_embedder(args)
        signature = embedding_signature(
            args.provider, args.model, embedder.get_sentence_embedding_dimension(),
            query_prefix=args.query_prefix, document_prefix=args.doc_prefix,
        )
        migrator = ReembedMigrator(
            client=client,
            embedder=embedder,
            source=args.source,
            target=target,
            batch_size=args.batch_size,
            state_path=state_path,
            signature=signature,
            max_rps=args.max_rps,
        )
        if args.doc_prefix:
            # Documents must be embedded with the document prefix; wrap encode.
            inner = migrator.embedder

            class _PrefixedEmbedder:
                def get_sentence_embedding_dimension(self):
                    return inner.get_sentence_embedding_dimension()

                def encode(self, texts, **kwargs):
                    return inner.encode([f"{args.doc_prefix}{t}" for t in texts], **kwargs)

            migrator.embedder = _PrefixedEmbedder()
        state = migrator.run(max_batches=args.max_batches or None)
        print(json.dumps({k: v for k, v in state.items() if k != "offset"}, indent=2))
        return 0 if state.get("done") else 3  # 3 = paused/resumable

    if args.command == "verify":
        embedder_stub = _DimOnly(args.dimension or 1)
        migrator = ReembedMigrator(
            client=client,
            embedder=embedder_stub,
            source=args.source,
            target=target,
            state_path=state_path,
        )
        report = migrator.verify(samples=args.samples, k=args.k, seed=args.seed)
        if args.report:
            Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k != "per_sample"}, indent=2))
        return 0

    if args.command == "cutover":
        ok, detail = check_cutover_ready(client, args.source, target)
        print(f"Readiness: {detail}")
        if not ok and not args.force:
            print("Refusing cutover (use --force to override).", file=sys.stderr)
            return 2
        updates = build_env_updates(
            provider=args.provider,
            model=args.model,
            target_collection=target,
            base_url=args.base_url,
            dimension=args.dimension or None,
            query_prefix=args.query_prefix,
            document_prefix=args.doc_prefix,
        )
        plan = apply_cutover(args.env_file, updates, execute=args.execute)
        print(json.dumps(plan, indent=2))
        if not args.execute:
            print("\nDRY RUN — re-run with --execute to apply. Then restart the service.")
        else:
            print(
                "\nApplied. Restart the Memories service to switch collections.\n"
                "ROLLBACK: restore the 'rollback' values above (or the .bak file) "
                "and restart — the old collection was never modified.\n"
                "Reminder: EMBED_API_KEY is never written; set it separately if needed."
            )
        return 0

    return 1


class _DimOnly:
    """Embedder stand-in for verify mode (no encoding needed)."""

    def __init__(self, dim: int):
        self._dim = dim

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(self, *args, **kwargs):  # pragma: no cover
        raise ReembedError("verify mode does not embed")


if __name__ == "__main__":
    sys.exit(main())
