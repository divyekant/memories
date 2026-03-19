# Web UI v2 Design

Date: 2026-03-04
Status: Approved
Design artifact: `docs/designs/memories-ui-v2.pen`

## Problem

The current `/ui` is minimal and doesn't support the growing feature set — multi-key management, extraction monitoring, theme switching. Need a scalable UI that serves both search/browse and health monitoring use cases equally.

## Decisions

### Layout: Sidebar Navigation
200px text sidebar with 5 sections: Dashboard, Memories, Extractions, API Keys, Settings. Chosen over top-nav and icon-rail alternatives for clarity at scale.

### Tenant Model: Filter, Not Navigation
API key switching is a dropdown in the top bar, not a navigation concept. Keys are managed via a dedicated CRUD page. Scales to 10+ keys without cluttering nav.

### Theme: Arkos-Inspired Dark/Light
- **Dark** (default): Gold #d4af37 on #0a0a0a, elevated surfaces #111111, text #e5e5e5
- **Light** (A+C combo): Darkened gold #b8960f on warm cream #FAF9F6, elevated #FFFEF9, text #2C2418
- OS `prefers-color-scheme` default + manual override via System/Dark/Light toggle, saved to localStorage

### Tech Stack
Vanilla JS + CSS3 + HTML5. No framework, no build step. CSS custom properties for theming. Single-page app with client-side routing.

## Pages

### Dashboard
- 4 stat cards: Total Memories, Extractions (7d), Search Queries (24h), Active Keys
- Activity feed (recent operations with timestamps)

### Memories
- **Default view**: List + detail panel. Left panel has filter bar (source prefix, date range) and view toggle (List/Grid). Right panel shows full text, metadata (ID, created, similarity score), and Delete/Edit actions.
- **Alternate view**: Card grid, toggled via List/Grid switch.

### Extractions
- 4 stat cards: Jobs (7d), Success Rate, Avg Facts/Job, Running
- Job history table: Status (color-coded badges), Source, AUDN counts (Add/Update/Delete/Skip), Duration
- Drill-down to individual job details

### API Keys
- Full CRUD: Create, revoke, rename
- Permissions: read-only, read-write, admin (color-coded badges)
- Per-key usage stats and last-used timestamp
- "+ Create Key" action in top bar

### Settings
- **Extraction Provider**: provider name, model, enabled status
- **Server Info**: embedder model, index size, backup count, auto-reload status
- **Appearance**: System/Dark/Light toggle
- **Danger Zone**: Export all memories, Reset index (with confirmation)

## Reusable Components

| Component | Purpose |
|-----------|---------|
| Sidebar | Nav + logo + theme toggle |
| TopBar | Page title + key filter dropdown + search |
| StatCard | Metric display (value + label) |
| ActivityRow | Single activity feed entry |
| MemoryListItem | Memory preview in list view |

## Design Tokens

Managed via Kalos (`.kalos.yaml`). Two brand palettes (dark, light) with shared typography (Inter), spacing (4px base), and radii (0/4/8/12/9999).
