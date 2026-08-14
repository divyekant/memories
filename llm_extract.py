"""Memory extraction pipeline with AUDN (Add/Update/Delete/Noop).

Two-call pipeline:
  1. LLM extracts atomic facts from conversation
  2. LLM (or novelty check for Ollama) decides AUDN action per fact

Usage:
  result = run_extraction(provider, engine, messages, source, context)
"""
import json
import hashlib
import logging
import os
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, List

from auth_context import source_matches_prefixes
from shadow_runner import build_shadow_providers, fanout_shadow_async
from transcript_hygiene import clean_transcript, redact_secrets
from project_memory import (
    is_reserved_namespace_source,
    parse_memory_source,
    ProjectMemoryPolicyError,
    TrustedAuthorship,
    validate_project_write,
)
from project_promotion import (
    PromotionConfig,
    PromotionContext,
    PromotionMode,
    PromotionProposal,
    PromotionState,
    PromotionStatus,
    load_promotion_config,
    parse_proposal,
    promotion_state_from_memory,
    select_review_route,
)

logger = logging.getLogger(__name__)

TRAINING_DATA_DIR = os.environ.get("EXTRACT_TRAINING_DATA_DIR", "").strip()
_TRAINING_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50MB rotation threshold


def _promotion_is_active(promotion_context: Optional[PromotionContext]) -> bool:
    return (
        isinstance(promotion_context, PromotionContext)
        and promotion_context.effective_mode is not PromotionMode.OFF
    )


def _promotion_config_for_extraction() -> Optional[PromotionConfig]:
    """Load the measured route policy without widening a failed-closed run."""
    try:
        config = load_promotion_config()
    except (TypeError, ValueError):
        return None
    if config.relevance_threshold is None:
        return None
    return config


def _promotion_proposal(
    fact: object,
    promotion_context: Optional[PromotionContext],
) -> Optional[PromotionProposal]:
    """Parse only server-versioned proposal fields from one extracted fact."""
    if not _promotion_is_active(promotion_context) or not isinstance(fact, dict):
        return None
    proposal_fields = {
        key: fact.get(key)
        for key in (
            "project_relevance",
            "visibility",
            "assertion_status",
            "project_kind",
            "confidence",
            "reason",
        )
    }
    # The provider cannot choose the policy identity.  The active context is
    # the only source of the classifier version persisted on a candidate.
    proposal_fields["classifier_version"] = promotion_context.classifier_version
    if any(value is None for value in proposal_fields.values()):
        return None
    return parse_proposal(proposal_fields)


def _promotion_evidence_fingerprint(messages: str) -> str:
    """Hash in-flight evidence; never persist the conversation itself."""
    return hashlib.sha256(messages.encode("utf-8")).hexdigest()


def _promotion_route(
    proposal: PromotionProposal,
    *,
    recent_audit_count: int = 0,
) -> Optional[str]:
    config = _promotion_config_for_extraction()
    if config is None:
        return None
    # The scheduler/reconciler owns the durable audit-period counter.  During
    # capture, an empty cohort safely fills the configured fixed floor.
    return select_review_route(
        proposal,
        recent_audit_count=recent_audit_count,
        config=config,
    )


def _build_promotion_state(
    promotion_context: PromotionContext,
    proposal: Optional[PromotionProposal],
    route: Optional[str],
    evidence_fingerprint: str,
    *,
    status: PromotionStatus,
) -> PromotionState:
    return PromotionState(
        status=status,
        owner=promotion_context.principal_id,
        project_id=promotion_context.project_id,
        declaration_fingerprint=promotion_context.declaration_fingerprint,
        classifier_provider=promotion_context.classifier_provider,
        classifier_model=promotion_context.classifier_model,
        reviewer_provider=promotion_context.reviewer_provider,
        reviewer_model=promotion_context.reviewer_model,
        capture_mode=promotion_context.effective_mode,
        route=route,
        proposal=proposal,
        review=None,
        evidence_fingerprint=evidence_fingerprint,
        captured_at=datetime.now(timezone.utc).isoformat(),
    )


def _promotion_state_for_fact(
    fact: object,
    promotion_context: Optional[PromotionContext],
    promotion_active: bool,
    evidence_fingerprint: str,
    recent_audit_count: int,
) -> tuple[Optional[PromotionState], Optional[str], int]:
    """Build private or candidate state for one active extraction fact."""
    if not promotion_active or promotion_context is None:
        return None, None, recent_audit_count
    proposal = _promotion_proposal(fact, promotion_context)
    route = (
        _promotion_route(proposal, recent_audit_count=recent_audit_count)
        if proposal is not None
        else None
    )
    status = PromotionStatus.CANDIDATE if route is not None else PromotionStatus.PRIVATE
    state = _build_promotion_state(
        promotion_context,
        proposal,
        route,
        evidence_fingerprint,
        status=status,
    )
    return state, route, recent_audit_count


def _promotion_failure_state(state: PromotionState) -> PromotionState:
    return replace(
        state,
        status=PromotionStatus.FAILED,
        attempt_count=state.attempt_count + 1,
    )


def _invoke_promotion_callback(
    callback: Optional[Callable],
    engine,
    candidates: list[dict],
    evidence: dict,
    trusted_authorship: Optional[TrustedAuthorship],
) -> Optional[str]:
    """Run review handoff after private writes; failures leave them private."""
    if callback is None or not candidates:
        return None
    try:
        callback(candidates, evidence)
        return None
    except Exception as exc:
        logger.warning("Promotion callback failed after private capture: %s", exc)
        for candidate in candidates:
            candidate_id = candidate.get("candidate_id")
            if candidate_id is None:
                continue
            try:
                current = engine.get_memory(candidate_id)
                state = promotion_state_from_memory(current)
                if state is None:
                    continue
                failed = _promotion_failure_state(state)
                engine.update_memory(
                    candidate_id,
                    trusted_authorship=trusted_authorship,
                    trusted_promotion=failed,
                )
            except Exception as update_exc:
                logger.warning(
                    "Unable to mark promotion callback failure for candidate %s: %s",
                    candidate_id,
                    update_exc,
                )
        return str(exc)


def _with_trusted_authorship(
    kwargs: dict, trusted_authorship: Optional[TrustedAuthorship]
) -> dict:
    """Preserve legacy engine call shapes when no request identity is present."""
    if trusted_authorship is not None:
        kwargs["trusted_authorship"] = trusted_authorship
    return kwargs


def _with_trusted_promotion(
    kwargs: dict, trusted_promotion: Optional[PromotionState]
) -> dict:
    """Attach server-owned promotion state only for internal engine calls."""
    if trusted_promotion is not None:
        kwargs["trusted_promotion"] = trusted_promotion
    return kwargs


def _promotion_context_matches_source(
    promotion_context: Optional[PromotionContext],
    source: str,
    allowed_prefixes: Optional[List[str]],
    trusted_authorship: Optional[TrustedAuthorship],
) -> bool:
    if not _promotion_is_active(promotion_context):
        return False
    parsed = parse_memory_source(source)
    if parsed is None or not parsed.is_person or parsed.kind != "knowledge":
        return False
    if (
        parsed.project_id != promotion_context.project_id
        or parsed.principal_id != promotion_context.principal_id
    ):
        return False
    if trusted_authorship is None or trusted_authorship.author != promotion_context.principal_id:
        return False
    if allowed_prefixes is not None:
        private_ok = source_matches_prefixes(source, allowed_prefixes)
        project_ok = source_matches_prefixes(
            f"project/{promotion_context.project_id}/knowledge", allowed_prefixes
        )
        if not private_ok or not project_ok:
            return False
    return True


def _training_output_path(out_dir: Path, now: datetime) -> Path:
    """Return the rotated JSONL output path for passive training data."""
    path = out_dir / f"extraction-training-{now.strftime('%Y-%m-%d')}.jsonl"
    try:
        if path.stat().st_size > _TRAINING_MAX_FILE_BYTES:
            seq = 1
            while True:
                rotated = out_dir / f"extraction-training-{now.strftime('%Y-%m-%d')}-{seq}.jsonl"
                if not rotated.exists():
                    return rotated
                seq += 1
    except OSError:
        pass
    return path


def _build_extraction_system_prompt(
    source: str,
    context: str,
    rules: dict | None = None,
    promotion_context: Optional[PromotionContext] = None,
) -> str:
    """Build the exact extraction system prompt used for a request."""
    template = FACT_EXTRACTION_PROMPT_AGGRESSIVE if context == "pre_compact" else FACT_EXTRACTION_PROMPT
    project = source.rsplit("/", 1)[-1] if "/" in source else source or "this"
    prompt = template.format(project=project)
    rules_section = _build_rules_section(rules)
    if rules_section:
        prompt = prompt + "\n\n" + rules_section
    if _promotion_is_active(promotion_context):
        prompt += PROMOTION_EXTRACTION_INSTRUCTIONS
    return prompt


def _compact_similar_memories(similar_per_fact: dict) -> dict[str, list[dict]]:
    """Reduce similar-memory payloads to the fields needed for fine-tuning."""
    compact: dict[str, list[dict]] = {}
    for idx, mems in similar_per_fact.items():
        compact[str(idx)] = [
            {
                "id": m.get("id"),
                "text": _clip_text(str(m.get("text", "")), EXTRACT_SIMILAR_TEXT_CHARS),
                "source": m.get("source", ""),
                "relevance": round(_mem_score(m), 6),
            }
            for m in mems[:EXTRACT_SIMILAR_PER_FACT]
        ]
    return compact


def _save_training_pair(
    messages: str,
    facts: list[dict],
    source: str,
    context: str,
    *,
    audn_prompt: str | None = None,
    audn_system: str | None = None,
    audn_decisions: list[dict] | None = None,
    similar_per_fact: dict | None = None,
    extract_tokens: dict | None = None,
    audn_tokens: dict | None = None,
    rules: dict | None = None,
    promotion_context: Optional[PromotionContext] = None,
) -> None:
    """Persist extraction and optional AUDN supervision for future fine-tuning.

    Best-effort only: failures are logged at debug level and never raise.
    """
    if not TRAINING_DATA_DIR:
        return
    try:
        out_dir = Path(TRAINING_DATA_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        path = _training_output_path(out_dir, now)
        record = {
            "input": messages,
            "output": facts,
            "source": source,
            "context": context,
            "ts": now.isoformat(),
            "extraction": {
                "system": _build_extraction_system_prompt(
                    source, context, rules, promotion_context
                ),
                "user": messages,
                "assistant": facts,
            },
        }
        if extract_tokens is not None:
            record["extraction"]["tokens"] = extract_tokens
        if audn_prompt is not None and audn_system is not None and audn_decisions is not None:
            audn_record = {
                "system": audn_system,
                "user": audn_prompt,
                "assistant": audn_decisions,
            }
            if similar_per_fact is not None:
                audn_record["similar_memories"] = _compact_similar_memories(similar_per_fact)
            if audn_tokens is not None:
                audn_record["tokens"] = audn_tokens
            record["audn"] = audn_record
        payload = json.dumps(record, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(payload + "\n")
    except Exception as e:
        logger.debug("Training data save failed (non-fatal): %s", e)


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


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    """Parse float env var with fallback and lower bound."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return max(minimum, default)
    try:
        return max(minimum, float(raw))
    except ValueError:
        logger.warning("Invalid %s=%r; using default %s", name, raw, default)
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
EXTRACT_MAX_LINKS = _env_int("EXTRACT_MAX_LINKS", 3, minimum=0)
EXTRACT_MIN_LINK_SCORE = _env_float("EXTRACT_MIN_LINK_SCORE", 0.005)

_NOVELTY_GATE_DEFAULT_THRESHOLD = 0.85


def _novelty_gate_enabled() -> bool:
    """EXTRACT_NOVELTY_GATE — explicit novelty gate on extraction ADDs (default on).

    Read dynamically (not at import) so operators and tests can toggle it
    without reloading the module.
    """
    raw = os.environ.get("EXTRACT_NOVELTY_GATE", "").strip().lower()
    if not raw:
        return True
    return raw not in ("0", "false", "no", "off")


def _novelty_gate_threshold() -> float:
    """EXTRACT_NOVELTY_THRESHOLD — cosine similarity above which an ADD is gated."""
    raw = os.environ.get("EXTRACT_NOVELTY_THRESHOLD", "").strip()
    if not raw:
        return _NOVELTY_GATE_DEFAULT_THRESHOLD
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "Invalid EXTRACT_NOVELTY_THRESHOLD=%r; using %s",
            raw, _NOVELTY_GATE_DEFAULT_THRESHOLD,
        )
        return _NOVELTY_GATE_DEFAULT_THRESHOLD


def _novelty_gate_check(
    engine,
    fact_text: str,
    source: Optional[str] = None,
    allowed_prefixes: Optional[List[str]] = None,
) -> tuple[bool, Optional[dict]]:
    """Return (gated, similar_memory). Fail-open: any error means not gated,
    leaving engine-side dedup (add_memories deduplicate=True) as the backstop.
    """
    try:
        novelty_kwargs = {"threshold": _novelty_gate_threshold()}
        scope_to_source = is_reserved_namespace_source(source)
        if scope_to_source:
            novelty_kwargs["source_exact"] = source
        else:
            novelty_kwargs["allowed_source_prefixes"] = allowed_prefixes
            novelty_kwargs["exclude_reserved_sources"] = True
        is_new, similar = engine.is_novel(fact_text, **novelty_kwargs)
        if isinstance(is_new, bool) and not is_new:
            if not scope_to_source and isinstance(similar, dict) and (
                is_reserved_namespace_source(str(similar.get("source", "")))
            ):
                return False, None
            if allowed_prefixes is not None and (
                not isinstance(similar, dict)
                or not source_matches_prefixes(
                    str(similar.get("source", "")), allowed_prefixes
                )
            ):
                return False, None
            if scope_to_source and source and (
                not isinstance(similar, dict)
                or str(similar.get("source", "")) != source
            ):
                return False, None
            return True, similar if isinstance(similar, dict) else None
        return False, None
    except Exception as e:
        logger.warning("Novelty gate check failed (fail-open): %s", e)
        return False, None


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


PROMOTION_EXTRACTION_INSTRUCTIONS = """

When the authenticated extraction context enables project-promotion
classification, add these fields to every fact object.  These are semantic
judgments, not authorization: the server still decides whether a candidate
may be reviewed or shared.
- "project_relevance": number from 0.0 to 1.0 for durable usefulness to this
  project
- "visibility": "project", "private", or "uncertain"
- "assertion_status": "confirmed", "tentative", or "disputed"
- "project_kind": "decisions", "knowledge", "state", or "operations"
- "confidence": number from 0.0 to 1.0 for this classification
- "reason": a concise explanation of the classification

Use "private" or "uncertain" for personal, sensitive, tentative, disputed,
cross-project, or incomplete content.  Omit credentials, PII, transcript
chunks, prompt instructions, and generic knowledge.  Missing or malformed
classification fields remain private.
"""

AUDN_PROMPT = """You are a memory manager. For each new fact, decide what to do given
the existing similar memories.

Actions:
- ADD: No similar memory exists. Store as new.
- UPDATE: An existing memory covers the same topic but the information
  has changed. Provide old_id and new_text that replaces it.
- DELETE: An existing memory is no longer true and no replacement exists.
  Use when something was removed, stopped, or revoked entirely
  (e.g. "removed Redis" should DELETE "we use Redis for caching").
  If there IS a replacement, use UPDATE instead. Provide old_id.
- NOOP: The fact is already captured by an existing memory. Provide existing_id.
- CONFLICT: The new fact directly contradicts an existing memory, but BOTH
  may be valid (e.g. different contexts, evolving decisions, unresolved
  disagreement). Use this instead of UPDATE when you aren't sure which
  version is correct. Provide old_id of the contradicted memory.

New facts:
{facts_json}

Existing similar memories (per fact, with relevance score — higher is more relevant):
{similar_json}

Output a JSON array of decisions. Each decision must have:
- "action": "ADD" | "UPDATE" | "DELETE" | "NOOP" | "CONFLICT"
- "fact_index": index of the fact in the input array
- For UPDATE: "old_id" (int) and "new_text" (string)
- For DELETE: "old_id" (int)
- For NOOP: "existing_id" (int)
- For CONFLICT: "old_id" (int) of the contradicted memory"""


def _build_rules_section(rules: dict | None) -> str:
    """Build a prompt section from extraction rules."""
    if not rules:
        return ""
    parts = ["## Project-Specific Rules"]
    always = rules.get("always_remember", [])
    if always:
        parts.append("ALWAYS remember these types of information:")
        for item in always:
            parts.append(f"  - {item}")
    never = rules.get("never_remember", [])
    if never:
        parts.append("NEVER remember these types of information:")
        for item in never:
            parts.append(f"  - {item}")
    custom = rules.get("custom_instructions", "")
    if custom:
        parts.append(f"Additional instructions: {custom}")
    return "\n".join(parts)


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
    rules: dict | None = None,
    promotion_context: Optional[PromotionContext] = None,
):
    """Extract categorized facts from conversation using LLM.

    Args:
        provider: LLM provider instance
        messages: conversation text
        context: "stop", "pre_compact", or "session_end"
        source: memory source identifier (e.g. "claude-code/my-app")
        rules: extraction rules (always_remember/never_remember/custom_instructions)
            appended to the system prompt so the bar applies at extraction time

    Returns:
        list[dict], or tuple[list[dict], Optional[str], dict] when return_error=True.
        Each dict has {"category": str, "text": str}.
    """
    system = _build_extraction_system_prompt(
        source, context, rules, promotion_context
    )

    tokens = {"input": 0, "output": 0}
    try:
        result = provider.complete(system, messages)
        try:
            shadows = build_shadow_providers()
            if shadows:
                fanout_shadow_async(
                    call_type="extract",
                    system=system,
                    user=messages,
                    primary_text=result.text,
                    source=source,
                    shadows=shadows,
                    log_dir=os.environ.get("SHADOW_LOG_DIR", "/tmp"),
                )
        except Exception as _shadow_err:
            logger.warning("Shadow fan-out (extract) suppressed: %s", _shadow_err)
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
                    fact = {"category": cat, "text": text}
                    if _promotion_is_active(promotion_context):
                        for key in (
                            "project_relevance",
                            "visibility",
                            "assertion_status",
                            "project_kind",
                            "confidence",
                            "reason",
                        ):
                            if key in item:
                                fact[key] = item[key]
                    facts.append(fact)
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


def run_audn(
    provider,
    engine,
    facts: list[dict],
    source: str,
    allowed_prefixes: Optional[List[str]] = None,
    debug: bool = False,
    rules: dict | None = None,
) -> tuple[list[dict], dict, dict]:
    """Run AUDN cycle on extracted facts.

    For providers with supports_audn=True: uses LLM to decide action per fact.
    For providers with supports_audn=False: uses engine.is_novel() for ADD/NOOP only.

    Args:
        facts: list of {"category": str, "text": str} dicts
        debug: when True, return similar memories per fact in debug_info

    Returns: (list of action dicts, token usage dict, audn_artifacts dict with similar_per_fact always present)
    """
    if not facts:
        return [], {"input": 0, "output": 0}, {"similar_per_fact": {}}

    scope_to_source = is_reserved_namespace_source(source)

    if not provider.supports_audn:
        # Ollama fallback: novelty check only
        decisions = []
        for i, fact in enumerate(facts):
            fact_text = fact["text"] if isinstance(fact, dict) else str(fact)
            novelty_kwargs = {"threshold": 0.88}
            if scope_to_source and source:
                novelty_kwargs["source_exact"] = source
            else:
                novelty_kwargs["allowed_source_prefixes"] = allowed_prefixes
                novelty_kwargs["exclude_reserved_sources"] = True
            is_new, similar = engine.is_novel(fact_text, **novelty_kwargs)
            if (
                not scope_to_source
                and not is_new
                and isinstance(similar, dict)
                and (
                    is_reserved_namespace_source(str(similar.get("source", "")))
                    or (
                        allowed_prefixes is not None
                        and not source_matches_prefixes(
                            str(similar.get("source", "")), allowed_prefixes
                        )
                    )
                )
            ):
                is_new = True
            if is_new:
                decisions.append({"action": "ADD", "fact_index": i})
            else:
                decisions.append({"action": "NOOP", "fact_index": i})
        return decisions, {"input": 0, "output": 0}, {"similar_per_fact": {}}

    # Full AUDN with LLM
    similar_per_fact = {}
    for i, fact in enumerate(facts):
        fact_text = fact["text"] if isinstance(fact, dict) else str(fact)
        try:
            search_kwargs = {"k": EXTRACT_SIMILAR_PER_FACT}
            if scope_to_source and source:
                search_kwargs["source_exact"] = source
            results = engine.hybrid_search(fact_text, **search_kwargs)
            # Defense in depth for engines/providers that do not enforce the
            # exact-source filter themselves.
            if scope_to_source and source:
                results = [
                    r for r in results
                    if str(r.get("source", "")) == source
                ]
            if allowed_prefixes is not None:
                results = [
                    r for r in results
                    if source_matches_prefixes(str(r.get("source", "")), allowed_prefixes)
                ]
            if not scope_to_source:
                results = [
                    r for r in results
                    if not is_reserved_namespace_source(str(r.get("source", "")))
                ]
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
                    "relevance": round(_mem_score(m), 3),
                }
                for m in mems[:EXTRACT_SIMILAR_PER_FACT]
            ]
            for i, mems in similar_per_fact.items()
        },
        separators=(",", ":"),
    )

    prompt = AUDN_PROMPT.format(facts_json=facts_json, similar_json=similar_json)
    rules_section = _build_rules_section(rules)
    if rules_section:
        prompt = prompt + "\n\n" + rules_section
    audn_system = "You are a memory manager. Output only valid JSON."
    audn_artifacts = {
        "similar_per_fact": dict(similar_per_fact),
        "training_prompt": {
            "system": audn_system,
            "user": prompt,
        },
    }
    if debug:
        audn_artifacts["debug_similar"] = {
            i: [
                {
                    "id": m.get("id"),
                    "text": _clip_text(str(m.get("text", "")), EXTRACT_SIMILAR_TEXT_CHARS),
                    "similarity": round(float(m.get("similarity", m.get("rrf_score", 0.0))), 4),
                }
                for m in mems[:EXTRACT_SIMILAR_PER_FACT]
            ]
            for i, mems in similar_per_fact.items()
        }

    try:
        result = provider.complete(audn_system, prompt)
        try:
            shadows = build_shadow_providers()
            if shadows:
                fanout_shadow_async(
                    call_type="audn",
                    system=audn_system,
                    user=prompt,
                    primary_text=result.text,
                    source=source,
                    shadows=shadows,
                    log_dir=os.environ.get("SHADOW_LOG_DIR", "/tmp"),
                )
        except Exception as _shadow_err:
            logger.warning("Shadow fan-out (audn) suppressed: %s", _shadow_err)
        tokens = {"input": result.input_tokens, "output": result.output_tokens}
        decisions = _parse_json_array(result.text)
        del result, prompt, facts_json, similar_json
        valid = []
        for d in decisions:
            if isinstance(d, dict) and "action" in d:
                d["action"] = d["action"].upper()
                valid.append(d)
        return valid, tokens, audn_artifacts
    except Exception as e:
        logger.error("AUDN cycle failed: %s", e)
        return [{"action": "FALLBACK_ADD", "fact_index": i} for i in range(len(facts))], {"input": 0, "output": 0}, audn_artifacts


SINGLE_CALL_PROMPT = """You are a memory extraction and classification system.

Given conversation text, extract important durable facts.

IMPORTANT: You have no access to existing memories, so you can only ADD new facts
or NOOP facts that are generic knowledge. Do NOT use UPDATE, DELETE, or CONFLICT
— those require existing memory context that is not available in single-call mode.

For each fact, output a JSON object with:
- "action": "ADD" or "NOOP"
- "fact_index": sequential index starting at 0
- "category": "decision" | "learning" | "detail"
- "text": the extracted fact text

Categories:
- DECISION: architectural choices, technology selections, trade-off resolutions
- LEARNING: non-obvious findings, gotchas, performance insights
- DETAIL: specific configs, paths, versions, API signatures

Rules:
- Skip generic programming knowledge
- Skip task status / commit hashes / counts
- Skip ephemeral session context
- ADD for new durable facts worth remembering long-term
- NOOP if the fact is already commonly known or too ephemeral

{rules_section}

Output ONLY a JSON array of action objects. No markdown, no explanation."""


def extract_and_decide_single_call(
    provider,
    messages: str,
    source: str,
    engine,
    rules: dict | None = None,
    max_facts: int = 30,
    promotion_context: Optional[PromotionContext] = None,
) -> tuple[list[dict], dict, None]:
    """Extract facts AND decide AUDN actions in a single LLM call.
    ~50% cost reduction, less accurate (no per-fact similar-memory lookup).
    Returns: (actions, token_usage, None) — same shape as run_audn().
    """
    rules_section = _build_rules_section(rules)
    prompt = SINGLE_CALL_PROMPT.format(rules_section=rules_section)
    if _promotion_is_active(promotion_context):
        prompt += PROMOTION_EXTRACTION_INSTRUCTIONS

    user_prompt = f"Extract and classify facts from this conversation:\n\n{messages[:max_facts * 500]}"
    result = provider.complete(system=prompt, user=user_prompt)
    try:
        shadows = build_shadow_providers()
        if shadows:
            fanout_shadow_async(
                call_type="single_call",
                system=prompt,
                user=user_prompt,
                primary_text=result.text,
                source=source,
                shadows=shadows,
                log_dir=os.environ.get("SHADOW_LOG_DIR", "/tmp"),
            )
    except Exception as _shadow_err:
        logger.warning("Shadow fan-out (single_call) suppressed: %s", _shadow_err)
    usage = {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens}

    try:
        actions = _parse_json_array(result.text)
    except Exception:
        actions = []

    for i, action in enumerate(actions[:max_facts]):
        action.setdefault("fact_index", i)
        action.setdefault("action", "ADD")
        action.setdefault("category", "detail")
        if "text" not in action:
            action["text"] = ""
        # Single-call has no existing-memory context, so force ADD/NOOP only.
        # If the model returns UPDATE/DELETE/CONFLICT anyway, demote to ADD.
        if action["action"] not in ("ADD", "NOOP"):
            action["action"] = "ADD"

    return actions[:max_facts], usage, None


def execute_actions(
    engine,
    actions: list[dict],
    facts: list[dict],
    source: str,
    allowed_prefixes: Optional[List[str]] = None,
    job_id: Optional[str] = None,
    document_at: Optional[str] = None,
    novelty_gate: bool = True,
    trusted_authorship: Optional[TrustedAuthorship] = None,
    promotion_context: Optional[PromotionContext] = None,
    evidence_fingerprint: Optional[str] = None,
) -> dict:
    """Execute AUDN decisions against the memory engine.

    Produces exactly one result action per input action, maintaining positional
    correspondence. _apply_maintenance() depends on this invariant.

    Args:
        facts: list of {"category": str, "text": str} dicts
        job_id: extraction job identifier for provenance tracking
        document_at: ISO 8601 timestamp for when the source content was created
        novelty_gate: apply the EXTRACT_NOVELTY_GATE check to ADD/FALLBACK_ADD
            actions (near-duplicates become noops). Callers executing
            human-approved actions (e.g. dry-run commit) pass False.
    """
    stored_count = 0
    updated_count = 0
    deleted_count = 0
    conflict_count = 0
    fallback_count = 0
    gated_count = 0
    result_actions = []
    promotion_candidates: list[dict] = []
    recent_audit_count = 0
    gate_active = novelty_gate and _novelty_gate_enabled()
    promotion_active = _promotion_context_matches_source(
        promotion_context,
        source,
        allowed_prefixes,
        trusted_authorship,
    )
    if evidence_fingerprint is None:
        evidence_fingerprint = hashlib.sha256(
            json.dumps(facts, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    # Project policy errors are terminal for extraction commits (the API
    # returns 422), so validate every action that can create replacement text
    # before executing any of them.  Without this pass, a later invalid action
    # could reject the request only after earlier actions had already mutated
    # storage.
    for action in actions:
        act = action.get("action", "").upper()
        if act not in ("ADD", "FALLBACK_ADD", "UPDATE", "CONFLICT"):
            continue
        fi = action.get("fact_index", -1)
        fact = facts[fi] if 0 <= fi < len(facts) else {"text": ""}
        fact_text = fact.get("text", "") if isinstance(fact, dict) else str(fact)
        candidate_text = action.get("new_text", fact_text) if act == "UPDATE" else fact_text
        validate_project_write(candidate_text, source, trusted_authorship)

    for action in actions:
        act = action.get("action", "").upper()
        fi = action.get("fact_index", -1)
        fact = facts[fi] if 0 <= fi < len(facts) else {"text": "", "category": "detail"}
        fact_text = fact["text"] if isinstance(fact, dict) else str(fact)

        try:
            if act in ("ADD", "FALLBACK_ADD"):
                if not source_matches_prefixes(source, allowed_prefixes):
                    raise PermissionError(f"source not authorized for add: {source}")
                if gate_active:
                    gated, similar = _novelty_gate_check(
                        engine, fact_text, source, allowed_prefixes
                    )
                    if gated:
                        result_actions.append({
                            "action": "noop",
                            "text": fact_text,
                            "reason": "novelty_gate",
                            "existing_id": (similar or {}).get("id"),
                            "similarity": (similar or {}).get("similarity"),
                        })
                        gated_count += 1
                        continue
                fact_meta = {"category": fact.get("category", "detail")} if isinstance(fact, dict) else {}
                if job_id:
                    fact_meta["extraction_job_id"] = job_id
                    fact_meta["extract_source"] = source
                if document_at:
                    fact_meta["document_at"] = document_at
                promotion_state, route, recent_audit_count = _promotion_state_for_fact(
                    fact,
                    promotion_context,
                    promotion_active,
                    evidence_fingerprint,
                    recent_audit_count,
                )
                add_kwargs = {
                    "texts": [fact_text],
                    "sources": [source],
                    "metadata_list": [fact_meta],
                    "deduplicate": True,
                }
                added_ids = engine.add_memories(
                    **_with_trusted_promotion(
                        _with_trusted_authorship(add_kwargs, trusted_authorship),
                        promotion_state,
                    )
                )
                new_id = added_ids[0] if added_ids else None
                result_actions.append({"action": "fallback_add" if act == "FALLBACK_ADD" else "add", "text": fact_text, "id": new_id})
                stored_count += 1
                if act == "FALLBACK_ADD":
                    fallback_count += 1
                if (
                    new_id is not None
                    and promotion_state is not None
                    and promotion_state.status is PromotionStatus.CANDIDATE
                    and route is not None
                ):
                    promotion_candidates.append(
                        {"candidate_id": new_id, "fact_index": fi, "route": route}
                    )
                    if route == "audit":
                        recent_audit_count += 1

            elif act == "UPDATE":
                old_id = action.get("old_id")
                existing = None
                if old_id is not None:
                    existing = engine.get_memory(old_id)
                    existing_source = str(existing.get("source", ""))
                    if existing_source != source:
                        raise PermissionError(f"old_id not authorized for update: {old_id}")
                    if existing and (existing.get("pinned") is True or existing.get("archived") is True):
                        result_actions.append({"action": "skipped", "reason": "protected", "old_id": old_id})
                        continue
                new_text = action.get("new_text", fact_text)
                if old_id is not None and allowed_prefixes is not None:
                    if not source_matches_prefixes(existing_source, allowed_prefixes):
                        raise PermissionError(f"old_id not authorized for update: {old_id}")
                if not source_matches_prefixes(source, allowed_prefixes):
                    raise PermissionError(f"source not authorized for update: {source}")
                promotion_state, route, recent_audit_count = _promotion_state_for_fact(
                    fact,
                    promotion_context,
                    promotion_active,
                    evidence_fingerprint,
                    recent_audit_count,
                )
                if old_id is not None:
                    validate_project_write(new_text, source, trusted_authorship)
                    if not promotion_active:
                        # Preserve the legacy extraction mutation order when
                        # no typed promotion context is active.
                        archive_kwargs = {
                            "archived": True,
                            "metadata_patch": {"is_latest": False},
                        }
                        engine.update_memory(
                            old_id,
                            **_with_trusted_authorship(archive_kwargs, trusted_authorship),
                        )
                fact_meta = {"category": fact.get("category", "detail"), "supersedes": old_id, "is_latest": True} if isinstance(fact, dict) else {"supersedes": old_id, "is_latest": True}
                if job_id:
                    fact_meta["extraction_job_id"] = job_id
                    fact_meta["extract_source"] = source
                if document_at:
                    fact_meta["document_at"] = document_at
                add_kwargs = {
                    "texts": [new_text],
                    "sources": [source],
                    "metadata_list": [fact_meta],
                    "deduplicate": False,
                }
                added_ids = engine.add_memories(
                    **_with_trusted_promotion(
                        _with_trusted_authorship(add_kwargs, trusted_authorship),
                        promotion_state,
                    )
                )
                new_id = added_ids[0] if added_ids else None
                if old_id is not None and promotion_active and new_id is not None:
                    # In active promotion mode the replacement is committed
                    # with its candidate state before the prior version is
                    # archived.  Legacy callers retain the historical order.
                    archive_kwargs = {
                        "archived": True,
                        "metadata_patch": {"is_latest": False},
                    }
                    engine.update_memory(
                        old_id,
                        **_with_trusted_authorship(archive_kwargs, trusted_authorship),
                    )
                # Create supersedes link from new → old
                if new_id and old_id is not None:
                    try:
                        engine.add_link(new_id, old_id, "supersedes")
                    except (ValueError, Exception):
                        pass  # Link creation is non-fatal
                result_actions.append({"action": "update", "old_id": old_id, "text": new_text, "new_id": new_id})
                updated_count += 1
                if (
                    new_id is not None
                    and promotion_state is not None
                    and promotion_state.status is PromotionStatus.CANDIDATE
                    and route is not None
                ):
                    promotion_candidates.append(
                        {"candidate_id": new_id, "fact_index": fi, "route": route}
                    )
                    if route == "audit":
                        recent_audit_count += 1

            elif act == "DELETE":
                old_id = action.get("old_id")
                existing = None
                if old_id is not None:
                    existing = engine.get_memory(old_id)
                    existing_source = str(existing.get("source", ""))
                    if existing_source != source:
                        raise PermissionError(f"old_id not authorized for delete: {old_id}")
                    if existing and (existing.get("pinned") is True or existing.get("archived") is True):
                        result_actions.append({"action": "skipped", "reason": "protected", "old_id": old_id})
                        continue
                if old_id is not None:
                    if allowed_prefixes is not None:
                        if not source_matches_prefixes(existing_source, allowed_prefixes):
                            raise PermissionError(f"old_id not authorized for delete: {old_id}")
                    engine.delete_memory(old_id)
                    result_actions.append({"action": "delete", "old_id": old_id})
                    deleted_count += 1

            elif act == "CONFLICT":
                old_id = action.get("old_id")
                if not source_matches_prefixes(source, allowed_prefixes):
                    raise PermissionError(f"source not authorized for add: {source}")
                fact_meta = {"category": fact.get("category", "detail")} if isinstance(fact, dict) else {}
                if job_id:
                    fact_meta["extraction_job_id"] = job_id
                    fact_meta["extract_source"] = source
                if document_at:
                    fact_meta["document_at"] = document_at
                if old_id is not None:
                    existing = engine.get_memory(old_id)
                    existing_source = str(existing.get("source", ""))
                    if existing_source != source:
                        raise PermissionError(f"old_id not authorized for conflict: {old_id}")
                    if allowed_prefixes is not None:
                        if not source_matches_prefixes(existing_source, allowed_prefixes):
                            raise PermissionError(f"old_id not authorized for conflict: {old_id}")
                    fact_meta["conflicts_with"] = old_id
                promotion_state, route, recent_audit_count = _promotion_state_for_fact(
                    fact,
                    promotion_context,
                    promotion_active,
                    evidence_fingerprint,
                    recent_audit_count,
                )
                add_kwargs = {
                    "texts": [fact_text],
                    "sources": [source],
                    "metadata_list": [fact_meta],
                    "deduplicate": False,
                }
                added_ids = engine.add_memories(
                    **_with_trusted_promotion(
                        _with_trusted_authorship(add_kwargs, trusted_authorship),
                        promotion_state,
                    )
                )
                new_id = added_ids[0] if added_ids else None
                result_actions.append({
                    "action": "conflict",
                    "text": fact_text,
                    "id": new_id,
                    "conflicts_with": old_id,
                })
                stored_count += 1
                conflict_count += 1
                if (
                    new_id is not None
                    and promotion_state is not None
                    and promotion_state.status is PromotionStatus.CANDIDATE
                    and route is not None
                ):
                    promotion_candidates.append(
                        {"candidate_id": new_id, "fact_index": fi, "route": route}
                    )
                    if route == "audit":
                        recent_audit_count += 1

            elif act == "NOOP":
                existing_id = action.get("existing_id")
                result_actions.append({"action": "noop", "text": fact_text, "existing_id": existing_id})

        except ProjectMemoryPolicyError:
            raise
        except Exception as e:
            logger.error("Failed to execute %s for fact '%s': %s", act, fact_text[:50], e)
            result_actions.append({"action": "error", "text": fact_text, "error": str(e)})

    if gated_count:
        logger.info("Novelty gate suppressed %d near-duplicate add(s)", gated_count)

    return {
        "actions": result_actions,
        "stored_count": stored_count,
        "updated_count": updated_count,
        "deleted_count": deleted_count,
        "conflict_count": conflict_count,
        "fallback_count": fallback_count,
        "gated_count": gated_count,
        "promotion_candidates": promotion_candidates,
    }


def _mem_score(m: dict) -> float:
    """Extract the relevance score from a memory dict (RRF or cosine fallback)."""
    return float(m.get("rrf_score", m.get("similarity", 0.0)))


def _apply_maintenance(
    engine,
    decisions: list[dict],
    exec_result: dict,
    audn_artifacts: dict,
    max_links: int = None,
    min_link_score: float = None,
    source: Optional[str] = None,
) -> dict:
    """Post-execution maintenance: auto-linking and compaction detection.

    Non-fatal: exceptions are caught and logged. Never rolls back execute_actions() results.
    Individual add_link() failures (deleted target, duplicate) are logged as warnings and skipped.
    """
    if max_links is None:
        max_links = EXTRACT_MAX_LINKS
    if min_link_score is None:
        min_link_score = EXTRACT_MIN_LINK_SCORE

    links_created = []
    compaction_candidates = []
    similar_per_fact = audn_artifacts.get("similar_per_fact", {})
    result_actions = exec_result.get("actions", [])

    # Collect IDs deleted in this batch (skip as link targets)
    deleted_ids = {
        a.get("old_id") for a in result_actions
        if a.get("action") == "delete" and a.get("old_id") is not None
    }

    # --- Auto-linking ---
    if max_links > 0:
        if len(decisions) != len(result_actions):
            logger.error(
                "decisions/actions length mismatch: %d vs %d; skipping auto-linking",
                len(decisions), len(result_actions),
            )
            max_links = 0

        for i, (decision, result_action) in enumerate(zip(decisions, result_actions)):
            act = result_action.get("action", "")
            if act not in ("add", "fallback_add", "conflict"):
                continue
            new_id = result_action.get("id")
            if new_id is None:
                continue

            fact_index = decision.get("fact_index", -1)
            similar = similar_per_fact.get(fact_index, [])

            scored = [
                m for m in similar
                if _mem_score(m) >= min_link_score
                and m.get("id") is not None
                and (source is None or str(m.get("source", "")) == source)
                and m["id"] not in deleted_ids
                and m["id"] != new_id
            ]
            scored.sort(key=_mem_score, reverse=True)
            targets = scored[:max_links]

            for target in targets:
                target_id = target["id"]
                rrf = _mem_score(target)
                try:
                    engine.add_link(new_id, target_id, "related_to")
                    links_created.append({"from_id": new_id, "to_id": target_id, "rrf_score": round(rrf, 6)})
                except ValueError as e:
                    logger.warning("Auto-link %d -> %d skipped: %s", new_id, target_id, e)
                except Exception as e:
                    logger.error("Auto-link %d -> %d failed: %s", new_id, target_id, e)

    if links_created:
        logger.info("Auto-linked %d edges during extraction", len(links_created))

    # --- Compaction detection ---
    for fact_idx, similar in similar_per_fact.items():
        if len(similar) < 3:
            continue
        scores = [
            (m.get("id"), _mem_score(m), m.get("source", ""))
            for m in similar
            if m.get("id") is not None
            and (source is None or str(m.get("source", "")) == source)
            and m.get("id") not in deleted_ids
        ]
        if len(scores) < 3:
            continue

        scores.sort(key=lambda x: x[1], reverse=True)
        best_cluster = []
        for start in range(len(scores) - 2):
            cluster = [scores[start]]
            for j in range(start + 1, len(scores)):
                if scores[start][1] > 0 and scores[j][1] / scores[start][1] >= 0.8:
                    cluster.append(scores[j])
                else:
                    break
            if len(cluster) >= 3 and len(cluster) > len(best_cluster):
                best_cluster = cluster

        if len(best_cluster) >= 3:
            memory_ids = [s[0] for s in best_cluster]
            sources = list({s[2] for s in best_cluster})
            source_families = {s.split("/")[0] for s in sources if "/" in s}
            avg_score = sum(s[1] for s in best_cluster) / len(best_cluster)
            compaction_candidates.append({
                "fact_index": fact_idx,
                "memory_ids": memory_ids,
                "avg_rrf_score": round(avg_score, 6),
                "sources": sorted(sources),
                "cross_source": len(source_families) > 1,
            })

    if compaction_candidates:
        logger.info("Compaction candidates detected: %d clusters", len(compaction_candidates))

    return {
        "links_created": links_created,
        "compaction_candidates": compaction_candidates,
    }


def run_extraction(
    provider: Optional[object],
    engine,
    messages: str,
    source: str,
    context: str = "stop",
    allowed_prefixes: Optional[List[str]] = None,
    debug: bool = False,
    profile: dict | None = None,
    document_at: Optional[str] = None,
    trusted_authorship: Optional[TrustedAuthorship] = None,
    promotion_context: Optional[PromotionContext] = None,
    promotion_callback: Optional[Callable] = None,
) -> dict:
    """Full extraction pipeline: extract facts -> AUDN -> execute.

    Args:
        provider: LLM provider (None = extraction disabled)
        engine: MemoryEngine instance
        messages: conversation text
        source: memory source identifier
        context: "stop", "pre_compact", or "session_end"
        debug: when True, include detailed debug_trace in result
        profile: resolved extraction profile (from ExtractionProfiles.resolve)

    Returns: result dict with actions and counts
    """
    if provider is None:
        return {"error": "extraction_disabled"}

    promotion_active = _promotion_is_active(promotion_context)

    # Transcript hygiene: hook-injected recalled memories, <system-reminder>
    # blocks, and hook additional-context blocks must never reach the
    # extraction LLM, or they get re-stored as new memories every session.
    messages = clean_transcript(messages)
    if os.environ.get("EXTRACT_REDACT_SECRETS", "true").strip().lower() not in ("0", "false", "no"):
        messages, _redacted_types = redact_secrets(messages)
        if _redacted_types:
            logger.info("Redacted credential-shaped content before extraction: %s", ", ".join(_redacted_types))
    if not messages:
        return {
            "actions": [],
            "extracted_count": 0,
            "stored_count": 0,
            "updated_count": 0,
            "deleted_count": 0,
            "skipped_reason": "empty_after_hygiene",
            "tokens": {"extract": {"input": 0, "output": 0}, "audn": {"input": 0, "output": 0}},
            "links_created": [],
            "compaction_candidates": [],
            "promotion_candidates": [],
        }

    job_id = uuid.uuid4().hex[:12]

    # Apply profile settings
    if profile:
        max_facts = profile.get("max_facts", 30)
        max_chars = profile.get("max_fact_chars", 500)
        mode = profile.get("mode", "standard")
        if mode == "aggressive":
            context = "pre_compact"
        rules = profile.get("rules", {})
    else:
        max_facts = 30
        max_chars = 500
        rules = {}

    # Single-call mode: combine extraction + AUDN in one LLM call
    if profile and profile.get("single_call"):
        actions, usage, _ = extract_and_decide_single_call(
            provider=provider,
            messages=messages,
            source=source,
            engine=engine,
            rules=rules,
            max_facts=max_facts,
            promotion_context=promotion_context,
        )
        facts = []
        for action in actions:
            fact = {
                "text": action.get("text", ""),
                "category": action.get("category", "detail"),
            }
            if promotion_active:
                for key in (
                    "project_relevance",
                    "visibility",
                    "assertion_status",
                    "project_kind",
                    "confidence",
                    "reason",
                ):
                    if key in action:
                        fact[key] = action[key]
            facts.append(fact)
        evidence_fingerprint = _promotion_evidence_fingerprint(messages)
        result = execute_actions(
            engine,
            actions,
            facts,
            source,
            allowed_prefixes,
            **_with_trusted_authorship(
                {"job_id": job_id, "document_at": document_at},
                trusted_authorship,
            ),
            promotion_context=promotion_context,
            evidence_fingerprint=evidence_fingerprint,
        )
        result["tokens"] = {"single_call": usage}
        result["job_id"] = job_id
        result["links_created"] = []
        result["compaction_candidates"] = []
        callback_error = _invoke_promotion_callback(
            promotion_callback,
            engine,
            result.get("promotion_candidates", []),
            {"messages": messages, "facts": facts, "source": source},
            trusted_authorship,
        )
        if callback_error is not None:
            result["promotion_callback_error"] = callback_error
        return result

    # Temporarily override module-level constants for extract_facts
    import llm_extract as _mod
    orig_max_facts = _mod.EXTRACT_MAX_FACTS
    orig_max_chars = _mod.EXTRACT_MAX_FACT_CHARS
    if profile:
        _mod.EXTRACT_MAX_FACTS = max_facts
        _mod.EXTRACT_MAX_FACT_CHARS = max_chars
    try:
        # Step 1: Extract facts
        facts, extract_error, extract_tokens = extract_facts(
            provider,
            messages,
            context=context,
            return_error=True,
            source=source,
            rules=rules,
            promotion_context=promotion_context,
        )
    finally:
        _mod.EXTRACT_MAX_FACTS = orig_max_facts
        _mod.EXTRACT_MAX_FACT_CHARS = orig_max_chars
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
            "promotion_candidates": [],
        }

    if not facts:
        return {
            "actions": [],
            "extracted_count": 0,
            "stored_count": 0,
            "updated_count": 0,
            "deleted_count": 0,
            "tokens": {"extract": extract_tokens, "audn": {"input": 0, "output": 0}},
            "promotion_candidates": [],
        }

    # Step 2: AUDN decisions
    decisions, audn_tokens, audn_artifacts = run_audn(
        provider,
        engine,
        facts,
        source,
        allowed_prefixes=allowed_prefixes,
        debug=debug,
        rules=rules,
    )

    # Passive training data collection (when EXTRACT_TRAINING_DATA_DIR is set)
    training_prompt = audn_artifacts.get("training_prompt", {})
    _save_training_pair(
        messages,
        facts,
        source,
        context,
        audn_prompt=training_prompt.get("user"),
        audn_system=training_prompt.get("system"),
        audn_decisions=decisions,
        similar_per_fact=audn_artifacts.get("similar_per_fact"),
        extract_tokens=extract_tokens,
        audn_tokens=audn_tokens,
        rules=rules,
        promotion_context=promotion_context,
    )

    # Step 3: Dry-run intercept — return planned actions without executing
    if profile and profile.get("dry_run"):
        # Attach fact info to each decision for caller inspection
        annotated = []
        for d in decisions:
            entry = dict(d)
            fi = d.get("fact_index", -1)
            if 0 <= fi < len(facts):
                entry["fact"] = facts[fi]
            annotated.append(entry)
        return {
            "dry_run": True,
            "actions": annotated,
            "extracted_count": len(facts),
            "tokens": {"extract": extract_tokens, "audn": audn_tokens},
            "links_created": [],
            "compaction_candidates": [],
            "promotion_candidates": [],
        }

    # Step 4: Execute
    result = execute_actions(
        engine,
        decisions,
        facts,
        source,
        **_with_trusted_authorship(
            {
                "allowed_prefixes": allowed_prefixes,
                "job_id": job_id,
                "document_at": document_at,
            },
            trusted_authorship,
        ),
        promotion_context=promotion_context,
        evidence_fingerprint=_promotion_evidence_fingerprint(messages),
    )
    result["extracted_count"] = len(facts)
    result["tokens"] = {"extract": extract_tokens, "audn": audn_tokens}
    result["job_id"] = job_id

    # Step 4b: Post-execution maintenance (auto-linking + compaction detection)
    try:
        maintenance = _apply_maintenance(
            engine, decisions, result, audn_artifacts, source=source
        )
        result["links_created"] = maintenance["links_created"]
        result["compaction_candidates"] = maintenance["compaction_candidates"]
    except Exception as e:
        logger.error("Extraction maintenance failed (non-fatal): %s", e)
        result["links_created"] = []
        result["compaction_candidates"] = []

    callback_error = _invoke_promotion_callback(
        promotion_callback,
        engine,
        result.get("promotion_candidates", []),
        {"messages": messages, "facts": facts, "source": source},
        trusted_authorship,
    )
    if callback_error is not None:
        result["promotion_callback_error"] = callback_error

    # Step 5: Build debug trace when requested
    debug_similar = audn_artifacts.get("debug_similar", {})
    if debug:
        # Build AUDN decisions trace with similar memories
        audn_trace = []
        for d in decisions:
            entry = {
                "fact_index": d.get("fact_index", -1),
                "action": d.get("action", "UNKNOWN"),
                "similar_memories": [],
            }
            fi = d.get("fact_index", -1)
            if fi in debug_similar:
                entry["similar_memories"] = debug_similar[fi]

            # Attach resulting IDs from execution
            for ra in result.get("actions", []):
                act = ra.get("action", "")
                if act in ("add", "fallback_add") and ra.get("text") == (facts[fi]["text"] if 0 <= fi < len(facts) else ""):
                    entry["new_id"] = ra.get("id")
                elif act == "update" and d.get("old_id") == ra.get("old_id"):
                    entry["old_id"] = ra.get("old_id")
                    entry["new_id"] = ra.get("new_id")
                elif act == "delete" and d.get("old_id") == ra.get("old_id"):
                    entry["old_id"] = ra.get("old_id")
                elif act == "noop" and ra.get("text") == (facts[fi]["text"] if 0 <= fi < len(facts) else ""):
                    entry["existing_id"] = ra.get("existing_id")
                elif act == "conflict" and ra.get("text") == (facts[fi]["text"] if 0 <= fi < len(facts) else ""):
                    entry["new_id"] = ra.get("id")
                    entry["conflicts_with"] = ra.get("conflicts_with")
            audn_trace.append(entry)

        # Build execution summary
        added_ids = [a.get("id") for a in result.get("actions", []) if a.get("action") in ("add", "fallback_add") and a.get("id") is not None]
        updated_entries = [{"old": a.get("old_id"), "new": a.get("new_id")} for a in result.get("actions", []) if a.get("action") == "update"]
        deleted_ids = [a.get("old_id") for a in result.get("actions", []) if a.get("action") == "delete" and a.get("old_id") is not None]
        noop_count = sum(1 for a in result.get("actions", []) if a.get("action") == "noop")
        conflict_count = sum(1 for a in result.get("actions", []) if a.get("action") == "conflict")

        result["debug_trace"] = {
            "extracted_facts": [
                {"text": f["text"], "category": f.get("category", "detail")}
                for f in facts
            ],
            "audn_decisions": audn_trace,
            "execution_summary": {
                "added": added_ids,
                "updated": updated_entries,
                "deleted": deleted_ids,
                "noops": noop_count,
                "conflicts": conflict_count,
            },
        }

    logger.info(
        "Extraction complete: %d extracted, %d stored, %d updated, %d deleted",
        len(facts), result["stored_count"], result["updated_count"], result.get("deleted_count", 0)
    )

    return result
