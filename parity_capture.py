"""Capture middleware for the parity kit.

If PARITY_CAPTURE_DIR is set, each request and its reply go as one NDJSON line
to <dir>/capture-<UTC date>.ndjson. The default is off. The file holds memory
text, so it gets mode 0600 and must stay on the host. Key headers and new raw
API keys are redacted.

A request never waits for the file: a background thread writes the records.
If the queue is full or the daily file is at its size limit, the record is lost.
"""

import json
import os
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

REDACTED = "<redacted>"
MAX_BODY = 4 * 1024 * 1024
QUEUE_SIZE = 1000
_KEEP_HEADERS = ("content-type", "x-api-key", "authorization")
_SECRET_HEADERS = ("x-api-key", "authorization")
_SKIP_PREFIXES = ("/ui",)  # static web UI files are not API traffic


def decode_body(raw: bytes, content_type: str):
    """Return (body_type, value): "json", "ndjson", "text" or "none". parity/kit.py uses the same rules."""
    if not raw:
        return "none", None
    text = raw.decode("utf-8", errors="replace")
    try:
        if "ndjson" in content_type:
            return "ndjson", [json.loads(line) for line in text.splitlines() if line.strip()]
        if "json" in content_type:
            return "json", json.loads(text)
    except ValueError:
        pass
    return "text", text


def _append(buf: bytearray, chunk: bytes) -> bool:
    """Add a chunk up to MAX_BODY. Return False if part of the chunk was dropped."""
    room = MAX_BODY - len(buf)
    buf.extend(chunk[:max(room, 0)])
    return len(chunk) <= room


class ParityCaptureMiddleware:
    def __init__(self, app, capture_dir: str, max_mb_per_day: float = None):
        self.app = app
        self.dir = Path(capture_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        if max_mb_per_day is None:
            max_mb_per_day = float(os.getenv("PARITY_CAPTURE_MAX_MB", "1024"))
        self.max_bytes = int(max_mb_per_day * 1024 * 1024)
        self.dropped = 0
        self._queue = queue.Queue(maxsize=QUEUE_SIZE)
        threading.Thread(target=self._writer, name="parity-capture", daemon=True).start()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith(_SKIP_PREFIXES):
            return await self.app(scope, receive, send)

        start = time.perf_counter()
        req = {"body": bytearray(), "truncated": False}
        resp = {"status": 0, "content_type": "", "body": bytearray(), "truncated": False,
                "stream": False, "queued": False}

        async def recv():
            message = await receive()
            if message["type"] == "http.request":
                if not _append(req["body"], message.get("body", b"")):
                    req["truncated"] = True
            return message

        async def snd(message):
            if message["type"] == "http.response.start":
                resp["status"] = message["status"]
                for k, v in message.get("headers", []):
                    if k.decode("latin-1").lower() == "content-type":
                        resp["content_type"] = v.decode("latin-1")
                resp["stream"] = resp["content_type"].startswith("text/event-stream")
            elif message["type"] == "http.response.body" and not resp["queued"]:
                if not _append(resp["body"], message.get("body", b"")):
                    resp["truncated"] = True
                # An SSE reply never ends: keep the first chunk that has data.
                if (resp["stream"] and resp["body"]) or not message.get("more_body", False):
                    self._enqueue(scope, req, resp, start)
            await send(message)

        await self.app(scope, recv, snd)

    def _enqueue(self, scope, req, resp, start):
        resp["queued"] = True
        item = (scope["method"], scope["path"], scope.get("query_string", b""), list(scope.get("headers", [])),
                bytes(req["body"]), req["truncated"], resp["status"], resp["content_type"],
                bytes(resp["body"]), resp["truncated"], round((time.perf_counter() - start) * 1000.0, 1),
                datetime.now(timezone.utc))
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self.dropped += 1

    def _writer(self):
        while True:
            item = self._queue.get()
            try:
                self._write(*item)
            except Exception:  # a capture fault must never stop the writer
                self.dropped += 1
            finally:
                self._queue.task_done()

    def _write(self, method, path, query, raw_headers, req_body, req_trunc, status, resp_ct, resp_body,
               resp_trunc, elapsed_ms, now):
        headers = {}
        for k, v in raw_headers:
            name = k.decode("latin-1").lower()
            if name in _KEEP_HEADERS or name.startswith("x-memories-"):
                headers[name] = REDACTED if name in _SECRET_HEADERS and v else v.decode("latin-1")
        request = {"method": method, "path": path, "query": query.decode("latin-1"), "headers": headers}
        req_type, req_val = decode_body(req_body, headers.get("content-type", ""))
        if req_type == "json":
            request["json"] = req_val
        elif req_type != "none":
            request["body"] = req_body.decode("utf-8", errors="replace")
        resp_type, resp_val = decode_body(resp_body, resp_ct)
        if path.startswith("/api/keys") and isinstance(resp_val, dict) and "key" in resp_val:
            resp_val["key"] = REDACTED  # POST /api/keys returns the new raw key one time
        record = {
            "captured_at": now.isoformat(),
            "request": request,
            "response": {"status": status, "content_type": resp_ct, "body_type": resp_type, "body": resp_val},
            "elapsed_ms": elapsed_ms,
        }
        if req_trunc or resp_trunc:
            record["truncated"] = {"request": req_trunc, "response": resp_trunc}
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        target = self.dir / f"capture-{now:%Y%m%d}.ndjson"
        size = target.stat().st_size if target.exists() else 0
        if size + len(line) > self.max_bytes:
            self.dropped += 1
            return
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as f:
            f.write(line)

    def flush(self, timeout: float = 5.0):
        """Wait until the writer thread has written each queued record. For tests."""
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
