# Transcript watcher — capture from hookless sessions

Claude Code's per-prompt and Stop hooks drive recall and capture in the
terminal CLI. **Claude Desktop does not fire per-turn or Stop hooks** — only
SessionStart runs — so desktop sessions recall memories at startup but never
*capture* what you decide. Web/mobile sessions run on isolated cloud VMs and
see none of the local setup at all.

The transcript watcher closes the capture gap for any **local** client that
writes JSONL transcripts but doesn't fire Stop hooks (Claude Desktop today;
client-agnostic by design). It is a small daemon that tails the transcript
directory and, when a session goes idle, sends the new messages to the
extraction endpoint.

## How it works

- Polls `~/.claude/projects/**/*.jsonl` (configurable) every `WATCHER_POLL_SECONDS`.
- A transcript untouched for `WATCHER_IDLE_SECONDS` (default 5 min) is treated as
  an idle burst and considered for capture.
- A **per-session watermark** (last captured message UUID, persisted in
  `~/.config/memories/watcher-state.json`) makes capture idempotent: only
  messages newer than the watermark are sent, across both idle bursts and
  daemon restarts. Short bursts advance the watermark without extracting, so
  they aren't re-evaluated every poll.
- The source is `claude-code/<project>`, with the project resolved from the
  transcript's `cwd` via the git common dir (worktree-aware) — the same scheme
  the hooks use, so watcher-captured and hook-captured memories share a source
  and recall together.
- Skips entirely when the backend health check fails.

This is **not** a substitute for the CLI hooks: it captures on idle, not at
every Stop, and it cannot recall mid-session the way the UserPromptSubmit hook
does. It is the best available capture path for clients Anthropic doesn't run
hooks in.

## Install (macOS, launchd)

```bash
# backend URL/key are read from ~/.config/memories/env or the environment
integrations/launchd/install-watcher.sh install
integrations/launchd/install-watcher.sh status
integrations/launchd/install-watcher.sh uninstall
```

## Run standalone

```bash
MEMORIES_URL=http://localhost:8900 MEMORIES_API_KEY=... \
  python scripts/transcript_watcher.py
```

## Config

| env | default | meaning |
|---|---|---|
| `WATCHER_TRANSCRIPT_DIR` | `~/.claude/projects` | directory tree of `*.jsonl` transcripts |
| `WATCHER_STATE_FILE` | `~/.config/memories/watcher-state.json` | watermark/cursor store |
| `WATCHER_IDLE_SECONDS` | `300` | quiet period before a burst is captured |
| `WATCHER_POLL_SECONDS` | `60` | scan interval |
| `WATCHER_MIN_CHARS` | `200` | skip bursts shorter than this |
| `WATCHER_SOURCE_PREFIX` | `claude-code` | source is `<prefix>/<project>` |

## Limitation worth knowing

The desktop per-turn hook gap is an upstream Claude Desktop limitation, not a
Memories bug — SessionStart recall proves the scripts, PATH, and backend all
work there; the app simply doesn't invoke per-turn or Stop hook events. The
watcher is the workaround until that's addressed upstream.
