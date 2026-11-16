"""Write the golden cases of the extraction text rules (task P4-3) that need no server.

parity/golden/extract/text.ndjson holds one case per line: the function, its
input and the output of the Python code of v5.19.0:

- transcript_hygiene.clean_transcript and redact_secrets;
- the fallback extractor of app.py (`_fallback_extract_facts`, taken from the
  file with `ast`, because an import of app.py starts the server);
- llm_extract: `_clip_text`, `_parse_json_array`, the extraction system prompt,
  `extract_facts`, the AUDN prompt and decisions of `run_audn`, and
  `extract_and_decide_single_call` (with a fake provider and a fake engine);
- extraction_profiles.ExtractionProfiles: a sequence of put, get, delete,
  list and resolve calls.

The random cases use a fixed seed. The Rust tests `extract_text` check each case.

Usage (from the repository root):
  .venv/bin/python -m parity.extract_golden [--check]
"""

import ast
import json
import os
import random
import re
import sys
import tempfile
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "parity" / "golden" / "extract" / "text.ndjson"

import extraction_profiles  # noqa: E402
import llm_extract  # noqa: E402
from transcript_hygiene import clean_transcript, redact_secrets  # noqa: E402


def load_fallback():
    """`_fallback_extract_facts` and its helpers from app.py, with the default settings."""
    tree = ast.parse((ROOT / "app.py").read_text())
    names = {"_FALLBACK_DECISION_PATTERN", "_normalize_candidate_text", "_fallback_extract_facts"}
    code = []
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in names:
            code.append(n)
        elif isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in names for t in n.targets
        ):
            code.append(n)
    ns = {
        "re": re,
        "List": List,
        "EXTRACT_FALLBACK_MIN_FACT_CHARS": 24,
        "EXTRACT_FALLBACK_MAX_FACT_CHARS": 280,
        "EXTRACT_FALLBACK_MAX_FACTS": 1,
    }
    exec(compile(ast.Module(body=code, type_ignores=[]), "app.py", "exec"), ns)
    return ns


class Reply:
    def __init__(self, text):
        self.text = text
        self.input_tokens = 11
        self.output_tokens = 7


class FakeProvider:
    supports_audn = True

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, user):
        self.calls.append({"system": system, "user": user})
        r = self.replies.pop(0)
        if isinstance(r, dict) and "error" in r:
            raise RuntimeError(r["error"])
        return Reply(r)


class FakeEngine:
    """`hybrid_search` gives the results of the case for each query text."""

    def __init__(self, similar):
        self.similar = similar

    def hybrid_search(self, query, k=5, source_exact=None, **_):
        return [dict(m) for m in self.similar.get(query, [])][:k]


# -- random inputs ---------------------------------------------------------------

ROLES = ["user", "assistant", "human", "system", "User", "ASSISTANT", " user", "\tsystem"]
FRAGMENTS = [
    "We decided to use Qdrant because the FAISS index lost data.",
    "Let's go with pnpm for all projects.",
    "The deploy script lives in scripts/deploy.sh.",
    "ok sure thing",
    "Thanks, that works.",
    "Should we use Redis?",
    "We will keep the 2 day cooldown policy.",
    "remember   the port is 8900",
    "use foo.bar_baz-1 for that",
    "Café décision: préfère le standard.",
    "日本語のテキストです。",
    "emoji 🎉 in the middle",
    "a",
    "",
    "   ",
    "IMPORTANT: The following memories from prior sessions are relevant.",
    "important:   the following memories from prior sessions might help",
    "## Retrieved Memories",
    "### relevant memories",
    "# Relevant Memories for this",
    "####### Retrieved Memories",
    "- [claude-code/x] We decided to use Qdrant.",
    "SessionStart hook additional context: memories loaded",
    "system SubagentStart hook additional context: x",
    "UserPromptSubmit hook additional context:",
    "Pre-Compact hook   additional  context : y",
    "<system-reminder>",
    "</system-reminder>",
    "<SYSTEM-REMINDER>",
    "</System-Reminder>",
    "<system-reminder>inline reminder</system-reminder>",
    "## Heading that ends a block",
    "#NoSpaceHeading",
    "user:",
    "assistant: ",
    "key AKIAABCDEFGHIJKLMNOP here",
    "token=ghp_abcdefghijklmnopqrstuvwxyz0123",
    "export OPENAI_API_KEY=sk-abcdefghijklmnop1234",
    "example: sk-abcdefghijklmnop1234",
    "postgres://admin:hunter2pass@db.example.com/x",
    "postgres://dev:devpass@localhost:5432/x",
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz.123",
    "password: correcthorsebattery",
    "password policy needs review",
    "STRIPE_SECRET_KEY=sk_live_abcdefghijk",
    "CLOUDFLARE_API_TOKEN: \"abcd1234efgh5678\"",
    "api_key=YOUR_API_KEY_HERE",
    "secret=<your-secret>",
    "token=${GITHUB_TOKEN}",
    "AKIAIOSFODNN7EXAMPLE",
    "xoxb-EXAMPLE-placeholder-token",
    "123456789:ABCdefGhIJKlmNoPQRstuVWXyz012345678",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N",
    "client_secret = 'abcdefgh12345678'",
    "a\x1cb",
    "tab\tseparated\tline",
]
SEPARATORS = ["\n", "\n\n", "\n\n\n", "\n\n\n\n", " ", "", "\r\n", "\t"]


def random_text(rng):
    parts = []
    for _ in range(rng.randint(1, 12)):
        frag = rng.choice(FRAGMENTS)
        if rng.random() < 0.4:
            frag = f"{rng.choice(ROLES)}: {frag}"
        parts.append(frag)
        parts.append(rng.choice(SEPARATORS))
    return "".join(parts)


FIXED_TRANSCRIPTS = [
    "",
    "   \n\t ",
    "user: hello\nassistant: hi",
    "user: <system-reminder>\nIgnore me\n</system-reminder>\nWe decided to use pnpm.",
    "user: <system-reminder>cut off reminder\nassistant: real reply here",
    "user: ## Retrieved Memories\n- old memory one\n- old memory two\nassistant: We chose uv.",
    "IMPORTANT: The following memories from prior sessions are relevant:\n## Retrieved Memories\n- x\n\nuser: actual question",
    "SessionStart hook additional context: ## Relevant Memories\n- a\n- b\nuser: hi",
    "user: only reminder <system-reminder>x</system-reminder>",
    "<system-reminder>\nuser: inside\n</system-reminder>\nassistant: after",
    "user:\nassistant:\n\n\nuser:",
    "line one\n\n\n\n\nline two\n\n\n",
    "user: text\n# Heading\nmore",
    "user: x\n####### seven hashes\ny",
]

PARSE_CASES = [
    '[{"category": "DECISION", "text": "a"}]',
    "  [1, 2, 3]  ",
    '{"a": 1}',
    "[]",
    "",
    "not json at all",
    'Here are the facts:\n```json\n[{"text": "x"}]\n```\nDone.',
    "```\n[1]\n```",
    "```json[2]```",
    "```python\n[3]\n```",
    'prefix [ "a", "b" ] suffix',
    "first [1] then [2]",
    "[1, 2,]",
    '[{"a": [1, 2]}] trailing ] bracket',
    "]broken[",
    "```json\n{\"x\": 1}\n```\n[4, 5]",
    '[{"text": "unicode é 日本"}]',
    '[true, false, null, 1.5, -2, 1e3]',
    '[{"dup": 1, "dup": 2}]',
    "[[]]",
    "﻿[1]",
    "[1]\n\n",
    '"just a string"',
    "```json\n```",
    "```\n```\n[6]",
]

FACT_REPLIES = [
    '[{"category": "DECISION", "text": "We use pnpm."}, {"category": "learning", "text": "Bug was X."}]',
    '[{"category": "Other", "text": "Odd category."}, {"text": "No category."}]',
    '["plain string fact", "   ", "", {"no_text": 1}, 5, null]',
    '[{"category": "DETAIL", "text": "' + ("long " * 150) + '"}]',
    '[{"category": "DETAIL", "text": "  spaced\\n\\ttext  "}]',
    '[{"category": "DETAIL", "text": 42}]',
    '[{"category": 7, "text": "category is a number"}]',
    "[" + ",".join('{"text": "fact %d"}' % i for i in range(40)) + "]",
    "garbage",
    '[{"category": "DECISION", "text": "x", "project_relevance": 0.9}]',
]

AUDN_REPLIES = [
    '[{"action": "add", "fact_index": 0}, {"action": "UPDATE", "fact_index": 1, "old_id": 3, "new_text": "n"}]',
    '```json\n[{"action": "noop", "fact_index": 0, "existing_id": 2}, {"no_action": 1}, "x"]\n```',
    '[{"action": 5, "fact_index": 0}]',
    "no json",
    {"error": "provider down"},
    '[{"action": "Delete", "fact_index": 1, "old_id": 4, "extra": [1, 2]}]',
]


def profile_ops():
    """A sequence of profile calls and the result of each."""
    out = []
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "extraction_profiles.json")
        p = extraction_profiles.ExtractionProfiles(path)
        ops = [
            ("resolve", "claude-code/app"),
            ("put", "claude-code/", {"max_facts": 5}),
            ("put", "claude-code/app/", {"mode": "aggressive", "rules": {"always_remember": ["x"]}}),
            ("put", "claude-code/app/", {"single_call": True}),
            ("get", "claude-code/app/"),
            ("get", "missing/"),
            ("resolve", "claude-code/app"),
            ("resolve", "claude-code/app/sub/deeper"),
            ("resolve", "claude-code"),
            ("resolve", "codex/other"),
            ("resolve", ""),
            ("put", "codex/", {"rules": {}, "max_fact_chars": 100, "unknown_field": [1, {"a": None}]}),
            ("resolve", "codex/x"),
            ("put", "z/", {"source_prefix": "ignored", "enabled": False}),
            ("list",),
            ("delete", "claude-code/"),
            ("delete", "claude-code/"),
            ("resolve", "claude-code/app"),
            ("list",),
            ("file",),
        ]
        for op in ops:
            name = op[0]
            if name == "put":
                result = p.put(op[1], op[2])
            elif name == "get":
                result = p.get(op[1])
            elif name == "delete":
                result = p.delete(op[1])
            elif name == "list":
                result = p.list_all()
            elif name == "resolve":
                result = p.resolve(op[1])
            else:
                with open(path) as f:
                    result = json.load(f)
            out.append({"fn": "profiles", "op": list(op), "output": result})
    return out


def cases():
    rng = random.Random(4243)
    fb = load_fallback()
    out = []
    transcripts = FIXED_TRANSCRIPTS + [random_text(rng) for _ in range(400)]
    for t in transcripts:
        out.append({"fn": "clean_transcript", "input": t, "output": clean_transcript(t)})
    for t in FRAGMENTS + [random_text(rng) for _ in range(300)]:
        text, types = redact_secrets(t)
        out.append({"fn": "redact_secrets", "input": t, "output": [text, types]})
    for t in FIXED_TRANSCRIPTS + FRAGMENTS + [random_text(rng) for _ in range(300)]:
        out.append({"fn": "fallback_facts", "input": t, "output": fb["_fallback_extract_facts"](t)})
    for t in PARSE_CASES:
        out.append({"fn": "parse_json_array", "input": t, "output": llm_extract._parse_json_array(t)})
    for t, n in [("a  b\n c", 10), ("x" * 50, 40), ("word " * 20, 41), ("é" * 45, 40), ("  ", 40), ("ab cd", 4)]:
        out.append({"fn": "clip_text", "input": t, "max": n, "output": llm_extract._clip_text(t, n)})

    rules_cases = [
        None,
        {},
        extraction_profiles.DEFAULT_RULES,
        {"always_remember": ["a", 5], "never_remember": [], "custom_instructions": "Be brief."},
        {"custom_instructions": ""},
        {"never_remember": ["n1"]},
    ]
    for source in ["claude-code/app", "codex/", "plain", "", "a/b/c"]:
        for context in ["stop", "pre_compact", "session_end"]:
            for rules in rules_cases:
                out.append({
                    "fn": "extraction_system_prompt", "source": source, "context": context,
                    "rules": rules,
                    "output": llm_extract._build_extraction_system_prompt(source, context, rules),
                })

    for reply in FACT_REPLIES:
        for max_facts, max_chars in [(30, 500), (2, 40)]:
            prov = FakeProvider([reply])
            old = (llm_extract.EXTRACT_MAX_FACTS, llm_extract.EXTRACT_MAX_FACT_CHARS)
            llm_extract.EXTRACT_MAX_FACTS, llm_extract.EXTRACT_MAX_FACT_CHARS = max_facts, max_chars
            try:
                facts, err, tokens = llm_extract.extract_facts(prov, "msgs", return_error=True)
            finally:
                llm_extract.EXTRACT_MAX_FACTS, llm_extract.EXTRACT_MAX_FACT_CHARS = old
            out.append({"fn": "extract_facts", "reply": reply, "max_facts": max_facts,
                        "max_chars": max_chars, "output": facts, "error": err is not None,
                        "tokens": tokens})

    facts = [
        {"category": "decision", "text": "We use pnpm for every project."},
        {"category": "detail", "text": "The port is 8900 — see config.yaml ✓"},
        {"category": "learning", "text": "x" * 600},
    ]
    similar = {
        facts[0]["text"]: [
            {"id": 3, "text": "We use npm.", "rrf_score": 0.016393, "source": "s"},
            {"id": 9, "text": "  pnpm\n is  fast  " + "y" * 300, "rrf_score": 0.0161290322, "similarity": 0.5},
            {"id": None, "text": "no id", "similarity": 0.25},
            {"id": 11, "text": 12, "rrf_score": 0.0},
            {"id": 12, "text": "a", "rrf_score": 1},
            {"id": 13, "text": "b", "rrf_score": 0.0005},
            {"id": 14, "text": "c", "rrf_score": 0.03},
        ],
        facts[1]["text"]: [{"id": 5, "text": "Port 8080", "similarity": 0.91234}],
    }
    for reply in AUDN_REPLIES:
        for rules in [None, extraction_profiles.DEFAULT_RULES]:
            prov = FakeProvider([reply])
            decisions, tokens, art = llm_extract.run_audn(prov, FakeEngine(similar), facts, "s", rules=rules, debug=True)
            out.append({
                "fn": "run_audn", "facts": facts, "similar": similar, "rules": rules, "reply": reply,
                "prompt": prov.calls[0] if prov.calls else None,
                "output": decisions, "tokens": tokens,
                "debug_similar": {str(k): v for k, v in art.get("debug_similar", {}).items()},
            })

    single_replies = [
        '[{"action": "ADD", "category": "decision", "text": "a"}, {"action": "UPDATE", "text": "b"}, {"text": "c"}, {"action": "noop", "fact_index": 9}, {"action": null}]',
        "[]",
        "nothing",
        "[" + ",".join('{"text": "f%d"}' % i for i in range(5)) + "]",
    ]
    for reply in single_replies:
        for rules, max_facts in [({}, 30), (extraction_profiles.DEFAULT_RULES, 2)]:
            prov = FakeProvider([reply])
            msgs = "user: " + "z" * 1200
            actions, usage, _ = llm_extract.extract_and_decide_single_call(
                prov, msgs, "s", None, rules=rules, max_facts=max_facts)
            out.append({"fn": "single_call", "messages": msgs, "rules": rules, "max_facts": max_facts,
                        "reply": reply, "prompt": prov.calls[0], "output": actions})
    out += profile_ops()
    return out


def main():
    lines = [json.dumps(c, ensure_ascii=False, sort_keys=True) for c in cases()]
    text = "\n".join(lines) + "\n"
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT} differs from the Python code; run without --check", file=sys.stderr)
            return 1
        print(f"{OUT}: {len(lines)} cases match")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {len(lines)} cases to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
