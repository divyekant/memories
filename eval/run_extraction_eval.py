#!/usr/bin/env python3
"""Extraction Quality Comparison Eval.

Scientific comparison of LLM models for the memory extraction pipeline.

Methodology:
  - Independent variable: extraction model
  - Controls: judge model (Anthropic Haiku), input corpus, temperature (0.0)
  - Measures: fact count, JSON compliance, quality scores, latency, tokens

Usage:
    python eval/run_extraction_eval.py [--models MODEL1,MODEL2,...] [--output PATH]

Models tested: Haiku (baseline), qwen3.5:9b, gemma3:12b, nuextract
"""

import json
import os
import sys
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_provider import OllamaProvider, AnthropicProvider, CompletionResult
from llm_extract import extract_facts


class NoThinkOllamaProvider(OllamaProvider):
    """OllamaProvider with thinking disabled and array normalization.

    Fixes two Ollama + local model issues:
    1. Qwen3.5 thinking mode produces empty responses with format: "json"
    2. Some models return a single JSON object instead of an array
       (e.g., {"category":"DECISION","text":"..."} instead of [{...}])
    """

    def complete(self, system: str, user: str) -> CompletionResult:
        import json as _json
        import urllib.request
        # No format: "json" — it forces single-object output on many models,
        # preventing array responses. The extraction pipeline's _parse_json_array
        # handles extracting arrays from freeform text.
        payload = _json.dumps({
            "model": self.model,
            "system": system,
            "prompt": user,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0},
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = _json.loads(resp.read())

        raw_text = data["response"]

        # Normalize: if model returned a single object or wrapped array, extract the array
        raw_text = self._normalize_to_array(raw_text)

        return CompletionResult(
            text=raw_text,
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
        )

    @staticmethod
    def _normalize_to_array(text: str) -> str:
        """Ensure the response is a JSON array.

        Handles:
        - Single object: {"category":...} -> [{"category":...}]
        - Wrapped array: {"facts": [...]} -> [...]
        - Already an array: [...] -> [...] (no-op)
        """
        import json as _json
        text = text.strip()
        if not text:
            return "[]"
        try:
            parsed = _json.loads(text)
        except _json.JSONDecodeError:
            return text  # Let downstream parser handle it

        if isinstance(parsed, list):
            return text  # Already an array

        if isinstance(parsed, dict):
            # Case 1: wrapped array {"facts": [...], "results": [...], etc.}
            for key in ("facts", "results", "items", "data", "extracted", "output"):
                if key in parsed and isinstance(parsed[key], list):
                    return _json.dumps(parsed[key])
            # Case 2: single fact object {"category": ..., "text": ...}
            if "text" in parsed or "category" in parsed:
                return _json.dumps([parsed])

        return text  # Unrecognized format, let downstream handle it

# ---------------------------------------------------------------------------
# Input corpus: 20 representative texts spanning extraction use cases
# ---------------------------------------------------------------------------

CORPUS = [
    # --- DECISIONS (6 texts) ---
    {
        "id": "decision-001-database",
        "category": "decision",
        "expected_facts_min": 2,
        "text": (
            "User: We need to pick a database for the order service.\n"
            "Assistant: I recommend CockroachDB over PostgreSQL. Reasons:\n"
            "1. We need multi-region active-active writes for EU + US data residency\n"
            "2. Automatic conflict resolution via MVCC timestamps\n"
            "3. PostgreSQL logical replication can't propagate DDL changes\n"
            "Tradeoff: higher per-query latency (~15ms vs ~5ms) but worth it for consistency.\n"
            "Decision: CockroachDB on managed cloud. Filed as ADR-017."
        ),
    },
    {
        "id": "decision-002-auth",
        "category": "decision",
        "expected_facts_min": 2,
        "text": (
            "User: Should we use JWT or session tokens for the API?\n"
            "Assistant: Going with opaque session tokens stored in Redis, not JWTs.\n"
            "JWTs can't be revoked without a blocklist, which defeats the purpose.\n"
            "Session tokens in Redis give us instant revocation and we already run Redis for caching.\n"
            "Token lifetime: 24 hours with sliding expiry. Refresh via /auth/refresh endpoint.\n"
            "User: Makes sense. What about the mobile clients?\n"
            "Assistant: Mobile gets the same tokens but with 30-day lifetime and device binding."
        ),
    },
    {
        "id": "decision-003-queue",
        "category": "decision",
        "expected_facts_min": 2,
        "text": (
            "After evaluating Kafka, RabbitMQ, and SQS for the event pipeline:\n"
            "- Kafka: best throughput but operational complexity is high\n"
            "- RabbitMQ: simpler but can't replay events (needed for audit)\n"
            "- SQS: cheapest but no ordering guarantees\n"
            "Decision: Kafka on Confluent Cloud. The replay capability is non-negotiable\n"
            "for financial audit compliance. Confluent reduces ops burden.\n"
            "Schema registry for Avro schemas. Topic naming: {domain}.{entity}.{version}"
        ),
    },
    {
        "id": "decision-004-frontend",
        "category": "decision",
        "expected_facts_min": 2,
        "text": (
            "User: React vs Vue for the dashboard?\n"
            "Assistant: React with Next.js. Three reasons:\n"
            "1. Team has 4 React devs and 0 Vue devs\n"
            "2. Next.js server components reduce client bundle by ~40%\n"
            "3. Vercel deployment pipeline already set up from the marketing site\n"
            "Component library: shadcn/ui on top of Radix primitives.\n"
            "State management: Zustand for client state, TanStack Query for server state.\n"
            "User: Agreed. Let's start with the data tables first."
        ),
    },
    {
        "id": "decision-005-testing",
        "category": "decision",
        "expected_facts_min": 2,
        "text": (
            "Testing strategy discussion:\n"
            "- Unit tests: vitest for frontend, pytest for backend\n"
            "- Integration tests: hit real database, no mocks for data layer\n"
            "- E2E: Playwright for critical flows only (login, checkout, onboarding)\n"
            "We got burned last quarter when mocked database tests passed but the migration\n"
            "broke production. Never mock the data layer again.\n"
            "Coverage target: 80% line coverage, but quality over quantity.\n"
            "CI runs tests on every PR, nightly full E2E suite."
        ),
    },
    {
        "id": "decision-006-deploy",
        "category": "decision",
        "expected_facts_min": 2,
        "text": (
            "Deployment architecture:\n"
            "Moving from Heroku to Fly.io for the backend services.\n"
            "Reasons: multi-region support, better pricing at our scale, LiteFS for SQLite replication.\n"
            "Frontend stays on Vercel. API gateway via Cloudflare Workers.\n"
            "Blue-green deploys with automatic rollback on 5xx spike > 2%.\n"
            "Monitoring: Grafana Cloud for metrics, Sentry for errors, PagerDuty for alerts."
        ),
    },
    # --- LEARNINGS (6 texts) ---
    {
        "id": "learning-001-cache-bug",
        "category": "learning",
        "expected_facts_min": 1,
        "text": (
            "User: Memory search returns stale results after updating a memory.\n"
            "Assistant: Found the root cause. The Qdrant client was reusing the old vector\n"
            "when processing UPDATE operations. The embedding wasn't being recomputed from\n"
            "the new text before upsert.\n"
            "Fix: Modified qdrant_store.py to always recompute the embedding from the updated\n"
            "text before calling upsert. The bug was introduced when we optimized the update\n"
            "path to skip embedding for metadata-only changes — the check was too broad."
        ),
    },
    {
        "id": "learning-002-rate-limit",
        "category": "learning",
        "expected_facts_min": 1,
        "text": (
            "Debugging the intermittent 429 errors from the Anthropic API:\n"
            "Root cause: our retry logic was using the same API key across all workers.\n"
            "When one worker hit the rate limit, all workers backed off simultaneously,\n"
            "then all retried at the same instant — thundering herd.\n"
            "Fix: added jitter to the exponential backoff (random 0-500ms per retry)\n"
            "and implemented per-worker rate tracking. 429s dropped from ~5% to <0.1%."
        ),
    },
    {
        "id": "learning-003-onnx-memory",
        "category": "learning",
        "expected_facts_min": 1,
        "text": (
            "The ONNX embedding model was leaking memory — RSS grew from 200MB to 1.2GB\n"
            "over 24 hours. Found that onnxruntime's InferenceSession holds internal caches\n"
            "that grow unboundedly with varied input lengths.\n"
            "Workaround: periodic reload of the ONNX session (every 900 seconds or when\n"
            "RSS exceeds 1.2GB). Added EMBEDDER_AUTO_RELOAD_ENABLED=true config.\n"
            "Root fix would require onnxruntime upstream change — filed issue #4521."
        ),
    },
    {
        "id": "learning-004-docker-dns",
        "category": "learning",
        "expected_facts_min": 1,
        "text": (
            "User: Docker containers can't resolve each other by name.\n"
            "Assistant: You're using `docker run` without a network. Containers need to be\n"
            "on the same user-defined bridge network for DNS resolution.\n"
            "With docker-compose this is automatic. With docker run:\n"
            "  docker network create mynet\n"
            "  docker run --network mynet --name svc1 ...\n"
            "  docker run --network mynet --name svc2 ...\n"
            "Now svc2 can reach svc1 at http://svc1:port. Default bridge network doesn't\n"
            "support DNS — that's a Docker gotcha."
        ),
    },
    {
        "id": "learning-005-migration-fail",
        "category": "learning",
        "expected_facts_min": 1,
        "text": (
            "Post-mortem: Production migration failed because ALTER TABLE ADD COLUMN\n"
            "with a DEFAULT value locks the table in PostgreSQL < 11. We're on PG 10.\n"
            "The orders table has 50M rows — the lock held for 8 minutes, causing timeouts.\n"
            "Fix: split into ALTER TABLE ADD COLUMN (no default) + UPDATE in batches +\n"
            "ALTER TABLE SET DEFAULT. Total time: 12 seconds with no locks.\n"
            "Action item: upgrade to PG 14 where ADD COLUMN DEFAULT is instant."
        ),
    },
    {
        "id": "learning-006-cors",
        "category": "learning",
        "expected_facts_min": 1,
        "text": (
            "CORS issue: preflight OPTIONS requests were returning 403.\n"
            "The API gateway was stripping the Access-Control-Allow-Methods header\n"
            "before it reached the browser. Root cause: Cloudflare Workers transform\n"
            "rules were overwriting response headers.\n"
            "Fix: moved CORS handling into the application layer (FastAPI CORSMiddleware)\n"
            "and configured Cloudflare to pass through CORS headers untouched."
        ),
    },
    # --- DETAILS (4 texts) ---
    {
        "id": "detail-001-config",
        "category": "detail",
        "expected_facts_min": 2,
        "text": (
            "Development environment setup:\n"
            "- Python 3.11+ required (3.12 works, 3.13 breaks onnxruntime)\n"
            "- QDRANT_URL=http://localhost:6333 for local dev\n"
            "- MODEL_NAME=all-MiniLM-L6-v2 for embeddings (384 dimensions)\n"
            "- API_KEY goes in .env, never hardcoded\n"
            "- docker compose up -d for Qdrant + app\n"
            "- ONNX model downloads on first boot (~90MB), cached at /data/models/"
        ),
    },
    {
        "id": "detail-002-api",
        "category": "detail",
        "expected_facts_min": 2,
        "text": (
            "API authentication model:\n"
            "- Header: X-API-Key\n"
            "- Three roles: admin (full access), read-write (source-scoped), read-only\n"
            "- Keys stored in SQLite keys.db with bcrypt hashing\n"
            "- Rate limiting: 100 req/min per key, 1000 req/min for admin\n"
            "- Health check at GET /health/ready (no auth required)\n"
            "- All write operations require source parameter for audit trail"
        ),
    },
    {
        "id": "detail-003-naming",
        "category": "detail",
        "expected_facts_min": 2,
        "text": (
            "Source prefix conventions:\n"
            "- claude-code/{project-name} for Claude Code hooks\n"
            "- cursor/{project-name} for Cursor hooks\n"
            "- manual/{topic} for manually added memories\n"
            "- eval/{test-name} for evaluation data\n"
            "Always use relative paths, never absolute (e.g., claude-code/memories not /Users/dk/projects/memories).\n"
            "Source prefixes are used for access scoping — a read-write key with prefix 'claude-code/'\n"
            "can only write to sources starting with 'claude-code/'."
        ),
    },
    {
        "id": "detail-004-ports",
        "category": "detail",
        "expected_facts_min": 2,
        "text": (
            "Service port allocation:\n"
            "- Production app: 8900 (maps to container 8000)\n"
            "- Eval app: 8901 (maps to container 8000)\n"
            "- Production Qdrant: 6333 (API), 6334 (gRPC)\n"
            "- Eval Qdrant: 6335 (API), 6336 (gRPC)\n"
            "- Ollama: 11434 (default)\n"
            "All services bind to 127.0.0.1 only — not exposed externally.\n"
            "Cloudflare tunnel handles external access for production."
        ),
    },
    # --- EDGE CASES (4 texts) ---
    {
        "id": "edge-001-noise",
        "category": "noise",
        "expected_facts_min": 0,
        "text": (
            "User: Hey, is it working now?\n"
            "Assistant: Yes, the build completed successfully. All 874 tests pass.\n"
            "User: Great, thanks!\n"
            "Assistant: No problem. Let me know if you need anything else."
        ),
    },
    {
        "id": "edge-002-short",
        "category": "decision",
        "expected_facts_min": 1,
        "text": (
            "Auth model: X-API-Key header. Three roles: admin, read-write, read-only. Keys in SQLite."
        ),
    },
    {
        "id": "edge-003-code-heavy",
        "category": "learning",
        "expected_facts_min": 1,
        "text": (
            "User: How do I fix the import cycle between memory_engine and qdrant_store?\n"
            "Assistant: Move the shared types to a separate module. Create types.py:\n"
            "```python\n"
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class MemoryRecord:\n"
            "    id: int\n"
            "    text: str\n"
            "    source: str\n"
            "    metadata: dict\n"
            "```\n"
            "Then import from types.py in both modules. The cycle was because\n"
            "memory_engine imported qdrant_store for QdrantStore and qdrant_store\n"
            "imported memory_engine for MemoryRecord. Classic Python circular import."
        ),
    },
    {
        "id": "edge-004-long-mixed",
        "category": "mixed",
        "expected_facts_min": 3,
        "text": (
            "Sprint retrospective notes:\n\n"
            "What went well:\n"
            "- Shipped the extraction pipeline in 3 days instead of estimated 5\n"
            "- New ONNX embeddings are 3x faster than the HuggingFace transformers version\n"
            "- Zero production incidents this sprint\n\n"
            "What didn't go well:\n"
            "- Spent 2 days debugging a Qdrant timeout that turned out to be OrbStack DNS\n"
            "  flaking under load. Fix: added retry with backoff to qdrant_store.py\n"
            "- The extraction prompt needs tuning — it extracts too many trivial facts\n"
            "  about file names and test counts. Added exclusion rules.\n\n"
            "Decisions:\n"
            "- Moving to weekly releases instead of bi-weekly\n"
            "- Adding a dry-run mode to extraction so users can preview before committing\n"
            "- Source prefix naming convention: always project-relative, never absolute paths\n\n"
            "Action items:\n"
            "- @dk: Write ADR for the extraction prompt design\n"
            "- @dk: Set up Grafana dashboard for extraction metrics"
        ),
    },
]


# ---------------------------------------------------------------------------
# Judge: Anthropic Haiku evaluates extraction quality
# ---------------------------------------------------------------------------

JUDGE_SYSTEM = """You are evaluating the quality of extracted facts from a conversation.

Given the original text and the extracted facts, score on three dimensions (0.0-1.0):

1. COMPLETENESS: What fraction of important, durable facts were captured?
   - 1.0: All key decisions, learnings, and details extracted
   - 0.5: Some important facts missed
   - 0.0: Most important facts missed or nothing extracted

2. ACCURACY: Are the extracted facts faithful to the original text?
   - 1.0: All facts accurately reflect the source
   - 0.5: Some facts are distorted or imprecise
   - 0.0: Facts contradict or misrepresent the source

3. SPECIFICITY: Are the facts precise and actionable, not vague summaries?
   - 1.0: Facts include specific names, values, reasons
   - 0.5: Some facts are too generic
   - 0.0: Facts are vague platitudes

Also note if the extraction correctly SKIPPED trivial content (task status, counts, greetings).

Return ONLY a raw JSON object:
{"completeness": <float>, "accuracy": <float>, "specificity": <float>, "reasoning": "<str>"}"""

JUDGE_USER = """Original text:
{text}

Extracted facts:
{facts}

Score the extraction quality."""


def judge_extraction(judge_provider, text: str, facts: list[dict]) -> dict:
    """Score an extraction using Anthropic as judge."""
    facts_str = json.dumps(facts, indent=2) if facts else "[]"
    user_msg = JUDGE_USER.format(text=text, facts=facts_str)
    try:
        resp = judge_provider.complete(system=JUDGE_SYSTEM, user=user_msg)
        # Parse JSON response
        raw = resp.text.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:])
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        json_start = raw.find("{")
        if json_start >= 0:
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(raw[json_start:])
            return {
                "completeness": float(data.get("completeness", 0)),
                "accuracy": float(data.get("accuracy", 0)),
                "specificity": float(data.get("specificity", 0)),
                "reasoning": str(data.get("reasoning", "")),
            }
    except Exception as e:
        pass
    return {"completeness": 0, "accuracy": 0, "specificity": 0, "reasoning": f"Judge parse error: {resp.text[:200] if 'resp' in dir() else str(e)}"}


# ---------------------------------------------------------------------------
# Model definitions
# ---------------------------------------------------------------------------

def get_models(filter_names: list[str] | None = None) -> dict:
    """Build model provider map. Skip unavailable models gracefully."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    all_models = {
        "haiku": lambda: AnthropicProvider(api_key=api_key, model="claude-haiku-4-5-20251001"),
        "qwen3:4b": lambda: NoThinkOllamaProvider(base_url="http://localhost:11434", model="qwen3:4b"),
        "qwen3.5:9b": lambda: NoThinkOllamaProvider(base_url="http://localhost:11434", model="qwen3.5:9b"),
        "qwen3.5:4b": lambda: NoThinkOllamaProvider(base_url="http://localhost:11434", model="qwen3.5:4b"),
        "gemma3:12b": lambda: NoThinkOllamaProvider(base_url="http://localhost:11434", model="gemma3:12b"),
        "gemma3:4b": lambda: NoThinkOllamaProvider(base_url="http://localhost:11434", model="gemma3:4b"),
        "nuextract": lambda: NoThinkOllamaProvider(base_url="http://localhost:11434", model="nuextract"),
        "qwen3:8b": lambda: NoThinkOllamaProvider(base_url="http://localhost:11434", model="qwen3:8b"),
        "memex:1.7b": lambda: NoThinkOllamaProvider(base_url="http://localhost:11434", model="memex:1.7b"),
    }

    if filter_names:
        all_models = {k: v for k, v in all_models.items() if k in filter_names}

    return all_models


# ---------------------------------------------------------------------------
# Main eval loop
# ---------------------------------------------------------------------------

def run_eval(models_filter: list[str] | None = None, output_path: str = "") -> dict:
    """Run extraction eval across all models and input texts."""
    # Load env
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY required (for judge + haiku baseline)")
        sys.exit(1)

    # Initialize constant judge
    judge = AnthropicProvider(api_key=api_key, model="claude-haiku-4-5-20251001")
    print(f"Judge initialized: Anthropic Haiku (constant across all models)")

    models = get_models(models_filter)
    print(f"Models to test: {list(models.keys())}")
    print(f"Corpus size: {len(CORPUS)} texts")
    print(f"{'='*70}")

    all_results = {}

    for model_name, model_factory in models.items():
        print(f"\n{'='*70}")
        print(f"MODEL: {model_name}")
        print(f"{'='*70}")

        try:
            provider = model_factory()
        except Exception as e:
            print(f"  SKIP: Failed to initialize {model_name}: {e}")
            continue

        # Health check
        try:
            healthy = provider.health_check()
        except Exception as e:
            healthy = False
        if not healthy:
            print(f"  SKIP: {model_name} health check failed")
            continue

        print(f"  Provider ready: {provider.provider_name}/{provider.model}")

        model_results = []
        total_start = time.time()

        for i, entry in enumerate(CORPUS):
            text_id = entry["id"]
            text = entry["text"]

            # Run extraction
            start = time.time()
            try:
                facts, error, tokens = extract_facts(
                    provider, text,
                    context="stop",
                    return_error=True,
                    source="eval/extraction",
                )
            except Exception as e:
                facts, error, tokens = [], str(e), {"input": 0, "output": 0}
            latency = time.time() - start

            # JSON compliance
            json_ok = error is None and isinstance(facts, list)
            fact_count = len(facts) if facts else 0

            # Judge quality (skip if no facts and none expected)
            if facts and json_ok:
                quality = judge_extraction(judge, text, facts)
            elif entry.get("expected_facts_min", 1) == 0 and fact_count == 0:
                # Correctly returned empty — score high
                quality = {"completeness": 1.0, "accuracy": 1.0, "specificity": 1.0,
                           "reasoning": "Correctly returned no facts for noise/trivial input"}
            else:
                quality = {"completeness": 0, "accuracy": 0, "specificity": 0,
                           "reasoning": f"Extraction failed: {error}"}

            result = {
                "text_id": text_id,
                "category": entry["category"],
                "fact_count": fact_count,
                "expected_facts_min": entry.get("expected_facts_min", 1),
                "json_compliant": json_ok,
                "error": error,
                "latency_s": round(latency, 2),
                "tokens": tokens,
                "quality": quality,
                "facts": facts[:5] if facts else [],  # keep first 5 for inspection
            }
            model_results.append(result)

            q_avg = (quality["completeness"] + quality["accuracy"] + quality["specificity"]) / 3
            status = "OK" if json_ok else "FAIL"
            print(f"  [{i+1:2d}/{len(CORPUS)}] {text_id:30s} facts={fact_count:2d}  json={status:4s}  "
                  f"quality={q_avg:.2f}  latency={latency:.1f}s")

        model_elapsed = time.time() - total_start
        all_results[model_name] = {
            "results": model_results,
            "elapsed_s": round(model_elapsed, 1),
        }
        print(f"\n  Total time: {model_elapsed:.0f}s ({model_elapsed/60:.1f}m)")

    # ---------------------------------------------------------------------------
    # Aggregate & report
    # ---------------------------------------------------------------------------
    print(f"\n\n{'='*70}")
    print("RESULTS SUMMARY")
    print(f"{'='*70}")

    summary = {}
    for model_name, model_data in all_results.items():
        results = model_data["results"]
        n = len(results)
        if n == 0:
            continue

        json_ok_count = sum(1 for r in results if r["json_compliant"])
        avg_facts = sum(r["fact_count"] for r in results) / n
        avg_latency = sum(r["latency_s"] for r in results) / n
        avg_completeness = sum(r["quality"]["completeness"] for r in results) / n
        avg_accuracy = sum(r["quality"]["accuracy"] for r in results) / n
        avg_specificity = sum(r["quality"]["specificity"] for r in results) / n
        avg_quality = (avg_completeness + avg_accuracy + avg_specificity) / 3
        total_tokens_in = sum(r["tokens"].get("input", 0) for r in results)
        total_tokens_out = sum(r["tokens"].get("output", 0) for r in results)

        summary[model_name] = {
            "texts_run": n,
            "json_compliance": round(json_ok_count / n, 3),
            "avg_fact_count": round(avg_facts, 1),
            "avg_latency_s": round(avg_latency, 2),
            "avg_completeness": round(avg_completeness, 3),
            "avg_accuracy": round(avg_accuracy, 3),
            "avg_specificity": round(avg_specificity, 3),
            "avg_quality": round(avg_quality, 3),
            "total_tokens_in": total_tokens_in,
            "total_tokens_out": total_tokens_out,
            "elapsed_s": model_data["elapsed_s"],
        }

        print(f"\n  {model_name}:")
        print(f"    JSON compliance : {json_ok_count}/{n} ({json_ok_count/n*100:.0f}%)")
        print(f"    Avg facts/text  : {avg_facts:.1f}")
        print(f"    Avg latency     : {avg_latency:.1f}s")
        print(f"    Avg completeness: {avg_completeness:.2f}")
        print(f"    Avg accuracy    : {avg_accuracy:.2f}")
        print(f"    Avg specificity : {avg_specificity:.2f}")
        print(f"    Avg quality     : {avg_quality:.2f}")
        print(f"    Total tokens    : {total_tokens_in} in / {total_tokens_out} out")

    # Comparison table
    if len(summary) > 1:
        print(f"\n\n{'='*70}")
        print("COMPARISON TABLE")
        print(f"{'='*70}")
        header = f"{'Model':20s} {'JSON%':>6s} {'Facts':>6s} {'Latency':>8s} {'Complete':>9s} {'Accuracy':>9s} {'Specific':>9s} {'Quality':>8s}"
        print(header)
        print("-" * len(header))
        for name, s in sorted(summary.items(), key=lambda x: -x[1]["avg_quality"]):
            print(f"{name:20s} {s['json_compliance']*100:5.0f}% {s['avg_fact_count']:5.1f}  {s['avg_latency_s']:6.1f}s  "
                  f"{s['avg_completeness']:8.3f}  {s['avg_accuracy']:8.3f}  {s['avg_specificity']:8.3f}  {s['avg_quality']:7.3f}")

        # Baseline delta
        if "haiku" in summary:
            baseline = summary["haiku"]["avg_quality"]
            print(f"\n  Delta vs Haiku baseline ({baseline:.3f}):")
            for name, s in sorted(summary.items(), key=lambda x: -x[1]["avg_quality"]):
                if name == "haiku":
                    continue
                delta = s["avg_quality"] - baseline
                sign = "+" if delta >= 0 else ""
                print(f"    {name:20s}: {sign}{delta:.3f} ({sign}{delta/baseline*100:.1f}%)")

    # Save full results
    output = {
        "eval_type": "extraction_quality",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "corpus_size": len(CORPUS),
        "judge": "anthropic/claude-haiku-4-5-20251001",
        "temperature": 0.0,
        "summary": summary,
        "details": {
            model_name: model_data["results"]
            for model_name, model_data in all_results.items()
        },
    }

    if not output_path:
        output_path = f"eval/results/extraction-eval-{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(output, indent=2))
    print(f"\nFull results saved to {output_path}")

    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extraction Quality Comparison Eval")
    parser.add_argument("--models", default=None, help="Comma-separated model names to test (default: all)")
    parser.add_argument("--output", default="", help="Output file path (default: auto-generated)")
    args = parser.parse_args()

    models_filter = args.models.split(",") if args.models else None
    run_eval(models_filter=models_filter, output_path=args.output)
