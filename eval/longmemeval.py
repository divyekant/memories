"""LongMemEval benchmark adapter for Memories engine."""

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class LongMemEvalResult:
    version: str = ""
    timestamp: str = ""
    judge: dict = field(default_factory=dict)
    overall: float = 0.0
    categories: dict = field(default_factory=dict)
    delta: dict = field(default_factory=dict)
    details: list = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, path: str) -> "LongMemEvalResult":
        with open(path) as f:
            return cls(**json.load(f))


LONGMEMEVAL_CATEGORIES = [
    "information_extraction",
    "multi_session_reasoning",
    "knowledge_update",
    "temporal_reasoning",
    "abstaining",
]


class LongMemEvalRunner:
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
        """Parse JSON array into list of question dicts."""
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        # Fallback for JSONL format
        items = []
        with open(path) as f:
            for line in f:
                if line.strip():
                    items.append(json.loads(line))
        return items

    def seed_memories(self, conversations: list[dict], source_prefix: str = "eval/longmemeval"):
        """Extract memories from conversation histories, scoped per session."""
        self.client.clear_by_prefix(source_prefix)
        self._session_map = {}
        for i, conv in enumerate(conversations):
            session_source = f"{source_prefix}/session_{i}"
            messages = self._format_conversation(conv)
            self.client.extract(messages=messages, source=session_source, context="stop")
            self._session_map[conv.get("id", i)] = session_source

    def _format_conversation(self, conv: dict) -> str:
        """Format a conversation dict into the text format extract expects."""
        turns = conv.get("turns", conv.get("messages", []))
        lines = []
        for turn in turns:
            role = turn.get("role", "user")
            text = turn.get("content", turn.get("text", ""))
            lines.append(f"{role}: {text}")
        return "\n\n".join(lines)

    def run_questions(self, questions: list[dict], k: int = 5, source_prefix: str = "eval/longmemeval") -> list[dict]:
        """For each question: search memories, build context, generate answer.

        Uses session-scoped search when a question's conversation/session
        can be mapped via ``_session_map``.  Falls back to the full
        *source_prefix* when no mapping exists.
        """
        session_map = getattr(self, "_session_map", {})
        results = []
        for q in questions:
            query = q.get("question", "")
            # Resolve session source: try conversation_id, session_id, then id
            conv_ref = q.get("conversation_id", q.get("session_id", q.get("id")))
            search_prefix = session_map.get(conv_ref, source_prefix)
            search_results = self.client.search(
                query=query, k=k, hybrid=True, source_prefix=search_prefix,
            )
            context = "\n".join(r.get("text", "") for r in search_results)
            results.append({
                "question_id": q.get("id", ""),
                "category": q.get("category", ""),
                "question": query,
                "expected": q.get("answer", ""),
                "context": context,
                "search_results": search_results,
            })
        return results

    def judge_answers(self, results: list[dict]) -> list[dict]:
        """Score each answer using LLM judge."""
        if self._judge is None:
            self._init_judge()
        scored = []
        for r in results:
            score, reasoning = self._judge_single(r)
            scored.append({**r, "score": score, "reasoning": reasoning})
        return scored

    def _init_judge(self):
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

    def _judge_single(self, result: dict) -> tuple[float, str]:
        """Score a single question-answer pair."""
        system = (
            "You are evaluating whether an AI assistant correctly answered a question "
            "based on its memory of past conversations. Score 0.0-1.0.\n"
            "Respond with JSON: {\"score\": <float>, \"reasoning\": \"<str>\"}"
        )
        user = (
            f"Question: {result['question']}\n"
            f"Expected answer: {result['expected']}\n"
            f"Retrieved context: {result['context']}\n"
            f"Score the retrieval quality: did the system find the right information?"
        )
        try:
            resp = self._judge.complete(system=system, user=user)
            # Strip markdown code fences if present
            text = resp.text.strip()
            if text.startswith("```"):
                text = "\n".join(text.split("\n")[1:])  # remove opening fence
                if text.endswith("```"):
                    text = text[:-3].strip()
            data = json.loads(text)
            return data.get("score", 0.0), data.get("reasoning", "")
        except Exception as e:
            return 0.0, f"Judge error: {e}"

    def report(self, scored: list[dict], version: str = "", previous: Optional[str] = None) -> LongMemEvalResult:
        """Aggregate scored results into a report with optional regression delta."""
        by_category = {}
        for s in scored:
            cat = s.get("category", "unknown")
            by_category.setdefault(cat, []).append(s["score"])

        categories = {cat: sum(scores) / len(scores) for cat, scores in by_category.items()}
        overall = sum(s["score"] for s in scored) / len(scored) if scored else 0.0

        delta = {}
        if previous and os.path.exists(previous):
            prev = LongMemEvalResult.from_json(previous)
            delta = {
                "vs_version": prev.version,
                "overall": round(overall - prev.overall, 4),
                "categories": {
                    cat: round(categories.get(cat, 0) - prev.categories.get(cat, 0), 4)
                    for cat in set(list(categories.keys()) + list(prev.categories.keys()))
                },
            }

        return LongMemEvalResult(
            version=version,
            timestamp=datetime.now(timezone.utc).isoformat(),
            judge={"provider": self.judge_provider, "model": self.judge_model or "default"},
            overall=round(overall, 4),
            categories={k: round(v, 4) for k, v in categories.items()},
            delta=delta,
            details=[{"id": s["question_id"], "category": s["category"], "score": s["score"]} for s in scored],
        )
