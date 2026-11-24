# Rust server: events, webhooks and the web UI files

Date: 2026-11-16
Status: adopted (task P5-5 of the Rust rewrite plan)

## Routes

| Route | Behavior |
|---|---|
| `GET /events/recent` | The last `limit` events (default 50) of the history of 100, oldest first, as `{"events": [...], "count": n}`. The slice is the Python `events[-limit:]`: `0` gives all events, and a negative limit drops the first events. A scoped key sees the events of its sources and the events with no source. |
| `GET /events/stream` | Server-sent events, `text/event-stream; charset=utf-8`. The first chunk is `retry: 5000`. Each event is `id`, `event` and `data` (the data as Python `json.dumps`). After 30 s with no event, the server sends `: keepalive`. `event_type` filters by type; an empty value is no filter. |
| `POST /webhooks` | Checks the body as the Python `WebhookRequest` (422 before 403), then needs an admin key. `events` that is missing, `null` or `[]` gives all five types. Returns `id`, `url`, `events` and `created_at`. |
| `GET /webhooks` | Admin key. `{"webhooks": [...]}`, oldest first. |
| `DELETE /webhooks/{webhook_id}` | Admin key. `{"deleted": true}` or `{"deleted": false}`. The ID must be the same string: `01` does not match `1`. |
| `GET /ui` | `webui/index.html`, or 404 `UI not found`. No key. |
| `GET /ui/static/{path}` | A file of `webui/`, or 404 `Not Found`. No key. A path with `..`, a root, or a symbolic link out of the folder gives 404. |

The event bus is the P3-1 bus (`memories_engine::events`), now held by
`AppState`. The writer sends `memory.added`, `memory.updated`,
`memory.deleted` and `memory.linked` to it. The extract worker and the
synchronous fallback (P4-3) send `extraction.completed` to the same bus, with
the Python fields `job_id`, `source`, `stored_count`, `updated_count` and
`conflict_count`.

None of these routes calls `_audit` or writes a usage row in Python, so none
does here.

## Differences from the Python server

| Python v5.19.0 | Rust server | Reason |
|---|---|---|
| A webhook is in RAM. A restart loses it, and the IDs start at 1 again. | A webhook is a row of the table `webhooks` in `state.db` (`AUTOINCREMENT`). It stays after a restart, and an ID is not given out again. | Plan, section "Wire rules". |
| One webhook call, with a time limit of 5 s. | Up to 3 tries for each call, with a time limit of 5 s each. A try is repeated after a connection error or an HTTP 5xx reply, after 1 s and then 2 s. A 4xx reply or a URL that is not valid is not repeated. At most 32 calls run at one time. As in Python, a call does not follow a redirect. | Plan: "Limit the retries of a webhook call". |
| The calls of one event go out in sequence, and the events go out near the order of their emit. | Each call is a task of its own, so a receiver can get event N+1 before a retry of event N. | Retries for one receiver must not stop the calls to the other receivers. |
| The default `events` list is `list(EVENT_TYPES)`, a set, so the order changes with `PYTHONHASHSEED`. | The order of `EVENT_TYPES`. | A fixed order. |
| A stream reader that falls 256 events behind is removed, and then gets only keepalives. | The reader loses the oldest events and continues. | Same events for a reader that keeps up. |
| `json.dumps` keeps the key order of the event data. | The keys of the event data come in alphabetical order (`serde_json` with no `preserve_order`). | A client reads the data as JSON; the order has no meaning. |
| An open event stream stays open at shutdown. | Each event stream ends when the shutdown starts, so the server can write its pending rows and stop. | A stream must not hold the shutdown. |
| Static files have `ETag`, `Last-Modified` and range support. | A static file has `Content-Type` and the bytes only. | The web UI does not use these headers. |

## Web UI files in the image

`UI_DIR` (default `/app/webui`) names the folder. The image build context is
`server/`, so the image takes the files from a second build context:

```bash
docker build --build-context webui=webui -t ghcr.io/divyekant/memories:rust-local server
```

The CI job `rust-image` uses this command. A workflow that publishes the
Rust image (task P7-2) must use it too.

## Exit check

`server/scripts/parity_events.sh` (CI job `rust-parity`):

1. Starts the Rust server on an empty data folder and replays the golden
   seed writes (the link cases are not in the last 20 events of the golden
   case), the two key cases and `usage-headers-add`.
2. Compares the 8 golden cases of this task: `events-recent`,
   `events-stream`, `webhooks-list`, `webhooks-list-ro-403`,
   `webhook-create`, `webhook-delete`, `ui-page` and `ui-static`.
3. Runs `server/scripts/webui_check.cjs`: Chromium opens each of the six
   pages with the admin key. A page fails if it shows no content, has a
   console error or a script error, or if a request to the server fails or
   gets HTTP 400 or more.
