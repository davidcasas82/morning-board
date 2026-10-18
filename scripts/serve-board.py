#!/usr/bin/env python3
"""Serve the morning board checkout on the LAN.

Port 8780, separate from the quotes-only server on 8765. The checkout
is read from disk on every request, so an HTML update does not need a
restart. /quotes.json is the file outside the repo:

    ~/Library/Application Support/morning-board/quotes.json

A missing quotes file is a 404. The server stays up and the clock page
still loads. Run it as a user LaunchAgent. It starts after
casasmacmini-server logs in, which depends on auto-login, not at the
boot chime. Installing it does not change the frame's start URL.
"""

from __future__ import annotations

import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

PORT = 8780
ROOT = Path(__file__).resolve().parents[1]
QUOTES = Path.home() / "Library" / "Application Support" / "morning-board" / "quotes.json"

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("text/javascript", ".js")


def quotes_bytes(path: Path):
    """Return the quotes file, or None when it is missing or unreadable."""
    try:
        if not path.is_file():
            return None
        return path.read_bytes()
    except OSError:
        return None


def static_file(root: Path, url_path: str):
    """Map a URL path to a file inside root, or None."""
    raw = unquote(url_path)
    if raw.startswith("//") or "\\" in raw or "\x00" in raw:
        return None
    rel = raw.lstrip("/")
    if not rel or rel.endswith("/"):
        rel = rel + "index.html"
    parts = Path(rel).parts
    if not parts or any(part in ("", ".", "..") or part.startswith(".") for part in parts):
        return None
    root = root.resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_dir():
        candidate = candidate / "index.html"
    if not candidate.is_file():
        return None
    return candidate


def content_type(path: Path) -> str:
    guessed = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if guessed.startswith("text/") or guessed in ("application/javascript", "application/json"):
        return guessed + "; charset=utf-8"
    return guessed


def make_handler(root: Path, quotes: Path):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self._respond(include_body=True)

        def do_HEAD(self):
            self._respond(include_body=False)

        def _respond(self, include_body: bool):
            path = urlsplit(self.path).path
            if path == "/quotes.json":
                body = quotes_bytes(quotes)
                if body is None:
                    self._send(404, b"", "text/plain; charset=utf-8", include_body)
                    return
                self._send(200, body, "application/json; charset=utf-8", include_body)
                return
            target = static_file(root, path)
            if target is None:
                self._send(404, b"", "text/plain; charset=utf-8", include_body)
                return
            try:
                body = target.read_bytes()
            except OSError:
                self._send(404, b"", "text/plain; charset=utf-8", include_body)
                return
            self._send(200, body, content_type(target), include_body)

        def _send(self, status: int, body: bytes, mime: str, include_body: bool):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if include_body and body:
                self.wfile.write(body)

        def log_message(self, fmt, *args):
            path = urlsplit(self.path).path
            sys.stderr.write("%s %s\n" % (self.command, path))

    return Handler


def bind(root: Path, quotes: Path, host: str = "0.0.0.0", port: int = PORT):
    server = ThreadingHTTPServer((host, port), make_handler(root, quotes))
    return server


def main(argv):
    root = ROOT
    quotes = QUOTES
    port = PORT
    if "--root" in argv:
        root = Path(argv[argv.index("--root") + 1]).expanduser()
    if "--quotes" in argv:
        quotes = Path(argv[argv.index("--quotes") + 1]).expanduser()
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])
    server = bind(root, quotes, "0.0.0.0", port)
    print("morning board on port %s" % port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1:])
