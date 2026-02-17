"""Memory extraction pipeline with AUDN (Add/Update/Delete/Noop).

Two-call pipeline:
  1. LLM extracts atomic facts from conversation
  2. LLM (or novelty check for Ollama) decides AUDN action per fact

Usage:
  result = run_extraction(provider, engine, messages, source, context)
"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# --- Prompts ---

FACT_EXTRACTION_PROMPT = """Extract atomic facts worth remembering from this conversation.
Focus on: decisions made, preferences expressed, bugs found + root causes + fixes,
architectural choices, tool/library selections, project conventions.
Output a JSON array of strings, one fact per element.
Each fact should be self-contained and understandable without the conversation.
If nothing worth storing, output []."""

FACT_EXTRACTION_PROMPT_AGGRESSIVE = """Extract ALL potentially useful facts from this conversation.
This context is about to be lost, so be thorough. Include:
- Decisions made, preferences expressed
- Bugs found + root causes + fixes
- Architectural choices, tool/library selections
- Project conventions, file paths mentioned
- Any technical detail that might be useful later
Output a JSON array of strings, one fact per element.
Each fact should be self-contained and understandable without the conversation.
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


def extract_facts(provider, messages: str, context: str = "stop") -> list[str]:
    """Extract atomic facts from conversation using LLM.

    Args:
        provider: LLM provider instance
        messages: conversation text
        context: "stop", "pre_compact", or "session_end"

    Returns: list of fact strings
    """
    if context == "pre_compact":
        system = FACT_EXTRACTION_PROMPT_AGGRESSIVE
    else:
        system = FACT_EXTRACTION_PROMPT

    try:
        response = provider.complete(system, messages)
        facts = _parse_json_array(response)
        # Filter to strings only
        facts = [f for f in facts if isinstance(f, str) and f.strip()]
        logger.info("Extracted %d facts (context=%s)", len(facts), context)
        return facts
    except Exception as e:
        logger.error("Fact extraction failed: %s", e)
        return []


def run_audn(provider, engine, facts: list[str], source: str) -> list[dict]:
    """Run AUDN cycle on extracted facts.

    For providers with supports_audn=True: uses LLM to decide action per fact.
    For Ollama (supports_audn=False): uses engine.is_novel() for ADD/NOOP only.

    Returns: list of action dicts
    """
    if not facts:
        return []

    if not provider.supports_audn:
        # Ollama fallback: novelty check only
        decisions = []
        for i, fact in enumerate(facts):
            is_new = engine.is_novel(fact, threshold=0.88)
            if is_new:
                decisions.append({"action": "ADD", "fact_index": i})
            else:
                decisions.append({"action": "NOOP", "fact_index": i})
        return decisions

    # Full AUDN with LLM
    similar_per_fact = {}
    for i, fact in enumerate(facts):
        try:
            results = engine.hybrid_search(fact, k=5)
            similar_per_fact[i] = results
        except Exception:
            similar_per_fact[i] = []

    facts_json = json.dumps([{"index": i, "text": f} for i, f in enumerate(facts)], indent=2)
    similar_json = json.dumps({
        str(i): [{"id": m.get("id"), "text": m.get("text"), "similarity": round(m.get("similarity", 0), 3)}
                 for m in mems[:5]]
        for i, mems in similar_per_fact.items()
    }, indent=2)

    prompt = AUDN_PROMPT.format(facts_json=facts_json, similar_json=similar_json)

    try:
        response = provider.complete("You are a memory manager. Output only valid JSON.", prompt)
        decisions = _parse_json_array(response)
        valid = []
        for d in decisions:
            if isinstance(d, dict) and "action" in d:
                d["action"] = d["action"].upper()
                valid.append(d)
        return valid
    except Exception as e:
        logger.error("AUDN cycle failed: %s", e)
        return [{"action": "ADD", "fact_index": i} for i in range(len(facts))]


def execute_actions(engine, actions: list[dict], facts: list[str], source: str) -> dict:
    """Execute AUDN decisions against the memory engine."""
    stored_count = 0
    updated_count = 0
    deleted_count = 0
    result_actions = []

    for action in actions:
        act = action.get("action", "").upper()
        fact_idx = action.get("fact_index", 0)
        fact_text = facts[fact_idx] if fact_idx < len(facts) else ""

        try:
            if act == "ADD":
                ids = engine.add_memories(
                    texts=[fact_text],
                    source=source,
                    deduplicate=True
                )
                new_id = ids.get("ids", [None])[0]
                result_actions.append({"action": "add", "text": fact_text, "id": new_id})
                stored_count += 1

            elif act == "UPDATE":
                old_id = action.get("old_id")
                new_text = action.get("new_text", fact_text)
                if old_id is not None:
                    engine.delete_memory(old_id)
                ids = engine.add_memories(
                    texts=[new_text],
                    source=source,
                    deduplicate=False,
                    metadata={"supersedes": old_id}
                )
                new_id = ids.get("ids", [None])[0]
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
    facts = extract_facts(provider, messages, context=context)
    if not facts:
        return {
            "actions": [],
            "extracted_count": 0,
            "stored_count": 0,
            "updated_count": 0,
            "deleted_count": 0,
        }

    # Step 2: AUDN decisions
    decisions = run_audn(provider, engine, facts, source)

    # Step 3: Execute
    result = execute_actions(engine, decisions, facts, source)
    result["extracted_count"] = len(facts)

    logger.info(
        "Extraction complete: %d extracted, %d stored, %d updated, %d deleted",
        len(facts), result["stored_count"], result["updated_count"], result.get("deleted_count", 0)
    )

    return result
