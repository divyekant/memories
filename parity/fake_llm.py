"""A stub of the Ollama API with fixed LLM replies, for the maintenance exit check (task P5-3).

Usage: python parity/fake_llm.py <port> <log file>

POST /api/generate answers a consolidation prompt with one merged text: the
text of the first member. A cluster whose first ID is a multiple of 7 gets a
reply that is not JSON, so the error path also runs. Each request body goes
to the log file as one JSON line, so the exit check can compare the prompts of
two servers.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MARK = "Memories to consolidate:\n"


def reply(prompt):
    if MARK not in prompt:
        return "[]"
    block = prompt.split(MARK, 1)[1].split("\n\nOutput a JSON array", 1)[0]
    members = json.loads(block)
    if members[0]["id"] % 7 == 0:
        return "I cannot merge these."
    return "```json\n" + json.dumps([f"Consolidated: {members[0]['text']}"]) + "\n```"


class Handler(BaseHTTPRequestHandler):
    log_path = ""

    def _send(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"models": []})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"system": body.get("system"), "prompt": body.get("prompt")}) + "\n")
        self._send({"response": reply(body.get("prompt", "")), "prompt_eval_count": 1, "eval_count": 1,
                    "done": True})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    Handler.log_path = sys.argv[2]
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
