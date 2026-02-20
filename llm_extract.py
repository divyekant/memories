"""Memory extraction pipeline with AUDN (Add/Update/Delete/Noop).

Two-call pipeline:
  1. LLM extracts atomic facts from conversation
  2. LLM (or novelty check for Ollama) decides AUDN action per fact

Usage:
  result = run_extraction(provider, engine, messages, source, context)
"""
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """Parse integer env var with fallback and lower bound."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return max(minimum, default)
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("Invalid %s=%r; using default %d", name, raw, default)
        return max(minimum, default)


def _clip_text(text: str, max_chars: int) -> str:
    """Normalize whitespace and cap text length to reduce prompt bloat."""
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


EXTRACT_MAX_FACTS = _env_int("EXTRACT_MAX_FACTS", 30)
EXTRACT_MAX_FACT_CHARS = _env_int("EXTRACT_MAX_FACT_CHARS", 500, minimum=40)
EXTRACT_SIMILAR_TEXT_CHARS = _env_int("EXTRACT_SIMILAR_TEXT_CHARS", 280, minimum=40)
EXTRACT_SIMILAR_PER_FACT = _env_int("EXTRACT_SIMILAR_PER_FACT", 5)

# --- Prompts ---

FACT_EXTRACTION_PROMPT = """Extract durable facts worth remembering from this conversation about the {project} project.

Categorize each fact:
- DECISION: Architectural choices, library selections, design patterns, preferences. WHY something was chosen matters more than WHAT.
- LEARNING: Bug root causes + fixes, gotchas discovered, workarounds, performance findings.
- DETAIL: File paths, API signatures, config values that are project-specific conventions.

Skip anything that fails this test: "Would this still be useful 30 days from now?"

DO NOT extract:
- Task completion status ("done", "all tests pass", "deployed successfully")
- Commit hashes, PR numbers, or branch names
- Counts or metrics ("44 tests", "5 files changed")
- Session-specific context ("currently working on...", "next step is...")
- Generic programming knowledge any developer would know

Output a JSON array of objects: [{{"category": "DECISION"|"LEARNING"|"DETAIL", "text": "..."}}]
Each fact must be self-contained and understandable without the conversation.
If nothing worth storing, output []."""

FACT_EXTRACTION_PROMPT_AGGRESSIVE = """Extract durable facts worth remembering from this conversation about the {project} project.
This context is about to be lost permanently. Be thorough but still apply the 30-day test.

Categorize each fact:
- DECISION: Architectural choices, library selections, design patterns, preferences. WHY > WHAT.
- LEARNING: Bug root causes + fixes, gotchas discovered, workarounds, performance findings.
- DETAIL: File paths, API signatures, config values, naming conventions — project-specific patterns.

Include DETAIL-category items you would normally skip — file paths, config patterns, naming conventions.

DO NOT extract:
- Task completion status ("done", "all tests pass", "deployed successfully")
- Commit hashes, PR numbers, or branch names
- Counts or metrics ("44 tests", "5 files changed")
- Session-specific context ("currently working on...", "next step is...")
- Generic programming knowledge any developer would know

Output a JSON array of objects: [{{"category": "DECISION"|"LEARNING"|"DETAIL", "text": "..."}}]
Each fact must be self-contained and understandable without the conversation.
If nothing worth storing, output []."""

AUDN_PROMPT = """You are a memory manager. For each new fact, decide what to do given
the existing similar memories.

Actions:
- ADD: No similar memory exists. Store as new.
- UPDATE: An existing memory covers the same topic but the information
  has changed. Provide old_id and new_text that replaces it.
- DELETE: An existing memory is now contradicted or obsolete. Provide old_id.
- NOOP: The fact is already captured by an existing memory. Provide existing_id.

New facts:
{facts_json}

Existing similar memories (per fact):
{similar_json}

Output a JSON array of decisions. Each decision must have:
- "action": "ADD" | "UPDATE" | "DELETE" | "NOOP"
- "fact_index": index of the fact in the input array
- For UPDATE: "old_id" (int) and "new_text" (string)
- For DELETE: "old_id" (int)
- For NOOP: "existing_id" (int)"""


def _parse_json_array(text: str) -> list:
    """Parse a JSON array from LLM output, handling common edge cases."""
    text = text.strip()
    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass
    # Try extracting JSON from markdown code blocks
    if "```" in text:
        for block in text.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            try:
                result = json.loads(block)
                if isinstance(result, list):
                    return result
            except json.JSONDecodeError:
                continue
    # Try finding array in text
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            result = json.loads(text[start:end + 1])
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass
    return []


def extract_facts(
    provider,
    messages: str,
    context: str = "stop",
    return_error: bool = False,
    source: str = "",
):
    """Extract categorized facts from conversation using LLM.

    Args:
        provider: LLM provider instance
        messages: conversation text
        context: "stop", "pre_compact", or "session_end"
        source: memory source identifier (e.g. "claude-code/my-app")

    Returns:
        list[dict], or tuple[list[dict], Optional[str], dict] when return_error=True.
        Each dict has {"category": str, "text": str}.
    """
    if context == "pre_compact":
        system = FACT_EXTRACTION_PROMPT_AGGRESSIVE
    else:
        system = FACT_EXTRACTION_PROMPT

    project = source.rsplit("/", 1)[-1] if "/" in source else source or "this"
    system = system.format(project=project)

    tokens = {"input": 0, "output": 0}
    try:
        result = provider.complete(system, messages)
        raw_facts = _parse_json_array(result.text)
        tokens = {"input": result.input_tokens, "output": result.output_tokens}

        facts = []
        for item in raw_facts:
            if isinstance(item, dict) and "text" in item:
                # New format: {"category": "...", "text": "..."}
                cat = item.get("category", "detail").lower()
                if cat not in ("decision", "learning", "detail"):
                    cat = "detail"
                text = _clip_text(str(item["text"]), EXTRACT_MAX_FACT_CHARS)
                if text:
                    facts.append({"category": cat, "text": text})
            elif isinstance(item, str) and item.strip():
                # Backward compat: plain string -> detail
                text = _clip_text(item, EXTRACT_MAX_FACT_CHARS)
                if text:
                    facts.append({"category": "detail", "text": text})

        if len(facts) > EXTRACT_MAX_FACTS:
            logger.info(
                "Extracted %d facts; keeping first %d",
                len(facts), EXTRACT_MAX_FACTS,
            )
            facts = facts[:EXTRACT_MAX_FACTS]

        logger.info("Extracted %d facts (context=%s)", len(facts), context)
        if return_error:
            return facts, None, tokens
        return facts
    except Exception as e:
        logger.error("Fact extraction failed: %s", e)
        if return_error:
            return [], str(e), tokens
        return []


def run_audn(provider, engine, facts: list[dict], source: str) -> tuple[list[dict], dict]:
    """Run AUDN cycle on extracted facts.

    For providers with supports_audn=True: uses LLM to decide action per fact.
    For Ollama (supports_audn=False): uses engine.is_novel() for ADD/NOOP only.

    Args:
        facts: list of {"category": str, "text": str} dicts

    Returns: (list of action dicts, token usage dict)
    """
    if not facts:
        return [], {"input": 0, "output": 0}

    if not provider.supports_audn:
        # Ollama fallback: novelty check only
        decisions = []
        for i, fact in enumerate(facts):
            fact_text = fact["text"] if isinstance(fact, dict) else str(fact)
            is_new, _ = engine.is_novel(fact_text, threshold=0.88)
            if is_new:
                decisions.append({"action": "ADD", "fact_index": i})
            else:
                decisions.append({"action": "NOOP", "fact_index": i})
        return decisions, {"input": 0, "output": 0}

    # Full AUDN with LLM
    similar_per_fact = {}
    for i, fact in enumerate(facts):
        fact_text = fact["text"] if isinstance(fact, dict) else str(fact)
        try:
            results = engine.hybrid_search(fact_text, k=EXTRACT_SIMILAR_PER_FACT)
            similar_per_fact[i] = results
        except Exception:
            similar_per_fact[i] = []

    facts_json = json.dumps(
        [{"index": i, "text": _clip_text(f["text"], EXTRACT_MAX_FACT_CHARS), "category": f.get("category", "detail")} for i, f in enumerate(facts)],
        separators=(",", ":"),
    )
    similar_json = json.dumps(
        {
            str(i): [
                {
                    "id": m.get("id"),
                    "text": _clip_text(str(m.get("text", "")), EXTRACT_SIMILAR_TEXT_CHARS),
                    "similarity": round(float(m.get("similarity", 0.0)), 3),
                }
                for m in mems[:EXTRACT_SIMILAR_PER_FACT]
            ]
            for i, mems in similar_per_fact.items()
        },
        separators=(",", ":"),
    )

    prompt = AUDN_PROMPT.format(facts_json=facts_json, similar_json=similar_json)

    try:
        result = provider.complete("You are a memory manager. Output only valid JSON.", prompt)
        tokens = {"input": result.input_tokens, "output": result.output_tokens}
        decisions = _parse_json_array(result.text)
        del result, prompt, facts_json, similar_json, similar_per_fact
        valid = []
        for d in decisions:
            if isinstance(d, dict) and "action" in d:
                d["action"] = d["action"].upper()
                valid.append(d)
        return valid, tokens
    except Exception as e:
        logger.error("AUDN cycle failed: %s", e)
        return [{"action": "ADD", "fact_index": i} for i in range(len(facts))], {"input": 0, "output": 0}


def execute_actions(engine, actions: list[dict], facts: list[dict], source: str) -> dict:
    """Execute AUDN decisions against the memory engine.

    Args:
        facts: list of {"category": str, "text": str} dicts
    """
    stored_count = 0
    updated_count = 0
    deleted_count = 0
    result_actions = []

    for action in actions:
        act = action.get("action", "").upper()
        fi = action.get("fact_index", -1)
        fact = facts[fi] if 0 <= fi < len(facts) else {"text": "", "category": "detail"}
        fact_text = fact["text"] if isinstance(fact, dict) else str(fact)

        try:
            if act == "ADD":
                fact_meta = {"category": fact.get("category", "detail")} if isinstance(fact, dict) else {}
                added_ids = engine.add_memories(
                    texts=[fact_text],
                    sources=[source],
                    metadata_list=[fact_meta],
                    deduplicate=True,
                )
                new_id = added_ids[0] if added_ids else None
                result_actions.append({"action": "add", "text": fact_text, "id": new_id})
                stored_count += 1

            elif act == "UPDATE":
                old_id = action.get("old_id")
                new_text = action.get("new_text", fact_text)
                if old_id is not None:
                    engine.delete_memory(old_id)
                fact_meta = {"category": fact.get("category", "detail"), "supersedes": old_id} if isinstance(fact, dict) else {"supersedes": old_id}
                added_ids = engine.add_memories(
                    texts=[new_text],
                    sources=[source],
                    metadata_list=[fact_meta],
                    deduplicate=False,
                )
                new_id = added_ids[0] if added_ids else None
                result_actions.append({"action": "update", "old_id": old_id, "text": new_text, "new_id": new_id})
                updated_count += 1

            elif act == "DELETE":
                old_id = action.get("old_id")
                if old_id is not None:
                    engine.delete_memory(old_id)
                    result_actions.append({"action": "delete", "old_id": old_id})
                    deleted_count += 1

            elif act == "NOOP":
                existing_id = action.get("existing_id")
                result_actions.append({"action": "noop", "text": fact_text, "existing_id": existing_id})

        except Exception as e:
            logger.error("Failed to execute %s for fact '%s': %s", act, fact_text[:50], e)
            result_actions.append({"action": "error", "text": fact_text, "error": str(e)})

    return {
        "actions": result_actions,
        "stored_count": stored_count,
        "updated_count": updated_count,
        "deleted_count": deleted_count,
    }


def run_extraction(
    provider: Optional[object],
    engine,
    messages: str,
    source: str,
    context: str = "stop"
) -> dict:
    """Full extraction pipeline: extract facts -> AUDN -> execute.

    Args:
        provider: LLM provider (None = extraction disabled)
        engine: MemoryEngine instance
        messages: conversation text
        source: memory source identifier
        context: "stop", "pre_compact", or "session_end"

    Returns: result dict with actions and counts
    """
    if provider is None:
        return {"error": "extraction_disabled"}

    # Step 1: Extract facts
    facts, extract_error, extract_tokens = extract_facts(
        provider,
        messages,
        context=context,
        return_error=True,
        source=source,
    )
    if extract_error:
        return {
            "actions": [],
            "extracted_count": 0,
            "stored_count": 0,
            "updated_count": 0,
            "deleted_count": 0,
            "error": "provider_runtime_failure",
            "error_stage": "extract_facts",
            "error_message": extract_error,
            "tokens": {"extract": extract_tokens, "audn": {"input": 0, "output": 0}},
        }

    if not facts:
        return {
            "actions": [],
            "extracted_count": 0,
            "stored_count": 0,
            "updated_count": 0,
            "deleted_count": 0,
            "tokens": {"extract": extract_tokens, "audn": {"input": 0, "output": 0}},
        }

    # Step 2: AUDN decisions
    decisions, audn_tokens = run_audn(provider, engine, facts, source)

    # Step 3: Execute
    result = execute_actions(engine, decisions, facts, source)
    result["extracted_count"] = len(facts)
    result["tokens"] = {"extract": extract_tokens, "audn": audn_tokens}

    logger.info(
        "Extraction complete: %d extracted, %d stored, %d updated, %d deleted",
        len(facts), result["stored_count"], result["updated_count"], result.get("deleted_count", 0)
    )

    return result
