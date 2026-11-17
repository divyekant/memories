<!-- APOLLO:START - Do not edit this section manually -->
## Project Conventions (managed by Apollo)
- Language: Python, package manager: pip
- Test framework: pytest
- Testing: TDD — write tests before implementation
- Run tests before every commit
- Commits: conventional style (feat:, fix:, chore:, etc.)
- Never auto-commit — always ask before committing
- Branch strategy: feature branches
- Code style: concise, comments: minimal
- Design before code: always run brainstorming/design phase first
- Design entry: invoke conductor skill for all design/brainstorm work
- Code review required before merging
- Maintain README.md
- Maintain CHANGELOG.md
- Maintain a Quick Start guide
- Maintain architecture documentation
- Track decisions in docs/decisions/
- Update docs on: feature
- Versioning: semver
- Check for secrets before committing
<!-- APOLLO:END -->

<!-- KALOS:START - Do not edit this section manually -->
## Design Standards (managed by Kalos)
- Primary: #b8960f (darkened gold)
- Secondary: #d4af37 (original Arkos gold)
- Semantic colors: success=#16A34A, warning=#CA8A04, error=#DC2626, info=#2563EB
- Font: Inter, system-ui, sans-serif
- Display font: Philosopher, serif
- Type scale: 1.25 from 16px base
- Spacing: 4px base unit, use multiples only
- Max 12 unique colors
- All colors must map to design tokens
- Max 2 font families
- All spacing must be multiples of base unit
- Min contrast ratio: 4.5 (WCAG AA)
- Require alt text on all images
- Active brand: dark
- 2 brand palettes configured (dark, light)
<!-- KALOS:END -->

## Rust server (v6)

The HTTP server is the Rust workspace in `server/`. The Python server (`app.py` and the modules it imports) is v5.19.0, kept unchanged as the rollback reference and the parity reference until task P7-3. The Apollo section above still says Python and pip. The CLI, the eval tools and `parity/` stay in Python (uv).

- Build, test and image commands: `server/README.md`. Toolchain from `server/rust-toolchain.toml`. Use `--locked`. Crate releases must be 2 days old or more.
- A change to a route keeps the v5 reply, or records the difference in `parity/intended.json` and a decision record in `docs/decisions/`.
- A route writes the audit row and the usage row where `app.py` does (`AppState::audit`, usage events).
- Environment variables: a new or newly read variable changes its state in `server/crates/memories-types/src/config.rs` and in `docs/rust-rewrite/configuration.md`.
- Version: `server/Cargo.toml`, `mcp-server/assets/backend/BACKEND_VERSION`, `mcp-server/package.json` and the two plugin manifests move together. `pyproject.toml` and `app.py` stay at 5.19.0.
- Image build: `docker build --build-context webui=webui server`. Release image: `.github/workflows/publish-image.yml`.
- Production runs Rust. Deploy, release and rollback steps: `docs/rust-rewrite/cutover-runbook.md`. Release status: `docs/rust-rewrite/release-readiness.md`.
