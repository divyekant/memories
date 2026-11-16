import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import resend_extract_jobs as r


class Handler(BaseHTTPRequestHandler):
    bodies = []
    busy = 1

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if Handler.busy:
            Handler.busy -= 1
            self.send_response(503)
            self.send_header("Retry-After", "0")
            self.end_headers()
            return
        Handler.bodies.append((self.headers["X-API-Key"], body))
        self.send_response(202)
        self.end_headers()
        self.wfile.write(json.dumps({"job_id": "new-%d" % len(Handler.bodies)}).encode())

    def log_message(self, *args):
        pass


class ResendTest(unittest.TestCase):
    def test_each_job_goes_once_with_retries_and_no_null_fields(self):
        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        req = {"messages": "m", "source": "s", "context": "stop", "debug": False,
               "dry_run": False, "document_at": None, "allowed_prefixes": ["s"],
               "promotion_context": None}
        lines = [{"job_id": "a", "status": "queued", "request": req},
                 {"job_id": "a", "status": "queued", "request": req},
                 {"job_id": "b", "status": "running", "request": dict(req, messages="n")}]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "jobs.ndjson")
            with open(path, "w") as f:
                f.write("".join(json.dumps(x) + "\n" for x in lines))
            url = "http://127.0.0.1:%d" % server.server_port
            os.environ["MEMORIES_API_KEY"] = "k"
            self.assertEqual(r.main([path, "--base-url", url]), 0)
            self.assertEqual(r.main([path, "--base-url", url]), 0)
        self.assertEqual([b["messages"] for _, b in Handler.bodies], ["m", "n"])
        self.assertEqual(Handler.bodies[0], ("k", {"messages": "m", "source": "s", "context": "stop",
                                                  "debug": False, "dry_run": False}))


class ScopeTest(unittest.TestCase):
    def test_a_scoped_job_keeps_its_scope(self):
        req = {"messages": "m", "source": "a/b", "allowed_prefixes": ["a/"], "promotion_context": {"x": 1}}
        self.assertEqual(r.body_of(req), {"messages": "m", "source": "a/b"})
        unscoped = dict(req, allowed_prefixes=None)
        self.assertEqual(r.body_of(unscoped)["promotion_context"], {"x": 1})
        for source in (None, "", "c/d"):
            with self.assertRaises(ValueError):
                r.body_of(dict(req, source=source))


if __name__ == "__main__":
    unittest.main()
