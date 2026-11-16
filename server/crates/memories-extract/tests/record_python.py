"""Capture the HTTP requests of the Python LLM providers (v5.19.0) into replay files.

Each case runs one Python provider against a local capture server that returns a
fixed reply. The request (secrets hidden) and the reply go to tests/replay/<case>.json.
The Rust test `provider_replay.rs` sends the same call and compares its request.

Run from the repository root:
    .venv/bin/python server/crates/memories-extract/tests/record_python.py
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent / "replay"

# Fake secrets. tests/provider_replay.rs has the same table.
SECRETS = {
    "ANTHROPIC_API_KEY": "sk-ant-api03-test-secret",
    "ANTHROPIC_OAUTH_TOKEN": "sk-ant-oat01-test-secret",
    "OPENAI_API_KEY": "sk-proj-test-secret",
    "OMLX_API_KEY": "omlx-test-secret",
}
SECRET_HEADERS = {"authorization", "x-api-key"}
# Transport headers that the SDKs and urllib set. They are not part of the contract.
IGNORED_HEADERS = {"host", "content-length", "user-agent", "accept", "accept-encoding", "connection"}

SYSTEM = "You extract facts.\nReturn a JSON array."
USER = "Conversation:\nuser: I moved to Lisbon in 2024. ünïcode ✓"

ANTHROPIC_REPLY = {
    "id": "msg_01", "type": "message", "role": "assistant", "model": "m",
    "content": [
        {"type": "thinking", "thinking": "hidden", "signature": "sig"},
        {"type": "text", "text": "[\"Lives in Lisbon\""},
        {"type": "text", "text": ", \"since 2024\"]"},
    ],
    "stop_reason": "end_turn", "stop_sequence": None,
    "usage": {"input_tokens": 41, "output_tokens": 9},
}


def openai_reply(content, reasoning=None):
    msg = {"role": "assistant", "content": content}
    if reasoning is not None:
        msg["reasoning"] = reasoning
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 33, "completion_tokens": 6, "total_tokens": 39},
    }


OLLAMA_REPLY = {"model": "m", "response": "[\"Lives in Lisbon\"]", "done": True,
                "prompt_eval_count": 27, "eval_count": 5}

# name, provider, explicit model, env (None = fake secret), base-URL env var, URL suffix, call, reply
CASES = [
    ("anthropic-api-key", "anthropic", None, {"ANTHROPIC_API_KEY": None},
     "ANTHROPIC_BASE_URL", "", "complete", ANTHROPIC_REPLY),
    ("anthropic-sampling-model", "anthropic", "claude-haiku-4-5-20251001", {"ANTHROPIC_API_KEY": None},
     "ANTHROPIC_BASE_URL", "", "complete", ANTHROPIC_REPLY),
    ("anthropic-workspace", "anthropic", None,
     {"ANTHROPIC_API_KEY": None, "ANTHROPIC_WORKSPACE_ID": "wrkspc_test"},
     "ANTHROPIC_BASE_URL", "", "complete", ANTHROPIC_REPLY),
    ("anthropic-oauth", "anthropic", None, {"ANTHROPIC_OAUTH_TOKEN": None},
     "ANTHROPIC_BASE_URL", "", "complete", ANTHROPIC_REPLY),
    ("anthropic-auth-switch", "anthropic", None,
     {"ANTHROPIC_API_KEY": None, "ANTHROPIC_OAUTH_TOKEN": None, "ANTHROPIC_AUTH": "oauth"},
     "ANTHROPIC_BASE_URL", "", "complete", ANTHROPIC_REPLY),
    ("openai", "openai", None, {"OPENAI_API_KEY": None},
     "OPENAI_BASE_URL", "/v1", "complete", openai_reply("[\"Lives in Lisbon\"]")),
    ("omlx", "omlx", None, {"OMLX_API_KEY": None},
     "OMLX_URL", "/v1", "complete", openai_reply(None, reasoning="[\"from reasoning\"]")),
    ("omlx-default-key", "omlx", "qwen3-8b", {},
     "OMLX_URL", "/v1", "complete", openai_reply("[]")),
    ("ollama", "ollama", None, {},
     "OLLAMA_URL", "", "complete", OLLAMA_REPLY),
    ("ollama-health", "ollama", None, {},
     "OLLAMA_URL", "", "health", {"models": []}),
]

PROVIDER_ENV = ("EXTRACT_PROVIDER", "EXTRACT_MODEL", "ANTHROPIC_AUTH", "ANTHROPIC_WORKSPACE_ID",
                "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "OMLX_URL", "OLLAMA_URL",
                "ANTHROPIC_AUTH_TOKEN", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", *SECRETS)


def redact(name, value):
    """Hide each known secret. Hide a whole auth header that holds an unknown value."""
    out = value
    for secret in SECRETS.values():
        out = out.replace(secret, "<redacted>")
    if name in SECRET_HEADERS and out == value:
        return "<redacted>"
    return out


def capture_server(reply):
    captured = []

    class Handler(BaseHTTPRequestHandler):
        def _serve(self):
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b""
            headers = {}
            for k, v in self.headers.items():
                k = k.lower()
                if k in IGNORED_HEADERS or k.startswith("x-stainless-"):
                    continue
                headers[k] = redact(k, v)
            captured.append({
                "method": self.command,
                "path": self.path,
                "headers": dict(sorted(headers.items())),
                "body": json.loads(raw) if raw else None,
            })
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = _serve

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, captured


def record(case):
    name, provider, model, env, url_var, suffix, call, reply = case
    server, captured = capture_server(reply)
    for var in PROVIDER_ENV:
        os.environ.pop(var, None)
    for var, value in env.items():
        os.environ[var] = SECRETS[var] if value is None else value
    os.environ[url_var] = f"http://127.0.0.1:{server.server_port}{suffix}"
    try:
        import llm_provider
        p = llm_provider.get_provider(provider, model)
        if call == "health":
            result = {"healthy": p.health_check()}
        else:
            r = p.complete(SYSTEM, USER)
            result = {"text": r.text, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens}
        result["model"] = p.model
    finally:
        server.shutdown()
    assert len(captured) == 1, (name, captured)
    doc = {
        "case": name,
        "provider": provider,
        "model": model,
        "env": env,
        "base_url_env": url_var,
        "base_url_suffix": suffix,
        "call": {"kind": call, "system": SYSTEM, "user": USER},
        "request": captured[0],
        "response": {"status": 200, "body": reply},
        "result": result,
    }
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    for secret in SECRETS.values():
        assert secret not in text, f"{name}: secret in replay file"
    (OUT / f"{name}.json").write_text(text)
    print("recorded", name)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for c in CASES:
        record(c)
