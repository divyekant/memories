# Claude Code Integration

> Use FAISS Memory with [Claude Code](https://docs.anthropic.com/claude/docs/claude-code) for persistent semantic memory across sessions.

---

## Quick Start

### 1. Ensure FAISS Memory is Running

```bash
# Check service health
curl http://localhost:8900/health

# If not running, start it:
cd /path/to/faiss-memory
docker compose up -d
```

### 2. Use Built-in HTTP Tool

Claude Code can make HTTP requests directly. No configuration needed!

**Example prompt:**
```
Search my memory for authentication patterns:

{
  "method": "POST",
  "url": "http://localhost:8900/search",
  "headers": {"Content-Type": "application/json"},
  "body": {
    "query": "How do we handle authentication?",
    "k": 3
  }
}
```

Claude Code will execute the HTTP request and use the results.

---

## Integration Methods

### Method 1: Direct HTTP Calls (Recommended)

**Pros:**
- ✅ Works immediately (no setup)
- ✅ Full control over queries
- ✅ No additional dependencies

**Cons:**
- ❌ Verbose (need to specify URL/headers each time)
- ❌ No auto-completion

**Example session:**

```bash
claude "Before we start, search my memory for React best practices:

curl -X POST http://localhost:8900/search \\
  -H 'Content-Type: application/json' \\
  -d '{
    \"query\": \"React best practices and patterns\",
    \"k\": 5
  }'

Then use those patterns to refactor src/components/UserProfile.tsx"
```

---

### Method 2: MCP Server (Recommended for Daily Use)

The MCP server is available and exposes typed memory tools directly to Claude Code.

**Setup:**

1. Install dependencies:

```bash
cd /path/to/memories/mcp-server
npm install
```

2. Add server config:

```json
// ~/.claude/settings.json
{
  "mcpServers": {
    "faiss-memory": {
      "command": "node",
      "args": ["/path/to/memories/mcp-server/index.js"],
      "env": {
        "FAISS_URL": "http://localhost:8900",
        "FAISS_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

3. Restart Claude Code.

**Tools exposed through MCP:**

- `memory_search`
- `memory_add`
- `memory_delete`
- `memory_list`
- `memory_stats`
- `memory_is_novel`

---

### Method 3: OpenClaw Skill (For OpenClaw Users)

**If using Claude Code via OpenClaw:**

```bash
# Source the helper functions
source ~/.openclaw/skills/faiss-memory/helpers.sh

# Claude Code can call these functions
fmem-search "authentication patterns" 5
fmem-add "Use JWT with 15min expiry" "auth-decisions.md"
fmem-is-novel "New authentication approach"
```

**In Claude Code prompt:**
```bash
claude "Check my memory for error handling patterns using:
fmem-search 'error handling best practices' 5

Then apply those patterns to fix errors in server/api/users.ts"
```

---

## Common Workflows

### 1. Project Initialization

When starting work on a project, load context:

```bash
claude "Search my memory for:
1. This project's architecture decisions
2. Coding standards we follow
3. Common patterns I prefer

Then summarize them for me.

Use: curl -X POST http://localhost:8900/search -H 'Content-Type: application/json' -d '{\"query\": \"[YOUR_QUERY]\", \"k\": 5}' for each search."
```

### 2. Pattern Recall

Before implementing a feature:

```bash
claude "Before building the user authentication flow:
1. Search memory for: 'authentication patterns we've used'
2. Search memory for: 'JWT token handling best practices'
3. Then design the auth flow using those patterns

Search command: curl -X POST http://localhost:8900/search -H 'Content-Type: application/json' -d '{\"query\": \"...\", \"k\": 3}'"
```

### 3. Avoiding Past Mistakes

```bash
claude "I'm getting a 'Connection refused' error with Redis.
First, search my memory for similar Redis connection issues we've solved before:

curl -X POST http://localhost:8900/search -H 'Content-Type: application/json' -d '{\"query\": \"Redis connection refused error\", \"k\": 5}'

Then help me debug based on past solutions."
```

### 4. Storing New Learnings

After fixing a bug:

```bash
claude "We just fixed the Redis connection issue by adding REDIS_URL env var.
Store this in memory:

curl -X POST http://localhost:8900/memory/add -H 'Content-Type: application/json' -d '{
  \"text\": \"Fixed: Redis connection refused error. Solution: Ensure REDIS_URL env var is set in docker-compose.yml. Default redis://redis:6379 doesn't work in some Docker network configs.\",
  \"source\": \"bug-fixes/redis-2026-02-12.md\"
}'

Confirm it was added."
```

### 5. Duplicate Detection

Before storing a new pattern:

```bash
claude "Check if this pattern is already in memory:

curl -X POST http://localhost:8900/memory/is-novel -H 'Content-Type: application/json' -d '{
  \"text\": \"Use React Query for all API calls instead of Redux\",
  \"threshold\": 0.85
}'

If novel, add it. If not, show me the existing similar memory."
```

---

## Best Practices

### 1. Start Sessions with Memory Search

**Always search memory first** before implementing:

```bash
claude "Before we start:
1. Search memory for '[PROJECT_NAME] architecture decisions'
2. Search memory for '[FEATURE] implementation patterns'
3. Load that context, then help me with [TASK]"
```

### 2. Store Decisions Immediately

**After making important decisions:**

```bash
claude "We just decided to use Prisma for the ORM.
Store this decision with reasoning:

curl -X POST http://localhost:8900/memory/add -H 'Content-Type: application/json' -d '{
  \"text\": \"Decision: Use Prisma ORM for database access. Reasons: (1) Type-safe queries, (2) Great TypeScript support, (3) Migration tooling, (4) Better dev experience than TypeORM. Date: 2026-02-12\",
  \"source\": \"project/architecture-decisions.md\",
  \"metadata\": {\"type\": \"decision\", \"date\": \"2026-02-12\"}
}'"
```

### 3. Use Specific Queries

**Bad:**
```json
{"query": "authentication"}
```

**Good:**
```json
{"query": "How should I implement JWT token refresh flow?"}
```

Specific queries return better results.

### 4. Check Novelty Before Adding

Avoid duplicates:

```bash
claude "Before storing '[NEW_PATTERN]', check if we already have this:

curl -X POST http://localhost:8900/memory/is-novel -H 'Content-Type: application/json' -d '{\"text\": \"[NEW_PATTERN]\", \"threshold\": 0.85}'

Only add if novel."
```

### 5. Tag Your Memories

Use metadata for filtering (future feature):

```json
{
  "text": "...",
  "source": "decisions.md",
  "metadata": {
    "type": "decision",
    "priority": "high",
    "project": "user-auth",
    "date": "2026-02-12"
  }
}
```

---

## Example Prompts

### Architecture Review

```bash
claude "I'm designing a new feature: [FEATURE_DESCRIPTION]

Before we start:
1. Search memory for similar features we've built
2. Search for architecture patterns we prefer
3. Search for common pitfalls to avoid

Commands:
curl -X POST http://localhost:8900/search -H 'Content-Type: application/json' -d '{\"query\": \"similar features\", \"k\": 5}'
curl -X POST http://localhost:8900/search -H 'Content-Type: application/json' -d '{\"query\": \"architecture patterns\", \"k\": 5}'
curl -X POST http://localhost:8900/search -H 'Content-Type: application/json' -d '{\"query\": \"common pitfalls\", \"k\": 5}'

Then design the architecture using that context."
```

### Bug Triage

```bash
claude "I'm seeing error: [ERROR_MESSAGE]

First, search memory for:
1. This exact error
2. Similar error patterns
3. Related debugging steps we've used

curl -X POST http://localhost:8900/search -H 'Content-Type: application/json' -d '{\"query\": \"[ERROR_MESSAGE]\", \"k\": 5}'

Then help me debug based on past solutions."
```

### Code Review

```bash
claude "Review the changes in [FILE_PATH]

Before reviewing:
1. Search memory for our code standards
2. Search for patterns we prefer for [FEATURE_TYPE]
3. Search for common mistakes to avoid

curl -X POST http://localhost:8900/search -H 'Content-Type: application/json' -d '{\"query\": \"code standards\", \"k\": 3}'
curl -X POST http://localhost:8900/search -H 'Content-Type: application/json' -d '{\"query\": \"[FEATURE_TYPE] patterns\", \"k\": 3}'

Then review the code against those standards."
```

---

## Configuration

### Optional: Create Bash Aliases

Add to `~/.bashrc` or `~/.zshrc`:

```bash
# FAISS Memory shortcuts
alias fmem-search='curl -X POST http://localhost:8900/search -H "Content-Type: application/json" -d'
alias fmem-add='curl -X POST http://localhost:8900/memory/add -H "Content-Type: application/json" -d'
alias fmem-novel='curl -X POST http://localhost:8900/memory/is-novel -H "Content-Type: application/json" -d'
alias fmem-stats='curl -s http://localhost:8900/stats | jq'
```

**Usage:**
```bash
fmem-search '{"query": "authentication patterns", "k": 3}' | jq
fmem-add '{"text": "Use bcrypt for passwords", "source": "security.md"}' | jq
```

### Optional: Claude Code Config

Create `~/.config/claude/tools.sh`:

```bash
#!/bin/bash
# Helper functions for Claude Code

fmem_search() {
    local query="$1"
    local k="${2:-5}"
    curl -s -X POST http://localhost:8900/search \
        -H "Content-Type: application/json" \
        -d "{\"query\": \"$query\", \"k\": $k}" | jq
}

fmem_add() {
    local text="$1"
    local source="$2"
    curl -s -X POST http://localhost:8900/memory/add \
        -H "Content-Type: application/json" \
        -d "{\"text\": \"$text\", \"source\": \"$source\"}" | jq
}

export -f fmem_search fmem_add
```

Source in your shell: `source ~/.config/claude/tools.sh`

---

## Troubleshooting

### Issue: "Connection refused" errors

**Solution:**
```bash
# Check if service is running
docker ps | grep faiss-memory

# If not running:
cd /path/to/faiss-memory
docker compose up -d

# Check health
curl http://localhost:8900/health
```

### Issue: Search returns no results

**Solution:**
```bash
# Check if index has memories
curl http://localhost:8900/stats | jq '.total_memories'

# If 0, add some memories first
curl -X POST http://localhost:8900/memory/add \
  -H "Content-Type: application/json" \
  -d '{"text": "Test memory", "source": "test.md"}'
```

### Issue: Claude Code says "Tool not available"

**Solution:**
This usually means MCP is not loaded or config path is wrong.

```bash
# 1) Confirm MCP config contains the faiss-memory server
cat ~/.claude/settings.json

# 2) Verify node deps exist
cd /path/to/memories/mcp-server && npm install

# 3) Restart Claude Code
```

If you intentionally do not use MCP, fallback to direct HTTP calls still works.

### Issue: Slow responses

**Solution:**
```bash
# Check service health
curl http://localhost:8900/stats

# If large index, consider:
# 1. Reducing k value (fewer results)
# 2. Increasing Docker memory limit
# 3. Pruning old/irrelevant memories
```

---

## Advanced Usage

### Batch Memory Loading

Load multiple files at once:

```bash
claude "Load all my project documentation into memory:

for file in docs/*.md; do
  echo \"Adding \$file...\"
  content=\$(cat \"\$file\")
  curl -X POST http://localhost:8900/memory/add \\
    -H 'Content-Type: application/json' \\
    -d \"{\\\"text\\\": \\\"\$content\\\", \\\"source\\\": \\\"\$file\\\"}\"
  sleep 0.5
done

Then confirm how many memories were added."
```

### Custom Similarity Thresholds

Adjust novelty detection sensitivity:

```json
{
  "text": "New pattern to check",
  "threshold": 0.90  // Higher = stricter (0.85 default)
}
```

- **0.95+** - Only add if very different
- **0.85** - Default (good balance)
- **0.75** - More lenient (allow similar entries)

---

## Integration Checklist

- [ ] FAISS Memory service running (`docker ps | grep faiss-memory`)
- [ ] Health check passing (`curl http://localhost:8900/health`)
- [ ] Initial memories loaded (project docs, standards, decisions)
- [ ] Tested search with sample query
- [ ] Tested add with sample memory
- [ ] Optional: Bash aliases created
- [ ] Optional: Helper functions configured
- [ ] Documented in project README

---

## Next Steps

1. **Load your project context** - Add existing docs, decisions, standards
2. **Start using in sessions** - Begin every Claude Code session with memory search
3. **Store new learnings** - Add memories after solving problems
4. **Refine queries** - Experiment with different search queries
5. **Use MCP by default** - Prefer MCP tools over manual curl once configured

---

## Resources

- 📖 [FAISS Memory API Docs](../docs/API.md)
- 💡 [Example Prompts](../examples/claude-code/)
- 🐛 [Troubleshooting](../docs/TROUBLESHOOTING.md)
- 🚀 [Advanced Usage](../docs/ADVANCED.md)

---

**Built for Claude Code users who want persistent memory across sessions** 🧠
