"""Transcript hygiene — strip hook-injected context before extraction.

Recall hooks inject previously stored memories into the conversation:

- UserPromptSubmit (plugin/hooks/memory-query.sh) injects an "IMPORTANT: The
  following memories from prior sessions..." preamble plus a
  "## Retrieved Memories" section.
- SessionStart (plugin/hooks/memory-recall.sh) injects "## Relevant Memories".
- Claude Code wraps injected hook output in <system-reminder> blocks and/or
  "<HookEvent> hook additional context:" prefixes inside transcript messages.

If the extraction LLM sees those blocks, previously stored memories are
re-extracted as "new" facts every session — the redundant-clusters
duplication bug. This module removes injected blocks so extraction only sees
genuine conversation. It is dependency-free and idempotent.
"""
import re

# A position that starts a new transcript message ("user: ...", "assistant: ...")
# or a markdown heading. Used to bound block-stripping so a truncated injected
# block never swallows the following genuine message.
_BOUNDARY_LOOKAHEAD = (
    r"(?=^[ \t]*#{1,6}[ \t]"
    r"|^[ \t]*(?:user|assistant|human|system)[ \t]*:"
    r"|\Z)"
)

_ROLE_LINE_GUARD = r"(?!^[ \t]*(?:user|assistant|human|system)[ \t]*:)"

# Injected blocks frequently start inline right after the assembled role
# prefix ("user: ## Retrieved Memories ..."). Strip the bare prefix with the
# block; the boundary lookahead keeps the next message intact.
_LINE_START = r"^[ \t]*(?:(?:user|assistant|human|system)[ \t]*:[ \t]*)?"

# <system-reminder>...</system-reminder> blocks. Tempered scan: consume until
# the closing tag, but never across a role boundary — handles blocks whose
# closing tag was cut off by the hook's per-message character cap without
# eating subsequent genuine messages.
_SYSTEM_REMINDER_RE = re.compile(
    r"<system-reminder>"
    r"(?:(?!</system-reminder>)" + _ROLE_LINE_GUARD + r".)*"
    r"(?:</system-reminder>)?",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)

# Stray tags left behind when a reminder block contained a role-like line.
_STRAY_REMINDER_TAG_RE = re.compile(r"</?system-reminder>", re.IGNORECASE)

# memory-query.sh preamble ahead of the Retrieved Memories section.
_MEMORY_PREAMBLE_RE = re.compile(
    _LINE_START
    + r"IMPORTANT:[ \t]+The following memories from prior sessions\b.*?"
    + _BOUNDARY_LOOKAHEAD,
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)

# "## Retrieved Memories" / "## Relevant Memories" sections (any heading level).
_MEMORY_SECTION_RE = re.compile(
    _LINE_START
    + r"#{1,6}[ \t]*(?:retrieved|relevant)[ \t]+memories\b.*?"
    + _BOUNDARY_LOOKAHEAD,
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)

# "<HookEvent> hook additional context:" blocks (1-3 leading words, e.g.
# "SessionStart hook additional context:", "system SubagentStart hook
# additional context:").
_HOOK_CONTEXT_RE = re.compile(
    _LINE_START
    + r"(?:[A-Za-z][\w-]*[ \t]+){1,3}hook[ \t]+additional[ \t]+context[ \t]*:.*?"
    + _BOUNDARY_LOOKAHEAD,
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)

_BLANK_RUNS_RE = re.compile(r"\n{3,}")

# A transcript that collapses to only bare role prefixes carries no signal.
_ONLY_ROLE_PREFIXES_RE = re.compile(
    r"^(?:[ \t]*(?:user|assistant|human|system)[ \t]*:[ \t]*\n*)*$",
    re.IGNORECASE | re.MULTILINE,
)


def clean_transcript(text: str) -> str:
    """Remove hook-injected context blocks from assembled transcript text.

    Returns "" when nothing but injected content (or whitespace) remains.
    Safe to call multiple times (idempotent) and on already-clean text.
    """
    if not text or not text.strip():
        return ""

    cleaned = _SYSTEM_REMINDER_RE.sub("", text)
    cleaned = _STRAY_REMINDER_TAG_RE.sub("", cleaned)
    cleaned = _MEMORY_PREAMBLE_RE.sub("", cleaned)
    cleaned = _MEMORY_SECTION_RE.sub("", cleaned)
    cleaned = _HOOK_CONTEXT_RE.sub("", cleaned)
    cleaned = _BLANK_RUNS_RE.sub("\n\n", cleaned).strip()

    if not cleaned or _ONLY_ROLE_PREFIXES_RE.fullmatch(cleaned):
        return ""
    return cleaned


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------
# Credentials mentioned in conversations must never reach the extraction LLM
# or be stored as memory text: stored memories are re-injected verbatim into
# future agent contexts, so one pasted token becomes a permanent leak (live
# audit finding: four real credentials sat in the corpus). Patterns are
# deliberately value-shaped to keep false positives low — prose like
# "password policy" is untouched because no value follows.

_SECRET_PATTERNS = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("telegram_bot_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b")),
    ("url_credentials", re.compile(r"(?<=://)[^\s/:@]+:[^\s/@]+(?=@)")),
    ("bearer_token", re.compile(r"(?i)(?<=bearer )[A-Za-z0-9._~+/=-]{16,}\b")),
    (
        "key_value_secret",
        re.compile(
            r"(?i)\b((?:api[_-]?key|x-api-key|access[_-]?token|auth[_-]?token|secret[_-]?key|client[_-]?secret|password|passwd)\s*[=:]\s*[\"']?)([^\s\"',;]{8,})"
        ),
    ),
]


def redact_secrets(text: str) -> tuple:
    """Replace credential-shaped substrings with [REDACTED:<type>].

    Returns (redacted_text, sorted list of redacted type names).
    """
    if not text:
        return text, []
    found = set()
    out = text
    for name, pattern in _SECRET_PATTERNS:
        if name == "key_value_secret":
            def _kv(m, _name=name):
                found.add(_name)
                return m.group(1) + f"[REDACTED:{_name}]"
            new = pattern.sub(_kv, out)
        else:
            new, n = pattern.subn(f"[REDACTED:{name}]", out)
            if n:
                found.add(name)
        out = new
    return out, sorted(found)
