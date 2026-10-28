"""A stand-in for llama.cpp's llama-server, for tests: same endpoints, scripted answers.

Run as ``python fake_llama_server.py --port N [--ready-after S]``. Unknown llama-server
arguments are accepted and ignored. The reply depends on the utterance:

- contains HANG: sleep 30 s before answering
- contains GARBAGE: reply with free text instead of JSON
- contains FAIL: HTTP 500
- contains CRASH: the process exits immediately
- otherwise: an intent from REPLIES, or unclear
"""

import argparse
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLIES = {
    "hmm i reckon it is probably b": {"action": "answer", "args": {"choice": "B"}},
    "on second thoughts make number seven a instead": {
        "action": "change_answer",
        "args": {"choice": "A", "question": 7},
    },
    "i am through i want to hand in": {"action": "submit", "args": {}},
    "is b the right one": {"action": "not_allowed", "args": {}},
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--ready-after", type=float, default=0.0)
    parser.add_argument("--exit-at-start", action="store_true")
    args, _unknown = parser.parse_known_args()
    if args.exit_at_start:
        sys.exit(3)
    ready_at = time.monotonic() + args.ready_after

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: object) -> None:
            pass

        def _send(self, status: int, body: object) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path == "/health":
                ready = time.monotonic() >= ready_at
                self._send(200 if ready else 503, {"status": "ok" if ready else "loading"})
            else:
                self._send(404, {})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            user = body["messages"][-1]["content"]
            utterance = re.search(r"<utterance>(.*)</utterance>", user, re.S)
            said = utterance.group(1) if utterance else ""
            if "CRASH" in said:
                os._exit(1)
            if "HANG" in said:
                time.sleep(30)
            if "FAIL" in said:
                self._send(500, {"error": "boom"})
                return
            if "GARBAGE" in said:
                content = "Sure! The answer is B."
            else:
                key = re.sub(r"[^a-z ]", "", said.lower()).strip()
                content = json.dumps(REPLIES.get(key, {"action": "unclear", "args": {}}))
            self._send(200, {"choices": [{"message": {"role": "assistant", "content": content}}]})

    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
