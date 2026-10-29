# The PreCompact flush sends clipped tool output

Date: 2026-10-29
Status: adopted

## Context

A context compaction replaces the transcript with a summary. A summary is
lossy. A probe on 2026-10-29 (one synthetic 20-turn session, 30 planted
details, production extraction) gave these results:

- A summary plus memory search had the answer for 23 of 30 questions.
- Extraction kept 11 of 11 durable facts.
- Extraction kept 0 of 6 details that appeared only in tool output, because
  the hooks send only text blocks to the extractor.

Two other options were rejected:

- A task-state checkpoint is also a summary, so it moves the loss and does
  not remove it.
- A search over raw transcript chunks found all 6 details, but one lookup
  cost about 1,060 tokens, against about 280 tokens for a memory lookup.

## Decision

The PreCompact flush adds clipped tool calls and tool output to its input,
and the pre-compact prompt keeps one-time results. The extractor pays the
extra tokens, not the main context. A later lookup stays at memory size.

- Send only built-in tools. Memory and messaging tools return recalled or
  Claude-written text, which must not be extracted again.
- Keep the head and the tail of each output, because errors and totals are
  usually at the end.
- Do not change the Stop hook or the Stop prompt.
- Let AUDN choose UPDATE when a new fact adds a specific detail. A second
  probe run with the new flush input showed why: the extractor wrote the
  pinned Stripe version and the exact mypy error, but AUDN chose NOOP
  against the shorter existing memories, so both details were lost.

## Consequences

- Tool output from before the flush window (the last 20 text messages) is
  still lost. To close this gap, the Stop hook must also send tool output,
  at extraction cost on every turn. This is not done.
- The Codex flush hook reads a different transcript format and does not send
  tool output yet.
- The full effect needs a server deploy, because the prompt change is on the
  server.
