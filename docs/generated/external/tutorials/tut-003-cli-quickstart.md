---
id: tut-003
type: tutorial
audience: external
topic: CLI Quick Start
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Tutorial: CLI Quick Start

In this tutorial, you will install the Memories CLI, connect it to your server, and run your first commands to add, search, and organize memories from the terminal.

**Time:** 5 minutes

**What you'll build:** A working CLI setup connected to your Memories server, with your first memory stored and searchable.

**Prerequisites:**
- A Memories server running (default: `http://localhost:8900`)
- Python 3.10 or later installed
- Your API key (if auth is enabled on your server)

## Step 1: Install the CLI

Install the Memories package, which includes the CLI:

```bash
pip install memories
```

Verify the installation:

```bash
memories --version
```

**Expected output:**

```
memories, version 1.6.0
```

You should also see the full help text by running:

```bash
memories --help
```

**Expected output:**

```
Usage: memories [OPTIONS] COMMAND [ARGS]...

  Memories -- local semantic memory for AI assistants.

Options:
  --url TEXT       Memories server URL
  --api-key TEXT   API key for authentication
  --json           Force JSON output
  --pretty         Force human-readable output
  --version        Show the version and exit.
  --help           Show this message and exit.

Commands:
  add         Add a memory.
  admin       Server administration commands.
  auth        Manage extraction provider authentication.
  backup      Backup and restore operations.
  batch       Batch operations on memories.
  config      Manage CLI configuration.
  count       Count memories.
  delete      Delete a memory by ID.
  delete-by   Delete memories by source pattern or prefix.
  extract     Memory extraction from transcripts.
  folders     List source folders with counts.
  get         Get a memory by ID.
  is-novel    Check if text is novel compared to existing memories.
  list        List memories.
  search      Search memories by semantic similarity.
  sync        Remote sync operations.
  upsert      Insert or update a memory by key.
```

## Step 2: Configure Your Connection

Tell the CLI where your server is and provide your API key:

```bash
memories config set url http://localhost:8900
```

**Expected output:**

```
Set url in /Users/you/.config/memories/config.json
```

```bash
memories config set api_key YOUR_API_KEY
```

**Expected output:**

```
Set api_key in /Users/you/.config/memories/config.json
```

To verify your configuration:

```bash
memories config show
```

**Expected output:**

```
            url: http://localhost:8900  (from file)
        api_key: YOUR_API****  (from file)
```

The `(from file)` annotation tells you the value is coming from your config file. If you also had an environment variable set, the config file takes precedence, and the output would reflect the active source.

## Step 3: Verify the Connection

Check that the CLI can reach your server:

```bash
memories admin health
```

**Expected output:**

```
Status: ok
Version: 1.6.0
Memories: 42
```

If you see this, your CLI is connected and ready to use.

## Step 4: Add Your First Memory

Add a memory with a source prefix:

```bash
memories add -s test/quickstart/ "The Memories CLI is working and connected to my server"
```

**Expected output:**

```
Added memory #43
```

The `-s` flag sets the source prefix, which organizes memories into folders. The number after `#` is the memory's ID.

## Step 5: Search for Your Memory

Search using natural language:

```bash
memories search "CLI working"
```

**Expected output:**

```
[43] (87%) test/quickstart/
  The Memories CLI is working and connected to my server
```

The result shows:
- `[43]` -- the memory ID
- `(87%)` -- the similarity score
- `test/quickstart/` -- the source prefix
- The memory text below

You can adjust the number of results with `-k`:

```bash
memories search "CLI" -k 3
```

## Step 6: List Your Folders

See how your memories are organized:

```bash
memories folders
```

**Expected output:**

```
  12  claude-code/myapp/
   8  learning/python/
   1  test/quickstart/
  21  (total)
```

Your `test/quickstart/` folder should appear with a count of 1.

## What You Have Now

After completing this tutorial:

- The Memories CLI is installed and on your PATH
- Your server URL and API key are stored in `~/.config/memories/config.json`
- You have verified the connection with a health check
- You have added and searched for a memory from the terminal
- You know how to view folder organization

## Troubleshooting

### "Cannot connect to server" (exit code 3)

The CLI cannot reach your Memories server.

1. Check that your server is running:
   ```bash
   curl -s http://localhost:8900/health
   ```
2. Verify the URL in your config:
   ```bash
   memories config show
   ```
3. If your server is on a different port or host, update the URL:
   ```bash
   memories config set url http://your-host:your-port
   ```

### "Authentication failed" (exit code 4)

Your API key is missing or incorrect.

1. Check that your key is set:
   ```bash
   memories config show
   ```
2. If the `api_key` line shows `not set`, add it:
   ```bash
   memories config set api_key YOUR_API_KEY
   ```
3. If the key is set but still failing, verify it works directly:
   ```bash
   curl -s http://localhost:8900/health -H "X-API-Key: YOUR_API_KEY"
   ```

### "command not found: memories"

The CLI is not on your PATH.

1. Check that you installed it:
   ```bash
   pip show memories
   ```
2. If installed but not found, your pip scripts directory may not be on your PATH. Try:
   ```bash
   python -m memories --help
   ```
3. Or add pip's script directory to your PATH:
   ```bash
   export PATH="$PATH:$(python -c 'import sysconfig; print(sysconfig.get_path("scripts"))')"
   ```

### Output looks like JSON when I expected pretty text

The CLI auto-detects whether it is running in an interactive terminal. If you are running inside a script, redirecting output, or using a non-TTY environment, it defaults to JSON.

To force human-readable output:

```bash
memories --pretty search "test"
```

## Next Steps

- [Agent CLI Integration](tut-004-cli-agent-integration.md) -- use the CLI from AI agents and automated scripts
- [CLI Feature Documentation](../features/feat-002-cli.md) -- full command reference and configuration options
- [CLI Recipes](../cookbook.md#cli-recipes) -- ready-to-use patterns for common workflows
