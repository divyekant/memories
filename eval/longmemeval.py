"""LongMemEval benchmark adapter for Memories engine."""

import json
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("eval.longmemeval")

# Pre-compiled pattern for extracting session index from source paths (e.g. ".../s42/c3")
_SESSION_INDEX_RE = re.compile(r"/s(\d+)/c\d+$")


@dataclass
class LongMemEvalResult:
    version: str = ""
    eval_mode: str = "tool"
    timestamp: str = ""
    judge: dict = field(default_factory=dict)
    overall: float = 0.0
    recall_any_at_5: float = 0.0
    categories: dict = field(default_factory=dict)
    recall_categories: dict = field(default_factory=dict)
    delta: dict = field(default_factory=dict)
    details: list = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, path: str) -> "LongMemEvalResult":
        with open(path) as f:
            data = json.load(f)
        # Filter to known fields to handle forward-compat with extra keys
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


def _normalize_longmemeval_date(raw: str) -> str:
    """Convert LongMemEval date format to ISO 8601.
    Input: '2023/05/20 (Sat) 02:21'
    Output: '2023-05-20T02:21:00+00:00'
    """
    try:
        # Strip day-of-week in parens: "2023/05/20 (Sat) 02:21" → "2023/05/20  02:21"
        clean = re.sub(r'\s*\([^)]*\)\s*', ' ', raw).strip()
        dt = datetime.strptime(clean, "%Y/%m/%d %H:%M")
        return dt.strftime("%Y-%m-%dT%H:%M:00+00:00")
    except Exception:
        return raw


LONGMEMEVAL_CATEGORIES = [
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
]


class LongMemEvalRunner:
    DEFAULT_MAX_MEMORY_CHARS = 3000
    DEFAULT_CONTEXT_RESULTS = 2

    def __init__(self, client, judge_provider="anthropic", judge_model=None):
        self.client = client
        self.judge_provider = judge_provider
        self.judge_model = judge_model
        self._judge = None

    def load_dataset(self, cache_dir="eval/scenarios/longmemeval/"):
        """Load LongMemEval dataset. Download from HuggingFace if not cached."""
        cache_path = Path(cache_dir)
        cache_path.mkdir(parents=True, exist_ok=True)
        data_file = cache_path / "longmemeval_s_cleaned.json"
        if not data_file.exists():
            self._download_dataset(data_file)
        return self._parse_dataset(data_file)

    def _download_dataset(self, dest: Path):
        """Download LongMemEval_s (cleaned) from HuggingFace."""
        import urllib.request
        url = "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json?download=true"
        urllib.request.urlretrieve(url, str(dest))

    def _parse_dataset(self, path: Path) -> list[dict]:
        """Parse JSON array or JSONL into list of question dicts."""
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            return [data]  # single object
        except json.JSONDecodeError:
            pass
        # Fallback for JSONL format
        items = []
        with open(path) as f:
            for line in f:
                if line.strip():
                    items.append(json.loads(line))
        return items

    @staticmethod
    def _question_id(question: dict) -> str:
        return str(question.get("question_id") or question.get("id") or "")

    @staticmethod
    def _question_category(question: dict) -> str:
        return (
            question.get("question_type")
            or question.get("category")
            or question.get("type")
            or "unknown"
        )

    def _question_prefix(self, question: dict, source_prefix: str) -> str:
        qid = self._question_id(question)
        return f"{source_prefix}/q{qid}" if qid else source_prefix

    @staticmethod
    def _split_text(prefix: str, text: str, max_chars: int) -> list[str]:
        """Split oversized turn text into bounded chunks."""
        available = max(1, max_chars - len(prefix))
        remaining = text.strip()
        chunks: list[str] = []
        while remaining:
            if len(remaining) <= available:
                chunks.append(prefix + remaining)
                break
            split_at = remaining.rfind(" ", 0, available + 1)
            if split_at <= 0:
                split_at = available
            piece = remaining[:split_at].rstrip()
            chunks.append(prefix + piece)
            remaining = remaining[split_at:].lstrip()
        return chunks

    def _render_turn_chunks(self, turn: dict, max_chars: int) -> list[str]:
        """Render one conversational turn into one or more bounded strings."""
        role = turn.get("role", "user")
        text = str(turn.get("content", turn.get("text", ""))).strip()
        if not text:
            return []
        prefix = f"{role}: "
        rendered = prefix + text
        if len(rendered) <= max_chars:
            return [rendered]
        return self._split_text(prefix, text, max_chars)

    def _chunk_session(self, session: list[dict], max_chars: int) -> list[str]:
        """Chunk a session on turn boundaries while respecting the API size limit."""
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for turn in session:
            for rendered in self._render_turn_chunks(turn, max_chars=max_chars):
                candidate_len = len(rendered) if not current else current_len + 2 + len(rendered)
                if current and candidate_len > max_chars:
                    chunks.append("\n\n".join(current))
                    current = [rendered]
                    current_len = len(rendered)
                else:
                    current.append(rendered)
                    current_len = candidate_len

        if current:
            chunks.append("\n\n".join(current))
        return chunks

    def seed_memories(
        self,
        conversations: list[dict],
        source_prefix: str = "eval/longmemeval",
        max_chars: int = DEFAULT_MAX_MEMORY_CHARS,
    ):
        """Store conversation chunks directly, scoped per session."""
        self.client.clear_by_prefix(source_prefix)
        memories = []
        for i, conv in enumerate(conversations):
            session_source = f"{source_prefix}/session_{i}"
            turns = conv.get("turns", conv.get("messages", []))
            for chunk_index, chunk in enumerate(self._chunk_session(turns, max_chars=max_chars)):
                memories.append(
                    {
                        "text": chunk,
                        "source": f"{session_source}/c{chunk_index}",
                        "metadata": {
                            "conversation_id": conv.get("id", i),
                            "chunk_index": chunk_index,
                        },
                    }
                )
        if memories:
            self.client.add_batch(memories, deduplicate=False)
        return len(memories)

    def seed_question(
        self,
        question: dict,
        source_prefix: str = "eval/longmemeval",
        max_chars: int = DEFAULT_MAX_MEMORY_CHARS,
    ) -> int:
        """Seed a single LongMemEval question's haystack sessions as direct memories."""
        question_prefix = self._question_prefix(question, source_prefix)
        qid = self._question_id(question)
        qtype = self._question_category(question)
        self.client.clear_by_prefix(question_prefix)

        # Get session dates for temporal grounding
        haystack_dates = question.get("haystack_dates", [])

        memories = []
        for session_index, session in enumerate(question.get("haystack_sessions", [])):
            # Inject session date as document_at for temporal reasoning
            session_date = haystack_dates[session_index] if session_index < len(haystack_dates) else None

            for chunk_index, chunk in enumerate(self._chunk_session(session, max_chars=max_chars)):
                if not chunk.strip():
                    continue
                metadata = {
                    "question_id": qid,
                    "question_type": qtype,
                    "session_index": session_index,
                    "chunk_index": chunk_index,
                }
                if session_date:
                    metadata["document_at"] = _normalize_longmemeval_date(session_date)

                memories.append(
                    {
                        "text": chunk,  # Don't prepend dates — hurts embedding retrieval
                        "source": f"{question_prefix}/s{session_index}/c{chunk_index}",
                        "metadata": metadata,
                    }
                )

        if not memories:
            return 0
        ids = self.client.add_batch(memories, deduplicate=False)
        return len(ids)

    def clear_question(self, question: dict, source_prefix: str = "eval/longmemeval") -> int:
        """Delete a question's scoped memories from the eval store."""
        return self.client.clear_by_prefix(self._question_prefix(question, source_prefix))

    @staticmethod
    def compute_recall_at_k(
        search_results: list[dict],
        question: dict,
        k: int = 5,
    ) -> dict:
        """Compute session-level recall@k matching MemPalace methodology.

        MemPalace stores one doc per session, so R@5 = "is the gold session in
        the top 5 docs?"  We chunk sessions into multiple memories, so we
        deduplicate to unique sessions (by first appearance) before checking.

        Returns dict with recall_any (binary), recall_all, and the unique
        session indices found in the top-k unique sessions.
        """
        # Build gold set: map answer_session_ids → session indices
        answer_sids = set(question.get("answer_session_ids", []))
        haystack_sids = question.get("haystack_session_ids", [])
        gold_indices = {
            i for i, sid in enumerate(haystack_sids) if sid in answer_sids
        }

        if not gold_indices:
            return {"recall_any": 0.0, "recall_all": 0.0, "top_sessions": []}

        # Extract unique session indices from results in rank order.
        # Each result has session_index in metadata (top-level key) or
        # can be parsed from the source path (…/s{idx}/c{chunk}).
        seen: set[int] = set()
        unique_sessions: list[int] = []
        for r in search_results:
            sidx = r.get("session_index")
            if sidx is None:
                # Fallback: parse from source path
                source = r.get("source", "")
                m = _SESSION_INDEX_RE.search(source)
                if m:
                    sidx = int(m.group(1))
            if sidx is not None and sidx not in seen:
                seen.add(sidx)
                unique_sessions.append(sidx)
                if len(unique_sessions) >= k:
                    break

        top_k_set = set(unique_sessions[:k])
        recall_any = float(any(g in top_k_set for g in gold_indices))
        recall_all = float(all(g in top_k_set for g in gold_indices))

        return {
            "recall_any": recall_any,
            "recall_all": recall_all,
            "top_sessions": unique_sessions[:k],
        }

    def run_question(self, question: dict, k: int = 5, source_prefix: str = "eval/longmemeval") -> dict:
        """Run retrieval for a single LongMemEval question against its scoped haystack."""
        query = str(question.get("question", ""))
        # Retrieve more results to get enough unique sessions for R@k
        retrieval_k = max(k, 50)
        # Pass question_date as reference for temporal intent detection
        ref_date = None
        raw_date = question.get("question_date", "")
        if raw_date:
            ref_date = _normalize_longmemeval_date(raw_date)
        search_kwargs = {
            "query": query,
            "k": retrieval_k,
            "hybrid": True,
            "source_prefix": self._question_prefix(question, source_prefix),
        }
        if ref_date is not None:
            search_kwargs["reference_date"] = ref_date
        search_results = self.client.search(**search_kwargs)
        context = "\n".join(
            r.get("text", "") for r in search_results[: self.DEFAULT_CONTEXT_RESULTS]
        )

        # Compute session-level recall metrics
        recall = self.compute_recall_at_k(search_results, question, k=5)

        return {
            "question_id": self._question_id(question),
            "category": self._question_category(question),
            "question": query,
            "expected": str(question.get("answer", "")),
            "context": context,
            "search_results": search_results,
            "eval_mode": "tool",
            "recall_any_at_5": recall["recall_any"],
            "recall_all_at_5": recall["recall_all"],
            "recall_top_sessions_at_5": recall["top_sessions"],
        }

    def run_question_system(
        self,
        question: dict,
        cc_executor,
        source_prefix: str = "eval/longmemeval",
        project_dir: str = "",
    ) -> dict:
        """Run a question through Claude Code with MCP tools (system eval mode).

        Instead of raw API search, this boots a Claude Code session with the
        Memories MCP server and lets the agent search, reason, and answer.

        If project_dir is provided, reuses it (caller manages lifecycle).
        If empty, creates and cleans up a temporary project per call.
        """
        query = str(question.get("question", ""))
        expected = str(question.get("answer", ""))
        qid = self._question_id(question)
        category = self._question_category(question)
        q_prefix = self._question_prefix(question, source_prefix)

        owns_project = not project_dir
        if owns_project:
            project_dir = cc_executor.create_isolated_project(with_memories=True)

        try:
            # Include question_date for temporal reasoning
            question_date = question.get("question_date", "")
            reference_date = _normalize_longmemeval_date(question_date) if question_date else ""
            date_context = (
                f"\nToday's date is: {question_date}\n"
                f"Use reference_date='{reference_date}' when calling memory_evidence or memory_search for relative temporal queries.\n"
                if question_date else ""
            )

            prompt = (
                f"You have access to memory_search, memory_evidence, memory_timeline, and other memory tools via MCP. "
                f"Use them to find relevant context, then answer the following question.\n\n"
                f"IMPORTANT: Search within the source prefix '{q_prefix}' to find relevant memories. "
                f"For temporal, latest/current, date range, or event-order questions, call memory_evidence first with this source_prefix. "
                f"For event-order, list, multi-event date math, or 'how many days/weeks ago did X when I Y' questions, call memory_timeline with this source_prefix as well; for user-history questions, call memory_timeline with user_facts_only=true. "
                f"Try multiple search queries if your first attempt doesn't find the answer. "
                f"Think about what keywords or phrases might be stored in memory.\n"
                f"Memory entries include dates in brackets like [2023/05/20 (Sat) 14:30]. "
                f"Use these dates and the evidence packet source/date trail for temporal calculations (how many days, which came first, etc.).\n"
                f"Temporal answer checklist:\n"
                f"- Identify the event date for each event from user-stated completed events before answering.\n"
                f"- For questions about things 'I took', 'I did', 'I bought', 'I visited', or similar user history, use user-stated completed events; do not count assistant-authored suggestions, itineraries, recommendations, or hypothetical plans as user facts unless a user message confirms the event happened.\n"
                f"- When a question asks 'how many days/weeks ago did X when I Y' or otherwise says 'when I ...', use the event date as the anchor for the 'ago' calculation, not the question date, even if the two events appear separate; do not include alternate interpretations in the final answer.\n"
                f"- If the question asks for weeks, months, or another coarse unit, round to the nearest whole requested unit unless it explicitly asks for exact days. Do not hedge with ranges when a single whole-unit answer is requested.\n"
                f"- For list/order questions asking for a number of events, gather exactly that many distinct user-confirmed events before answering. Deduplicate repeated mentions of the same event, and day hikes and outings count as trips when the question asks about trips.\n"
                f"- If multiple memories mention the same event with conflicting or vague timing, prefer direct dated user evidence for that event over incidental recency mentions like 'recently got back'.\n"
                f"- If the first result lacks a date, keep searching for the dated event evidence before saying the answer is unavailable.\n"
                f"{date_context}\n"
                f"Question: {query}\n\n"
                f"Provide a direct, concise answer based on what you find in memory. "
                f"If you cannot find the answer, say so clearly."
            )

            agent_response = cc_executor.run_prompt(prompt, project_dir)
            agent_trace = getattr(cc_executor, "last_run_trace", {}) or {}
            logger.debug("Agent response for Q%s: %s", qid, agent_response[:200])

            retrieval_k = 50
            search_kwargs = {
                "query": query,
                "k": retrieval_k,
                "hybrid": True,
                "source_prefix": q_prefix,
            }
            if question_date:
                search_kwargs["reference_date"] = _normalize_longmemeval_date(question_date)
            search_results = self.client.search(**search_kwargs)
            recall = self.compute_recall_at_k(search_results, question, k=5)

            return {
                "question_id": qid,
                "category": category,
                "question": query,
                "expected": expected,
                "context": agent_response,
                "search_results": search_results,
                "eval_mode": "system",
                "recall_any_at_5": recall["recall_any"],
                "recall_all_at_5": recall["recall_all"],
                "recall_top_sessions_at_5": recall["top_sessions"],
                "agent_trace": agent_trace,
            }
        finally:
            if owns_project:
                cc_executor.cleanup_project(project_dir)

    def run_questions(self, questions: list[dict], k: int = 5, source_prefix: str = "eval/longmemeval") -> list[dict]:
        """Run retrieval for multiple LongMemEval questions."""
        return [self.run_question(q, k=k, source_prefix=source_prefix) for q in questions]

    def judge_answers(self, results: list[dict]) -> list[dict]:
        """Score each answer using LLM judge."""
        if self._judge is None:
            self.init_judge()
        scored = []
        for r in results:
            score, reasoning = self._judge_single(r)
            scored.append({**r, "score": score, "reasoning": reasoning})
        return scored

    def init_judge(self):
        """Initialize LLM judge provider via environment-based factory."""
        from llm_provider import get_provider
        # get_provider reads EXTRACT_PROVIDER / EXTRACT_MODEL from env;
        # temporarily override so the judge uses the requested provider.
        old_provider = os.environ.get("EXTRACT_PROVIDER", "")
        old_model = os.environ.get("EXTRACT_MODEL", "")
        try:
            os.environ["EXTRACT_PROVIDER"] = self.judge_provider
            if self.judge_model:
                os.environ["EXTRACT_MODEL"] = self.judge_model
            self._judge = get_provider()
        finally:
            # Restore previous env
            if old_provider:
                os.environ["EXTRACT_PROVIDER"] = old_provider
            else:
                os.environ.pop("EXTRACT_PROVIDER", None)
            if old_model:
                os.environ["EXTRACT_MODEL"] = old_model
            else:
                os.environ.pop("EXTRACT_MODEL", None)

    @staticmethod
    def _parse_judge_response(text: str) -> tuple[float, str]:
        """Parse judge output, tolerating code fences and trailing prose."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:]) if len(lines) > 1 else ""
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        decoder = json.JSONDecoder()
        candidates = [cleaned]

        json_start = cleaned.find("{")
        if json_start >= 0:
            candidates.append(cleaned[json_start:])

        for candidate in candidates:
            if not candidate:
                continue
            try:
                data, _ = decoder.raw_decode(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return float(data.get("score", 0.0)), str(data.get("reasoning", ""))

        from eval.judge import _parse_response

        return _parse_response(cleaned)

    def _judge_single(self, result: dict) -> tuple[float, str]:
        """Score a single question-answer pair.

        In tool eval mode: judges whether retrieved context contains the answer.
        In system eval mode: judges whether the agent's response answers correctly.
        """
        eval_mode = result.get("eval_mode", "tool")

        if eval_mode == "system":
            system = (
                "You are evaluating whether an AI assistant correctly answered a question "
                "based on its memory of past conversations. Score 0.0-1.0 using this rubric:\n"
                "- 1.0: the assistant's response contains the correct answer.\n"
                "- 0.5: the response is partially correct or ambiguous.\n"
                "- 0.0: the response is wrong, says it doesn't know, or doesn't address the question.\n"
                "If the expected answer appears in the response (verbatim or paraphrased), score at least 0.95.\n"
                "Return ONLY a raw JSON object: {\"score\": <float>, \"reasoning\": \"<str>\"}"
            )
            user = (
                f"Question: {result['question']}\n"
                f"Expected answer: {result['expected']}\n"
                f"Assistant's response: {result['context']}\n"
                f"Did the assistant answer the question correctly?"
            )
        else:
            system = (
                "You are evaluating whether retrieved memory context is sufficient to answer "
                "a question based on past conversations. Score 0.0-1.0 using this rubric:\n"
                "- 1.0: the context clearly contains the facts needed to produce the expected answer.\n"
                "- 0.5: the context is partially relevant or ambiguous.\n"
                "- 0.0: the context does not contain the needed information or contradicts it.\n"
                "If the expected answer appears verbatim or as an obvious paraphrase, score at least 0.95.\n"
                "Do not heavily penalize extra unrelated text if the answer is still clearly present.\n"
                "Return ONLY a raw JSON object with no markdown fences or extra text: "
                "{\"score\": <float>, \"reasoning\": \"<str>\"}"
            )
            user = (
                f"Question: {result['question']}\n"
                f"Expected answer: {result['expected']}\n"
                f"Retrieved context: {result['context']}\n"
                f"Score the retrieval quality: did the system find the right information?"
            )
        try:
            resp = self._judge.complete(system=system, user=user)
            return self._parse_judge_response(resp.text)
        except Exception as e:
            return 0.0, f"Judge error: {e}"

    def report(self, scored: list[dict], version: str = "", previous: Optional[str] = None, eval_mode: str = "tool") -> LongMemEvalResult:
        """Aggregate scored results into a report with optional regression delta."""
        by_category = {}
        recall_by_category = {}
        for s in scored:
            cat = s.get("category", "unknown")
            by_category.setdefault(cat, []).append(s["score"])
            if "recall_any_at_5" in s:
                recall_by_category.setdefault(cat, []).append(s["recall_any_at_5"])

        categories = {cat: sum(scores) / len(scores) for cat, scores in by_category.items()}
        overall = sum(s["score"] for s in scored) / len(scored) if scored else 0.0

        # Aggregate R@5 (session-level recall, matching MemPalace methodology)
        recall_categories = {
            cat: round(sum(scores) / len(scores), 4)
            for cat, scores in recall_by_category.items()
        }
        recall_overall = (
            sum(s.get("recall_any_at_5", 0.0) for s in scored) / len(scored)
            if scored else 0.0
        )

        delta = {}
        if previous and os.path.exists(previous):
            prev = LongMemEvalResult.from_json(previous)
            if prev.eval_mode != eval_mode:
                logger.warning(
                    "Comparing %s mode results against %s mode baseline — delta may be meaningless",
                    eval_mode, prev.eval_mode,
                )
            delta = {
                "vs_version": prev.version,
                "vs_eval_mode": prev.eval_mode,
                "overall": round(overall - prev.overall, 4),
                "categories": {
                    cat: round(categories.get(cat, 0) - prev.categories.get(cat, 0), 4)
                    for cat in set(list(categories.keys()) + list(prev.categories.keys()))
                },
            }

        return LongMemEvalResult(
            version=version,
            eval_mode=eval_mode,
            timestamp=datetime.now(timezone.utc).isoformat(),
            judge={"provider": self.judge_provider, "model": self.judge_model or "default"},
            overall=round(overall, 4),
            recall_any_at_5=round(recall_overall, 4),
            categories={k: round(v, 4) for k, v in categories.items()},
            recall_categories=recall_categories,
            delta=delta,
            details=[
                {
                    "id": s["question_id"],
                    "category": s["category"],
                    "score": s["score"],
                    **({"recall_any_at_5": s["recall_any_at_5"]} if "recall_any_at_5" in s else {}),
                    **({"recall_top_sessions_at_5": s["recall_top_sessions_at_5"]} if "recall_top_sessions_at_5" in s else {}),
                    **({"answer_excerpt": s["answer_excerpt"]} if "answer_excerpt" in s else {}),
                    **({"answer_chars": s["answer_chars"]} if "answer_chars" in s else {}),
                    **({"error_kind": s["error_kind"]} if "error_kind" in s else {}),
                    **({"agent_trace": s["agent_trace"]} if "agent_trace" in s else {}),
                }
                for s in scored
            ],
        )
