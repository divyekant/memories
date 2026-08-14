"""Consolidation and pruning module for memory management.

Finds redundant memory clusters, merges them via LLM, and identifies
stale unused memories for cleanup.
"""

import json
import logging
import random
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List

from project_memory import TrustedAuthorship, is_reserved_namespace_source

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
    max_candidates: int = 500,
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
        max_candidates: Maximum number of memories to scan (0 = unlimited).
            Prevents O(n) query storms on large collections.

    Returns:
        List of clusters, where each cluster is a list of memory dicts.
    """
    # Filter for memories matching source_prefix. Pinned and archived
    # memories are never consolidation material: pinned is operator-protected,
    # archived is supersede-chain history.
    candidates = []
    for m in engine.metadata:
        if not m:
            continue
        if m.get("pinned") or m.get("archived"):
            continue
        if source_prefix and not m.get("source", "").startswith(source_prefix):
            continue
        candidates.append(m)

    if not candidates:
        return []

    # Cap candidates to avoid O(n) query storms on large collections
    if max_candidates > 0 and len(candidates) > max_candidates:
        logger.info(
            "Consolidation: capping %d candidates to %d (max_candidates)",
            len(candidates), max_candidates,
        )
        candidates = random.sample(candidates, max_candidates)

    logger.info("Consolidation: scanning %d candidates for clusters", len(candidates))

    clustered_ids: set = set()
    clusters: List[List[Dict]] = []
    searched = 0

    for mem in candidates:
        mem_id = mem["id"]
        if mem_id in clustered_ids:
            continue

        # Search for similar memories. Vector-only search: cosine similarity
        # lives on a 0-1 scale that similarity_threshold (0.75) was written
        # for. hybrid_search returns RRF rank-fusion scores structurally
        # bounded near 1/60, so comparing those against 0.75 meant no cluster
        # could ever form.
        seed_source = mem.get("source", "")
        search_kwargs = {"query": mem["text"], "k": 10}
        # Structured records are exact isolation domains. Legacy client
        # sources keep historical cross-client consolidation, but may never
        # absorb a reserved person/project record.
        if is_reserved_namespace_source(seed_source):
            search_kwargs["source_exact"] = seed_source
        if source_prefix:
            search_kwargs["source_prefix"] = source_prefix
        similar = engine.search(**search_kwargs)
        searched += 1

        # Build cluster: start with the seed memory
        cluster = [mem]
        cluster_ids = {mem_id}

        for hit in similar:
            hit_id = hit["id"]
            if hit_id == mem_id:
                continue
            if hit_id in clustered_ids:
                continue
            if hit.get("pinned") or hit.get("archived"):
                continue
            hit_source = hit.get("source", "")
            if is_reserved_namespace_source(seed_source):
                if hit_source != seed_source:
                    continue
            elif is_reserved_namespace_source(hit_source):
                continue
            score = hit.get("similarity", 0.0)
            if score >= similarity_threshold:
                cluster.append(hit)
                cluster_ids.add(hit_id)

        if len(cluster) >= min_cluster_size:
            clusters.append(cluster)
            clustered_ids.update(cluster_ids)

        # Log progress every 100 searches
        if searched % 100 == 0:
            logger.info("Consolidation progress: %d/%d searched, %d clusters found",
                        searched, len(candidates), len(clusters))

    logger.info("Consolidation complete: %d searched, %d clusters found", searched, len(clusters))
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
    sources = {m.get("source", "") for m in cluster}
    if len(sources) > 1 and any(
        is_reserved_namespace_source(source) for source in sources
    ):
        return {
            "merged_count": 0,
            "new_count": 0,
            "old_ids": old_ids,
            "new_texts": [],
            "dry_run": dry_run,
            "skipped_reason": "cluster contains structured memories from multiple exact sources",
        }
    protected = [m["id"] for m in cluster if m.get("pinned") or m.get("archived")]
    if protected:
        return {
            "merged_count": 0,
            "new_count": 0,
            "old_ids": old_ids,
            "new_texts": [],
            "dry_run": dry_run,
            "skipped_reason": f"cluster contains pinned/archived ids {protected}",
        }
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

    # Parse response. A response that is not a clean JSON array is REJECTED for
    # mutation: the old fallback stored the raw LLM text as a memory while the
    # originals were already deleted, so one malformed response could replace
    # real memories with garbage.
    parse_error = None
    try:
        from llm_extract import _parse_json_array
        new_texts = _parse_json_array(result.text)
    except Exception as e:
        new_texts = None
        parse_error = str(e)
    if not new_texts or not all(isinstance(t, str) and t.strip() for t in (str(t) for t in new_texts)):
        return {
            "merged_count": 0,
            "new_count": 0,
            "old_ids": old_ids,
            "new_texts": [],
            "dry_run": dry_run,
            "error": f"unparseable consolidation response{': ' + parse_error if parse_error else ''}",
        }
    new_texts = [str(t) for t in new_texts]

    if not dry_run:
        # Add the consolidated memories FIRST, delete originals only after the
        # add succeeded — never leave a window where the cluster is gone and
        # nothing replaced it.
        source = cluster[0].get("source", "consolidated")
        metadata_list = [
            {"category": category, "consolidated_from": old_ids}
            for _ in new_texts
        ]
        # Existing records have no cryptographically trustworthy way to prove
        # their authorship fields came from the new server boundary: every
        # possible plain metadata marker could also exist on a pre-upgrade
        # caller-controlled record. Preserve source IDs for traceability, but
        # do not inherit author/contributor labels during Phase 1 consolidation.
        trusted_authorship = TrustedAuthorship.system(
            source_memory_ids=old_ids,
        )
        replace_method = getattr(type(engine), "replace_consolidation_cluster", None)
        if callable(replace_method):
            mutation = replace_method(
                engine,
                cluster,
                new_texts,
                source,
                metadata_list,
                trusted_authorship,
            )
            mutation_error = mutation.get("error")
        else:
            # Compatibility for lightweight integrations and test doubles;
            # production MemoryEngine uses the locked transaction above.
            added = engine.add_memories(
                texts=new_texts,
                sources=[source] * len(new_texts),
                metadata_list=metadata_list,
                trusted_authorship=trusted_authorship,
            )
            mutation_error = None if added else "add_memories stored nothing; originals left untouched"
            if added:
                engine.delete_memories(old_ids)
        if mutation_error:
            return {
                "merged_count": 0,
                "new_count": 0,
                "old_ids": old_ids,
                "new_texts": new_texts,
                "dry_run": dry_run,
                "error": mutation_error,
            }

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
        # Pinned memories are operator-protected; archived memories are
        # supersede-chain version history. Neither is ever prunable.
        if mem.get("pinned") or mem.get("archived"):
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
