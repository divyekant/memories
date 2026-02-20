"""Consolidation and pruning module for memory management.

Finds redundant memory clusters, merges them via LLM, and identifies
stale unused memories for cleanup.
"""

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

CONSOLIDATION_PROMPT = """These {n} memories are about the same topic in the {project} project.
Consolidate them into 1-2 concise memories that capture ALL unique information.
Drop redundant or overlapping details. Preserve: decisions and reasoning, bug fixes, conventions.

Memories to consolidate:
{memories_json}

Output a JSON array of consolidated text strings. Each must be self-contained."""

# Categories that use the longer decision_days threshold (lowercase — matches llm_extract.py)
_LONG_LIVED_CATEGORIES = {"decision", "learning"}


def _parse_datetime(ts: str) -> datetime:
    """Parse an ISO datetime string, handling both +00:00 and Z suffixes."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def find_clusters(
    engine,
    source_prefix: str = "",
    similarity_threshold: float = 0.75,
    min_cluster_size: int = 3,
) -> List[List[Dict]]:
    """Group memories by semantic similarity into clusters.

    Iterates through memories matching source_prefix, uses
    engine.hybrid_search() to find similar memories, and groups
    those with similarity >= threshold into clusters.

    Args:
        engine: MemoryEngine instance with metadata and hybrid_search.
        source_prefix: Only consider memories whose source starts with this.
        similarity_threshold: Minimum similarity to join a cluster.
        min_cluster_size: Minimum number of members for a cluster to be returned.

    Returns:
        List of clusters, where each cluster is a list of memory dicts.
    """
    # Filter for memories matching source_prefix
    candidates = []
    for m in engine.metadata:
        if not m:
            continue
        if source_prefix and not m.get("source", "").startswith(source_prefix):
            continue
        candidates.append(m)

    if not candidates:
        return []

    clustered_ids: set = set()
    clusters: List[List[Dict]] = []

    for mem in candidates:
        mem_id = mem["id"]
        if mem_id in clustered_ids:
            continue

        # Search for similar memories
        search_kwargs = {"query": mem["text"], "k": 10}
        if source_prefix:
            search_kwargs["source_prefix"] = source_prefix
        similar = engine.hybrid_search(**search_kwargs)

        # Build cluster: start with the seed memory
        cluster = [mem]
        cluster_ids = {mem_id}

        for hit in similar:
            hit_id = hit["id"]
            if hit_id == mem_id:
                continue
            if hit_id in clustered_ids:
                continue
            # Use rrf_score as similarity proxy -- it's what hybrid_search returns
            score = hit.get("rrf_score", hit.get("similarity", 0.0))
            if score >= similarity_threshold:
                cluster.append(hit)
                cluster_ids.add(hit_id)

        if len(cluster) >= min_cluster_size:
            clusters.append(cluster)
            clustered_ids.update(cluster_ids)

    return clusters


def _dominant_category(cluster: List[Dict]) -> str:
    """Return the most common category in a cluster, defaulting to detail."""
    cats = [m.get("category", "detail") for m in cluster]
    counter = Counter(cats)
    return counter.most_common(1)[0][0]


def _infer_project(cluster: List[Dict]) -> str:
    """Best-effort project name from sources in the cluster."""
    sources = [m.get("source", "") for m in cluster]
    for s in sources:
        parts = s.split("/")
        if len(parts) > 1 and parts[-1]:
            return parts[-1]
        if parts and parts[0]:
            return parts[0]
    return "unknown"


def consolidate_cluster(
    provider,
    engine,
    cluster: List[Dict],
    dry_run: bool = True,
) -> Dict[str, Any]:
    """LLM-merge a cluster of similar memories into 1-2 concise facts.

    Args:
        provider: LLMProvider instance with complete() method.
        engine: MemoryEngine instance for add/delete operations.
        cluster: List of memory dicts to consolidate.
        dry_run: If True, return what would happen without mutating.

    Returns:
        Dict with merged_count, new_count, old_ids, new_texts, dry_run.
    """
    old_ids = [m["id"] for m in cluster]
    project = _infer_project(cluster)
    category = _dominant_category(cluster)

    # Build prompt
    memories_for_prompt = [
        {"id": m["id"], "text": m["text"], "category": m.get("category", "DETAIL")}
        for m in cluster
    ]

    prompt = CONSOLIDATION_PROMPT.format(
        n=len(cluster),
        project=project,
        memories_json=json.dumps(memories_for_prompt, indent=2),
    )

    # Call LLM
    result = provider.complete(
        system="You are a memory consolidation assistant. Output only valid JSON.",
        user=prompt,
    )

    # Parse response
    try:
        new_texts = json.loads(result.text)
        if not isinstance(new_texts, list):
            new_texts = [str(new_texts)]
        new_texts = [str(t) for t in new_texts]
    except (json.JSONDecodeError, TypeError):
        # Fallback: treat entire response as a single consolidated memory
        new_texts = [result.text.strip()]

    if not dry_run:
        # Delete old memories
        engine.delete_memories(old_ids)

        # Determine source from first cluster member
        source = cluster[0].get("source", "consolidated")

        # Add consolidated memories
        metadata_list = [
            {"category": category, "consolidated_from": old_ids}
            for _ in new_texts
        ]
        engine.add_memories(
            texts=new_texts,
            sources=[source] * len(new_texts),
            metadata_list=metadata_list,
        )

    return {
        "merged_count": len(cluster),
        "new_count": len(new_texts),
        "old_ids": old_ids,
        "new_texts": new_texts,
        "dry_run": dry_run,
    }


def find_prune_candidates(
    all_memories: List[Dict],
    unretrieved_ids: List[int],
    detail_days: int = 60,
    decision_days: int = 120,
) -> List[Dict]:
    """Identify stale, unretrieved memories that are candidates for pruning.

    Args:
        all_memories: Full list of memory dicts.
        unretrieved_ids: IDs of memories that have never been retrieved.
        detail_days: Age threshold in days for DETAIL category memories.
        decision_days: Age threshold in days for DECISION/LEARNING category memories.

    Returns:
        List of memory dicts that exceed their category's age threshold
        and have never been retrieved.
    """
    unretrieved_set = set(unretrieved_ids)
    now = datetime.now(timezone.utc)
    candidates = []

    for mem in all_memories:
        if not mem:
            continue
        mem_id = mem.get("id")
        if mem_id is None:
            continue
        if mem_id not in unretrieved_set:
            continue

        # Parse creation time
        created_str = mem.get("created_at") or mem.get("timestamp")
        if not created_str:
            continue

        try:
            created = _parse_datetime(created_str)
        except (ValueError, TypeError):
            continue

        # Ensure created is timezone-aware for comparison
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        age_days = (now - created).days

        # Determine threshold based on category (lowercase from llm_extract.py)
        category = mem.get("category", "detail").lower()
        if category in _LONG_LIVED_CATEGORIES:
            threshold = decision_days
        else:
            threshold = detail_days

        if age_days > threshold:
            candidates.append(mem)

    return candidates
