# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 6.0.x (Rust server) | Yes |
| 5.19.x (Python server, rollback reference) | Security fixes until the Python server is removed (task P7-3) |
| 5.18 and older | No |

## Report a vulnerability

Do not open a public issue for a security problem.

1. Use GitHub private vulnerability reporting: open the repository, then
   **Security**, then **Report a vulnerability**.
2. Give the version, the steps to reproduce the problem, and the effect.

You get a first reply in 7 days. A fix goes into a patch release. The advisory
names you if you agree.

## Scope

- The HTTP server (`server/`), the web UI (`webui/`), the MCP server and CLI
  (`mcp-server/`), the hooks and the Docker images.
- Not in scope: a problem that needs a valid admin API key, and a problem in a
  third-party package that has no effect on Memories. Report the second kind to
  the package owner.

## Operator rules

- Set `API_KEY` on every install that a network can reach.
- Do not put a key, a token or a password in an issue, a log or a commit.
