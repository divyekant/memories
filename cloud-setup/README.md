# claude-cloud-setup

Bootstrap config that makes **Memories** (and a sane CLAUDE.md) active in every
**Claude Code on the web** (cloud) session — without running any backend inside
the ephemeral sandbox.

A cloud session is a fresh, throwaway container. Nothing from your laptop's
`~/.claude` carries over, and stateful services (databases, vector stores) can't
live there. So this repo installs only the **thin client** — hooks + an MCP
bridge + a global `CLAUDE.md` — and points it at your **externally hosted**
Memories backend over HTTPS.

```
┌─ claude-cloud-setup (this repo) ─────────────────────────┐
│  setup.sh · templates/CLAUDE.md · settings.json · mcp.json │
└───────────────────────────┬───────────────────────────────┘
                            │ cloned + run by the Environment's setup script
                            ▼
┌─ Cloud Environment (configured once, in the web UI) ──────┐
│  Setup script:  clone this repo → run setup.sh             │
│  Env vars:      MEMORIES_URL, MEMORIES_API_KEY, SETUP_GH_TOKEN │
│  Network:       Custom allowlist → your backend domain     │
└───────────────────────────┬───────────────────────────────┘
                            │ first session (cached ~7 days)
                            ▼
┌─ Sandbox (ephemeral) ─────────────────────────────────────┐
│  ~/.claude/hooks/memory/   recall every prompt, capture on stop │
│  ~/.claude/CLAUDE.md       behavioral rules                 │
│  ~/.claude/memories-mcp/   memory_search / memory_add tools │
│        │  curl / fetch over HTTPS                           │
│        ▼                                                    │
│   YOUR HOSTED BACKEND  ← data lives here, not in the sandbox │
└────────────────────────────────────────────────────────────┘
```

---

## What gets installed

| Component | Path in sandbox | Role |
|---|---|---|
| **Hooks** | `~/.claude/hooks/memory/*.sh` | bash + `curl`; automatic recall (SessionStart/UserPromptSubmit) and capture (Stop/SubagentStop). The reliable core — works with zero model involvement. |
| **settings.json** | `~/.claude/settings.json` | registers the hooks (merged if a file already exists) |
| **CLAUDE.md** | `~/.claude/CLAUDE.md` | global behavioral rules: memory usage, cloud caveats, GitHub, skills |
| **MCP bridge** | `~/.claude/memories-mcp/` | Node stdio server exposing `memory_search` / `memory_add` as tools. Optional — the backend is REST-only, so the model reaches it through this bridge. |

---

## Prerequisites

1. A **Memories backend reachable over the internet** (HTTPS), with an API key.
   Verify: `curl -H "X-API-Key: $KEY" https://your-backend/health`.
2. A **GitHub PAT** (`SETUP_GH_TOKEN`) with read access to this repo and to the
   source repo (`divyekant/memories`) — needed because the sandbox clones them
   privately during setup.
3. Access to **Claude Code on the web** with permission to create/edit an
   Environment.

---

## Setup (once per environment)

### 1. Environment variables
In the Claude Code web UI → your Environment → settings, add (see `.env.example`):

```
MEMORIES_URL=https://your-backend
MEMORIES_API_KEY=your-backend-key
SETUP_GH_TOKEN=ghp_your_pat
```

> No secrets store exists yet — these are visible to anyone who can edit the
> environment. Use a least-privilege, rotatable PAT and backend key.

### 2. Network policy
Set the network access level to **Custom** and allowlist:

```
your-backend-domain.com
github.com
```

Without the backend domain, hooks `curl` it, fail, and trip a circuit breaker —
memory silently does nothing. (`github.com` lets the setup script clone.)

### 3. Setup script
Paste the contents of [`environment-setup-script.sh`](./environment-setup-script.sh)
into the Environment's **Setup script** field. It clones this repo and runs
`setup.sh`, which installs everything above.

That's it. Start a session — `setup.sh` runs on the first session, its files are
cached (~7 days, "files persist, not processes"), and every later session loads
them at launch. Memory recall/capture is active from the next prompt.

---

## How it works (and what it costs)

- **Per-session install, not per-session backend.** `setup.sh` only writes files
  and builds the Node bridge; it starts no long-lived service. The backend lives
  on your host and persists data across all sessions.
- **Hooks are the dependable path; MCP is the extra.** Hooks are self-contained
  bash that only need `MEMORIES_URL` + the allowlist. The MCP bridge needs Node +
  `npm install` + tool registration; if any of that fails, hooks keep working.
- **Cost:** the cloud setup adds **$0** — private GitHub repos are free, and a
  cloud session bills the same usage/token budget as any Claude Code use (no
  separate sandbox fee). Your only recurring cost is the **hosted backend** you
  already run (plus any LLM API spend it makes for extraction/embeddings).

---

## Verifying it works

In a cloud session:

```bash
# backend reachable from inside the sandbox?
curl -sf -H "X-API-Key: $MEMORIES_API_KEY" "$MEMORIES_URL/health" && echo OK

# hooks installed and registered?
ls ~/.claude/hooks/memory/ && jq '.hooks | keys' ~/.claude/settings.json

# setup log
cat ~/.config/memories/hook.log 2>/dev/null | tail
```

Then ask something like *"what did we decide about X?"* — recall context should
be injected before the answer.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Recall always empty | backend domain not allowlisted, or wrong `MEMORIES_URL` | add domain to Custom network policy; check env vars |
| `setup.sh` clone fails | missing/invalid `SETUP_GH_TOKEN`, or `github.com` not allowlisted | set a valid read PAT; allowlist `github.com` |
| Hooks present but no effect | `MEMORIES_DISABLED` set, or circuit breaker open | unset the var; confirm backend `/health` |
| MCP tools missing | `node` absent or `npm install` failed | set `INSTALL_MCP=0` and rely on hooks, or fix Node availability |

---

## Updating

Edit this repo (`setup.sh`, `templates/*`) and push. The next session whose
setup-script cache has expired (or a re-created environment) picks up changes.
To force it, re-create or re-run the environment's setup.

## Layout

```
claude-cloud-setup/
├── README.md
├── environment-setup-script.sh   # paste into the Environment's Setup script field
├── setup.sh                      # does the install inside the sandbox
├── .env.example                  # env vars to set in the web UI
└── templates/
    ├── CLAUDE.md                 # installed to ~/.claude/CLAUDE.md
    ├── settings.json             # hook registrations (__HOOK_DIR__ is substituted)
    └── mcp.json                  # MCP bridge config (__BRIDGE_DIR__ is substituted)
```
