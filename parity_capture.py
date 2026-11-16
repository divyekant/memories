"""Capture middleware for the parity kit.

If PARITY_CAPTURE_DIR is set, each request and its reply go as one NDJSON line
to <dir>/capture-<UTC date>.ndjson. The default is off. The file holds memory
text, so it gets mode 0600 and must stay on the host. Key headers are redacted.
"""

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

REDACTED = "<redacted>"
MAX_BODY = 4 * 1024 * 1024
_KEEP_HEADERS = ("content-type", "x-api-key", "authorization")
_SECRET_HEADERS = ("x-api-key", "authorization")


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


class ParityCaptureMiddleware:
    def __init__(self, app, capture_dir: str):
        self.app = app
        self.dir = Path(capture_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        start = time.perf_counter()
        req_body = bytearray()
        resp = {"status": 0, "content_type": "", "body": bytearray(), "stream": False}

        async def recv():
            message = await receive()
            if message["type"] == "http.request" and len(req_body) < MAX_BODY:
                req_body.extend(message.get("body", b""))
            return message

        async def snd(message):
            if message["type"] == "http.response.start":
                resp["status"] = message["status"]
                for k, v in message.get("headers", []):
                    if k.decode().lower() == "content-type":
                        resp["content_type"] = v.decode()
                resp["stream"] = resp["content_type"].startswith("text/event-stream")
            elif message["type"] == "http.response.body":
                if resp["stream"]:
                    # shortcut: an SSE reply is endless, so record its first chunk and write at once.
                    if not resp["body"]:
                        resp["body"].extend(message.get("body", b""))
                        self._write(scope, req_body, resp, start)
                elif len(resp["body"]) < MAX_BODY:
                    resp["body"].extend(message.get("body", b""))
                if not message.get("more_body", False) and not resp["stream"]:
                    self._write(scope, req_body, resp, start)
            await send(message)

        await self.app(scope, recv, snd)

    def _write(self, scope, req_body, resp, start):
        try:
            headers = {}
            for k, v in scope.get("headers", []):
                name = k.decode().lower()
                if name in _KEEP_HEADERS or name.startswith("x-memories-"):
                    headers[name] = REDACTED if name in _SECRET_HEADERS and v else v.decode()
            req_ct = headers.get("content-type", "")
            req_type, req_val = decode_body(bytes(req_body), req_ct)
            resp_type, resp_val = decode_body(bytes(resp["body"]), resp["content_type"])
            request = {
                "method": scope["method"],
                "path": scope["path"],
                "query": scope.get("query_string", b"").decode(),
                "headers": headers,
            }
            if req_type == "json":
                request["json"] = req_val
            elif req_type != "none":
                request["body"] = bytes(req_body).decode("utf-8", errors="replace")
            record = {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "request": request,
                "response": {
                    "status": resp["status"],
                    "content_type": resp["content_type"],
                    "body_type": resp_type,
                    "body": resp_val,
                },
                "elapsed_ms": round((time.perf_counter() - start) * 1000.0, 1),
            }
            line = json.dumps(record, ensure_ascii=False) + "\n"
            path = self.dir / f"capture-{datetime.now(timezone.utc):%Y%m%d}.ndjson"
            with self._lock:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception:  # a capture fault must never break a request
            pass
