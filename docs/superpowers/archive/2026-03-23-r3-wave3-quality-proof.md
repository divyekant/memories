# R3 Wave 3: Quality Proof Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove retrieval quality with LongMemEval benchmarks, validate recovery paths, and reduce extraction cost with signal keyword filtering.

**Architecture:** LongMemEval adapter extends the existing `eval/` framework via HTTP client (not direct engine calls). Signal keyword filter is a bash-level pre-check in extraction hooks. Backup/import validation are pytest-based round-trip tests.

**Tech Stack:** Python (eval adapter, CLI), Bash (hook filter), pytest

**Design spec:** `docs/superpowers/specs/2026-03-23-r3-wave3-quality-proof-design.md`

**Test baseline:** 955 tests passing

---

## File Map

| File | Role | Action |
|------|------|--------|
| `eval/longmemeval.py` | LongMemEval adapter | **Create** |
| `eval/memories_client.py` | Add search() and extract() to MemoriesClient | **Modify** |
| `cli/commands/eval_cmd.py` | CLI: `memories eval longmemeval` | **Create** |
| `cli/__init__.py` | Register eval command | **Modify** |
| `integrations/claude-code/hooks/memory-extract.sh` | Signal keyword filter | **Modify** |
| `integrations/claude-code/hooks/memory-commit.sh` | Signal keyword filter | **Modify** |
| `tests/test_longmemeval.py` | Adapter logic tests | **Create** |
| `tests/test_snapshot_roundtrip.py` | Snapshot round-trip validation | **Create** |
| `tests/test_import_export_roundtrip.py` | Import/export round-trip validation | **Create** |

---

## B1: LongMemEval Integration

### Task 1: Extend MemoriesClient with search() and extract()

**Files:**
- Modify: `eval/memories_client.py`
- Test: `tests/test_longmemeval.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_longmemeval.py
import pytest
from unittest.mock import patch, MagicMock
import json

def test_memories_client_has_search_method():
    """MemoriesClient should have a search() method."""
    from eval.runner import MemoriesClient
    client = MemoriesClient(url="http://localhost:8900", api_key="test")
    assert hasattr(client, "search")
    assert callable(client.search)

def test_memories_client_has_extract_method():
    """MemoriesClient should have an extract() method."""
    from eval.runner import MemoriesClient
    client = MemoriesClient(url="http://localhost:8900", api_key="test")
    assert hasattr(client, "extract")
    assert callable(client.extract)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/test_longmemeval.py -v`
Expected: FAIL — methods don't exist

- [ ] **Step 3: Add methods to MemoriesClient**

In `eval/memories_client.py`, in the `MemoriesClient` class, add two new methods. The existing client uses `httpx` (not `requests`) with `self._client` as the httpx.Client instance. Follow the existing pattern (e.g., `seed_memories`, `clear_by_prefix`):

```python
def search(self, query: str, k: int = 5, hybrid: bool = True, feedback_weight: float = 0.0) -> list[dict]:
    """POST /search — returns results list."""
    resp = self._client.post(
        f"{self.url}/search",
        json={"query": query, "k": k, "hybrid": hybrid, "feedback_weight": feedback_weight},
    )
    resp.raise_for_status()
    return resp.json().get("results", [])

def extract(self, messages: str, source: str, context: str = "stop", dry_run: bool = False) -> dict:
    """POST /memory/extract — submits extraction job, polls until complete."""
    resp = self._client.post(
        f"{self.url}/memory/extract",
        json={"messages": messages, "source": source, "context": context, "dry_run": dry_run},
    )
    resp.raise_for_status()
    job_id = resp.json().get("job_id")
    if not job_id:
        return resp.json()
    # Poll until complete (max 30s)
    import time
    for _ in range(30):
        time.sleep(1)
        poll = self._client.get(f"{self.url}/memory/extract/{job_id}")
        poll.raise_for_status()
        data = poll.json()
        if data.get("status") in ("completed", "failed"):
            return data
    return {"status": "timeout", "job_id": job_id}
```

Read `eval/memories_client.py` first to verify exact httpx patterns.

- [ ] **Step 4: Run test**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/test_longmemeval.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add eval/runner.py tests/test_longmemeval.py
git commit -m "feat: add search() and extract() to eval MemoriesClient"
```

---

### Task 2: LongMemEval adapter

**Files:**
- Create: `eval/longmemeval.py`
- Create: `eval/scenarios/longmemeval/.gitkeep`
- Test: `tests/test_longmemeval.py`

- [ ] **Step 1: Write failing test**

```python
def test_longmemeval_runner_init():
    """LongMemEvalRunner should accept client and judge config."""
    from eval.longmemeval import LongMemEvalRunner
    client = MagicMock()
    runner = LongMemEvalRunner(client=client, judge_provider="anthropic")
    assert runner is not None

def test_longmemeval_report_format():
    """Report should include overall score, categories, and delta."""
    from eval.longmemeval import LongMemEvalResult
    result = LongMemEvalResult(
        version="3.2.2",
        overall=0.724,
        categories={"information_extraction": 0.78},
    )
    assert result.overall == 0.724
    assert "information_extraction" in result.categories
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Create `eval/longmemeval.py`**

```python
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
        data_file = cache_path / "longmemeval_s.jsonl"
        if not data_file.exists():
            self._download_dataset(data_file)
        return self._parse_dataset(data_file)

    def _download_dataset(self, dest: Path):
        """Download LongMemEval_s from HuggingFace."""
        import urllib.request
        url = "https://huggingface.co/datasets/xiaowu0162/LongMemEval/resolve/main/longmemeval_s.jsonl"
        urllib.request.urlretrieve(url, str(dest))

    def _parse_dataset(self, path: Path) -> list[dict]:
        """Parse JSONL into list of question dicts."""
        items = []
        with open(path) as f:
            for line in f:
                if line.strip():
                    items.append(json.loads(line))
        return items

    def seed_memories(self, conversations: list[dict], source_prefix: str = "eval/longmemeval"):
        """Extract memories from conversation histories."""
        self.client.clear_by_prefix(source_prefix)
        for conv in conversations:
            messages = self._format_conversation(conv)
            self.client.extract(messages=messages, source=source_prefix, context="stop")

    def _format_conversation(self, conv: dict) -> str:
        """Format a conversation dict into the text format extract expects."""
        turns = conv.get("turns", conv.get("messages", []))
        lines = []
        for turn in turns:
            role = turn.get("role", "user")
            text = turn.get("content", turn.get("text", ""))
            lines.append(f"{role}: {text}")
        return "\n\n".join(lines)

    def run_questions(self, questions: list[dict], k: int = 5) -> list[dict]:
        """For each question: search memories, build context, generate answer."""
        results = []
        for q in questions:
            query = q.get("question", "")
            search_results = self.client.search(query=query, k=k, hybrid=True)
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
        """Initialize LLM judge provider."""
        from llm_provider import get_provider
        self._judge = get_provider(self.judge_provider, self.judge_model)

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
            data = json.loads(resp.text)
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
```

- [ ] **Step 4: Create `.gitkeep`**

```bash
mkdir -p eval/scenarios/longmemeval
touch eval/scenarios/longmemeval/.gitkeep
echo "eval/scenarios/longmemeval/*.jsonl" >> .gitignore
```

- [ ] **Step 5: Run tests**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/test_longmemeval.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add eval/longmemeval.py eval/scenarios/longmemeval/.gitkeep .gitignore tests/test_longmemeval.py
git commit -m "feat: add LongMemEval benchmark adapter"
```

---

### Task 3: CLI entry point for eval

**Files:**
- Create: `cli/commands/eval_cmd.py`
- Modify: `cli/__init__.py`

- [ ] **Step 1: Create CLI command**

```python
# cli/commands/eval_cmd.py
"""CLI commands for running evaluation benchmarks."""

import click
import json
import sys
from pathlib import Path


@click.group("eval")
def eval_group():
    """Run evaluation benchmarks."""
    pass


@eval_group.command("longmemeval")
@click.option("--judge-provider", default="anthropic", help="LLM judge provider")
@click.option("--judge-model", default=None, help="Override judge model")
@click.option("--output", default=None, help="Output file path for results JSON")
@click.option("--compare", default=None, help="Previous results file for regression delta")
@click.option("--k", default=5, help="Number of search results per question")
@click.option("--url", default="http://localhost:8900", help="Memories service URL")
@click.option("--api-key", default="", help="API key")
def longmemeval(judge_provider, judge_model, output, compare, k, url, api_key):
    """Run LongMemEval benchmark against the Memories engine."""
    import os
    from eval.runner import MemoriesClient
    from eval.longmemeval import LongMemEvalRunner

    api_key = api_key or os.getenv("MEMORIES_API_KEY", "")
    url = os.getenv("MEMORIES_URL", url)

    client = MemoriesClient(url=url, api_key=api_key)
    runner = LongMemEvalRunner(client=client, judge_provider=judge_provider, judge_model=judge_model)

    click.echo("Loading LongMemEval dataset...")
    dataset = runner.load_dataset()
    click.echo(f"Loaded {len(dataset)} questions")

    click.echo("Seeding memories from conversations...")
    conversations = [q for q in dataset if q.get("turns") or q.get("messages")]
    runner.seed_memories(conversations)

    click.echo(f"Running {len(dataset)} questions (k={k})...")
    results = runner.run_questions(dataset, k=k)

    click.echo("Judging answers...")
    scored = runner.judge_answers(results)

    # Get version from pyproject.toml
    version = "unknown"
    pyproject = Path("pyproject.toml")
    if pyproject.exists():
        for line in pyproject.read_text().splitlines():
            if line.startswith("version"):
                version = line.split('"')[1]
                break

    report = runner.report(scored, version=version, previous=compare)

    # Print summary
    click.echo(f"\nLongMemEval v{report.version} ({report.timestamp[:10]})")
    click.echo(f"Judge: {report.judge['provider']}/{report.judge['model']}")
    delta_str = ""
    if report.delta:
        d = report.delta.get("overall", 0)
        delta_str = f" ({'+' if d >= 0 else ''}{d*100:.1f}% vs {report.delta['vs_version']})"
    click.echo(f"Overall: {report.overall*100:.1f}%{delta_str}")
    for cat, score in sorted(report.categories.items()):
        cat_delta = ""
        if report.delta and "categories" in report.delta:
            cd = report.delta["categories"].get(cat, 0)
            cat_delta = f" ({'+' if cd >= 0 else ''}{cd*100:.1f}%)"
        click.echo(f"  {cat}: {score*100:.1f}%{cat_delta}")

    # Save results
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(report.to_json())
        click.echo(f"\nResults saved to {output}")
```

- [ ] **Step 2: Register in cli/__init__.py**

Add after the last import at ~line 48:
```python
from cli.commands.eval_cmd import eval_group
app.add_command(eval_group)
```

- [ ] **Step 3: Commit**

```bash
git add cli/commands/eval_cmd.py cli/__init__.py
git commit -m "feat: add CLI entry point for LongMemEval benchmark"
```

---

## B2: Operational Hardening

### Task 4: Signal keyword pre-filter in extraction hooks

**Files:**
- Modify: `integrations/claude-code/hooks/memory-extract.sh`
- Modify: `integrations/claude-code/hooks/memory-commit.sh`

- [ ] **Step 1: Add filter to memory-extract.sh**

After the `$MESSAGES` assembly (after line ~80, before the curl call at line ~84), insert:

```bash
# Signal keyword pre-filter — skip extraction if no signals detected
SIGNAL_KEYWORDS="${MEMORIES_SIGNAL_KEYWORDS:-decide|decision|chose|bug|fix|remember|architecture|convention|pattern|learning|mistake}"
if [ -n "$SIGNAL_KEYWORDS" ] && [ -n "$MESSAGES" ] && ! echo "$MESSAGES" | grep -qiE "$SIGNAL_KEYWORDS"; then
  exit 0
fi
```

- [ ] **Step 2: Add filter to memory-commit.sh**

Same pattern, inserted after `$MESSAGES` assembly and before curl call:

```bash
SIGNAL_KEYWORDS="${MEMORIES_SIGNAL_KEYWORDS:-decide|decision|chose|bug|fix|remember|architecture|convention|pattern|learning|mistake}"
if [ -n "$SIGNAL_KEYWORDS" ] && [ -n "$MESSAGES" ] && ! echo "$MESSAGES" | grep -qiE "$SIGNAL_KEYWORDS"; then
  exit 0
fi
```

**NOTE:** Do NOT add filter to `memory-flush.sh` — PreCompact always extracts (context about to be lost).

- [ ] **Step 3: Commit**

```bash
git add integrations/claude-code/hooks/memory-extract.sh integrations/claude-code/hooks/memory-commit.sh
git commit -m "feat: add signal keyword pre-filter to extraction hooks"
```

---

### Task 5: Snapshot round-trip validation test

**Files:**
- Create: `tests/test_snapshot_roundtrip.py`

- [ ] **Step 1: Write test**

```python
# tests/test_snapshot_roundtrip.py
"""End-to-end snapshot create-mutate-restore-verify test."""

import pytest
from memory_engine import MemoryEngine


@pytest.fixture
def engine(tmp_path):
    return MemoryEngine(data_dir=str(tmp_path))


class TestSnapshotRoundTrip:
    def test_create_mutate_restore_verifies_data(self, engine):
        """Full snapshot lifecycle: add → snapshot → delete → restore → verify."""
        # 1. Add 5 test memories
        ids = []
        for i in range(5):
            mid = engine.add(f"test memory {i}", source="test/snapshot")
            ids.append(mid)
        assert len(engine.list_memories()) == 5

        # 2. Create snapshot
        snapshot_name = engine.create_snapshot(reason="roundtrip-test")
        assert snapshot_name is not None

        # 3. Delete 3 memories
        for mid in ids[:3]:
            engine.delete_memory(mid)
        remaining = engine.list_memories()
        assert len(remaining) == 2

        # 4. Restore snapshot
        engine.restore_snapshot(snapshot_name)

        # 5. Verify all 5 are back
        restored = engine.list_memories()
        assert len(restored) == 5

        # 6. Verify content
        for i, mid in enumerate(ids):
            mem = engine.get_memory(mid)
            assert f"test memory {i}" in mem["text"]

    def test_snapshot_empty_engine(self, engine):
        """Snapshot with zero memories should work."""
        name = engine.create_snapshot(reason="empty-test")
        assert name is not None
```

**IMPORTANT:** The snapshot methods live on `qdrant_store`, not `MemoryEngine` directly. Use the FastAPI TestClient (like `tests/test_snapshots.py`) to test via HTTP endpoints (`POST /snapshots`, `POST /snapshots/{name}/restore`). Read `tests/test_snapshots.py` for the exact fixture and mock patterns before writing these tests. The engine method signatures differ from the simplified code above — `add_memories([{"text": ..., "source": ...}])` not `add()`.

- [ ] **Step 2: Run test**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/test_snapshot_roundtrip.py -v`

- [ ] **Step 3: Commit**

```bash
git add tests/test_snapshot_roundtrip.py
git commit -m "test: add snapshot round-trip validation"
```

---

### Task 6: Import/export round-trip validation test

**Files:**
- Create: `tests/test_import_export_roundtrip.py`

- [ ] **Step 1: Write test**

```python
# tests/test_import_export_roundtrip.py
"""End-to-end import/export round-trip test."""

import pytest
import json
from memory_engine import MemoryEngine


@pytest.fixture
def engine(tmp_path):
    return MemoryEngine(data_dir=str(tmp_path))


class TestImportExportRoundTrip:
    def test_export_clear_import_verifies_data(self, engine):
        """Full round-trip: add → export → clear → import → verify."""
        # 1. Add 5 test memories with metadata
        texts = [
            ("decision: use PostgreSQL", "test/decisions"),
            ("learning: always validate inputs", "test/learnings"),
            ("bug fix: null pointer in auth", "test/bugs"),
            ("convention: snake_case for Python", "test/conventions"),
            ("architecture: microservices pattern", "test/architecture"),
        ]
        ids = []
        for text, source in texts:
            mid = engine.add(text, source=source)
            ids.append(mid)

        # 2. Export
        lines = engine.export_memories()
        assert len(lines) > 1  # header + 5 memories

        # 3. Clear all
        for mid in ids:
            engine.delete_memory(mid)
        assert len(engine.list_memories()) == 0

        # 4. Import with strategy=add
        result = engine.import_memories(lines, strategy="add")
        assert result["imported"] >= 5

        # 5. Verify all restored
        memories = engine.list_memories()
        assert len(memories) >= 5

        # 6. Verify content searchable
        results = engine.search("PostgreSQL")
        assert any("PostgreSQL" in r.get("text", "") for r in results)

    def test_smart_import_deduplicates(self, engine):
        """Smart import should skip duplicates."""
        engine.add("test memory for dedup", source="test/dedup")
        lines = engine.export_memories()

        # Import same data again with smart mode
        result = engine.import_memories(lines, strategy="smart")
        assert result["skipped"] >= 1

    def test_export_with_source_filter(self, engine):
        """Export with source filter should only include matching memories."""
        engine.add("memory A", source="project-a/test")
        engine.add("memory B", source="project-b/test")
        lines = engine.export_memories(source_prefix="project-a")
        # Should have header + 1 memory (not 2)
        data_lines = [l for l in lines if not l.startswith('{"_header"')]
        assert len(data_lines) == 1
```

**IMPORTANT:** Engine methods differ from simplified code above. Use `add_memories([{"text": ..., "source": ...}])` not `add()`. Read `tests/test_export_import.py` for the exact fixture and engine patterns before writing. `engine.search()` takes `query` as first arg — verify signature.

- [ ] **Step 2: Run test**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/test_import_export_roundtrip.py -v`

- [ ] **Step 3: Run full suite**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/ -q`
Expected: All pass (955 baseline + new tests)

- [ ] **Step 4: Commit**

```bash
git add tests/test_import_export_roundtrip.py
git commit -m "test: add import/export round-trip validation"
```

---

### Task 7: Signal keyword filter tests

**Files:**
- Create: `tests/test_signal_filter.py`

- [ ] **Step 1: Write tests for the grep pattern**

```python
# tests/test_signal_filter.py
"""Test the signal keyword pre-filter logic used by extraction hooks."""

import subprocess
import pytest

DEFAULT_KEYWORDS = "decide|decision|chose|bug|fix|remember|architecture|convention|pattern|learning|mistake"

def _grep_matches(text: str, keywords: str = DEFAULT_KEYWORDS) -> bool:
    """Simulate the hook's grep check."""
    result = subprocess.run(
        ["grep", "-qiE", keywords],
        input=text,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0

def test_matches_decision_keyword():
    assert _grep_matches("We decided to use PostgreSQL")

def test_matches_bug_keyword():
    assert _grep_matches("Found a bug in the auth module")

def test_matches_remember_keyword():
    assert _grep_matches("Remember this for next time")

def test_no_match_skips():
    assert not _grep_matches("Just running some tests and checking output")

def test_case_insensitive():
    assert _grep_matches("This is an ARCHITECTURE decision")

def test_empty_keywords_matches_everything():
    """Empty keywords string disables the filter (matches everything)."""
    # The hook guards this with [ -n "$SIGNAL_KEYWORDS" ] so empty = no filter
    # But grep with empty pattern matches everything, which is why the guard exists
    result = subprocess.run(
        ["grep", "-qiE", ""],
        input="any random text",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0  # empty pattern matches

def test_custom_keywords():
    assert _grep_matches("This is important", keywords="important|critical")
    assert not _grep_matches("Just a normal message", keywords="important|critical")
```

- [ ] **Step 2: Run tests**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/test_signal_filter.py -v`

- [ ] **Step 3: Commit**

```bash
git add tests/test_signal_filter.py
git commit -m "test: add signal keyword filter pattern tests"
```

---

## Post-Implementation Checklist

- [ ] All baseline tests still pass
- [ ] LongMemEval adapter loads dataset and runs synthetic test
- [ ] CLI `memories eval longmemeval` works (with --help at minimum)
- [ ] Signal keyword filter skips extraction when no keywords match
- [ ] Signal keyword filter does NOT skip memory-flush.sh (PreCompact)
- [ ] Snapshot round-trip: create → mutate → restore → verify
- [ ] Import/export round-trip: add → export → clear → import → verify
- [ ] Smart import dedup works in round-trip test
