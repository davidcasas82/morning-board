#!/usr/bin/env python3
"""Serve the quotes file on the home network.

GitHub Pages is HTTPS, so the URL in board config has to be HTTPS too
or the frame will ignore the fetch and stay on the clock. This server
speaks plain HTTP and sends CORS headers. Point a tunnel or reverse
proxy at it if the page needs HTTPS.

Not installed on the mini by this repo. Default file:
~/Library/Application Support/morning-board/quotes.json
Default listen: 0.0.0.0:8765
"""

from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT = Path.home() / "Library" / "Application Support" / "morning-board" / "quotes.json"


class Handler(BaseHTTPRequestHandler):
    quotes_path = DEFAULT

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path != "/quotes.json":
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        target = self.quotes_path
        if not target.is_file():
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        body = target.read_bytes()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt, *args):
        path = self.path.split("?", 1)[0]
        sys.stderr.write("%s %s\n" % (self.command, path))


def main(argv):
    quotes_path = DEFAULT
    port = 8765
    if "--file" in argv:
        quotes_path = Path(argv[argv.index("--file") + 1]).expanduser()
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])
    Handler.quotes_path = quotes_path
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("serving quotes on port %s" % port)
    server.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1:])
