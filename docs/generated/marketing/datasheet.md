---
doc-type: product-datasheet
doc-id: ds-001
version: 1.5.0
date: 2026-03-05
audience: marketing
source-tier: direct
hermes-version: 1.0.0
status: final
---

# Memories — Product Datasheet

## Overview

Memories is a local-first semantic memory service for AI assistants. It gives every AI tool you use — Claude Code, Claude Desktop, Codex, Cursor, ChatGPT, and anything that speaks HTTP or MCP — a shared, persistent knowledge base with sub-50ms hybrid search. Zero cloud dependency. Zero per-query cost.

## Core Capabilities

### Hybrid Search

Combines vector similarity (FAISS/ONNX) with keyword matching (BM25) using reciprocal rank fusion. Sub-50ms response times for typical workloads.

### Universal Compatibility

Works with Claude Code, Claude Desktop, Claude Chat, Codex, Cursor, ChatGPT, OpenClaw, and any HTTP or MCP client. MCP server included for native tool integration.

### Automatic Extraction

Learns from conversations automatically. The extraction pipeline uses LLM-assisted fact extraction with AUDN (Add/Update/Delete/Noop) lifecycle management to keep memories current and deduplicated.

### Multi-Auth (v1.5.0)

Prefix-scoped API keys with role-based access control. Three tiers — read-only, read-write, admin — let you isolate agents, control team access, and enforce least privilege. SHA-256 hashed keys. Zero migration from existing setups.

### Web UI

Built-in interface at `/ui` with five pages: Dashboard, Memories, Extractions, API Keys, and Settings. Dark and light themes. No build step required.

### Cloud Sync

S3-compatible automatic backups. Google Drive off-site backup support. Local-first by default with optional cloud redundancy.

## Integration Methods

| Method | Clients | Setup |
|--------|---------|-------|
| MCP Server | Claude Code, Claude Desktop, Codex, Cursor | npm install + settings.json |
| REST API | Any HTTP client | Docker container |
| Web UI | Browsers | Built-in at /ui |

## Security

| Feature | Detail |
|---------|--------|
| Authentication | API key auth (optional), multi-auth with scoped keys |
| Key storage | SHA-256 hashed, shown once at creation |
| Role-based access | Read-only, read-write, admin |
| Namespace isolation | Prefix-scoped keys filter all read/write operations |
| Rate limiting | Per-IP rate limiting on failed auth attempts |
| Network | CORS restricted to localhost, non-root Docker container |
| Input validation | Path traversal prevention, reserved field protection |
| Timing safety | Constant-time key comparison (hmac.compare_digest) |

## Architecture

| Component | Technology |
|-----------|-----------|
| API server | FastAPI (Python) |
| Vector search | FAISS with ONNX Runtime embeddings |
| Keyword search | BM25 |
| Fusion | Reciprocal rank fusion (RRF) |
| Storage | Local filesystem + SQLite |
| Extraction | Anthropic, OpenAI, Ollama, ChatGPT OAuth |
| Containerization | Docker with multi-target builds |
| MCP server | Node.js |

## Deployment

| Model | Description |
|-------|-------------|
| Local single-user | One Docker container, local volume, default settings |
| Multi-agent | One container, multiple scoped API keys per agent |
| Team shared | One container, role-based keys per teammate |

## System Requirements

| Requirement | Specification |
|-------------|--------------|
| Runtime | Docker |
| Storage | Local filesystem (data/) |
| Memory | Scales with number of stored memories |
| Network | Localhost by default; HTTPS recommended if exposed |

## Key Metrics

| Metric | Value |
|--------|-------|
| Search latency | Sub-50ms (typical) |
| Per-query cost | Zero (local embeddings) |
| Docker image size | 649 MB (ONNX, no PyTorch) |
| Test coverage | 346 tests passing |
| Auth tests | 96 dedicated tests |
| Efficacy delta | +0.86 (eval harness, 11 scenarios) |

## Version History

| Version | Date | Highlights |
|---------|------|------------|
| 1.5.0 | 2026-03-05 | Multi-auth: prefix-scoped API keys, role-based access |
| 1.4.0 | 2026-03-04 | Web UI v2: sidebar navigation, Arkos theme, 5 pages |
| 1.3.0 | 2026-03-04 | memory_extract MCP tool, Memories skill v2 |
| 1.2.0 | 2026-03-04 | Memories skill for Claude Code |
| 1.1.0 | 2026-03-03 | Efficacy eval harness |
| 1.0.0 | 2026-02-28 | First stable release |

## Getting Started

1. Clone the repository and run `docker compose up -d`
2. Verify with `curl http://localhost:8900/health`
3. Add your first memory via the API or Web UI
4. Connect your AI tools via MCP or REST

Full setup guide: 10-15 minutes from clone to working integration.
