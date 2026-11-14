#!/usr/bin/env python3
"""Serve the morning board checkout on the LAN.

Port 8780. Only the board's own files are served (see ALLOWED); every
other path is a 404, so a token or config file dropped into the checkout
never leaves the mini. The checkout is read from disk on every request,
so an HTML update does not need a restart. /quotes.json is the file
outside the repo:

    ~/Library/Application Support/morning-board/quotes.json

A missing quotes file is a 404. The server stays up and the clock page
still loads. /weather.json is the Open-Meteo forecast for the house,
fetched here with a short timeout and cached for about 10 minutes. A
failed refresh returns that cached copy and its age. The city name is
fixed from the default coordinates. Run it as a user LaunchAgent. It
starts after casasmacmini-server logs in, which depends on auto-login,
not at the boot chime. Installing it does not change the frame's start
URL.
"""

from __future__ import annotations

import fnmatch
import json
import mimetypes
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

PORT = 8780
ROOT = Path(__file__).resolve().parents[1]
QUOTES = Path.home() / "Library" / "Application Support" / "morning-board" / "quotes.json"
DEFAULT_LAT = "33.67"
DEFAULT_LON = "-117.82"
WEATHER_TTL = 10 * 60
WEATHER_TIMEOUT = 4
WEATHER_MAX_BYTES = 1000000
WEATHER_CITY = "Irvine, California"
WEATHER_ERROR = b'{"error":"weather unavailable"}'
FORECAST_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
    "&current=temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code,is_day"
    "&hourly=temperature_2m"
    "&daily=temperature_2m_max,weather_code,sunrise,sunset"
    "&temperature_unit=fahrenheit&wind_speed_unit=mph"
    "&timezone=auto&forecast_days=7&models=gfs_seamless"
) % (DEFAULT_LAT, DEFAULT_LON)
ALLOWED = (
    "index.html",
    "kiosk.html",
    "*.js",
    "schedule.json",
    "board-config*.json",
    "fonts/*",
)

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


def allowed(rel: str) -> bool:
    depth = rel.count("/")
    return any(pattern.count("/") == depth and fnmatch.fnmatchcase(rel, pattern) for pattern in ALLOWED)


def static_file(root: Path, url_path: str):
    """Map a URL path to an allowlisted file inside root, or None."""
    rel = unquote(url_path).lstrip("/") or "index.html"
    if "\\" in rel or "\x00" in rel:
        return None
    if any(part in ("", ".", "..") or part.startswith(".") for part in rel.split("/")):
        return None
    if not allowed(rel):
        return None
    root = root.resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def content_type(path: Path) -> str:
    guessed = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if guessed.startswith("text/") or guessed in ("application/javascript", "application/json"):
        return guessed + "; charset=utf-8"
    return guessed


def fetch_forecast(url=FORECAST_URL, timeout=WEATHER_TIMEOUT, opener=None):
    """GET the Open-Meteo forecast. A short timeout keeps a hung upstream off the server."""
    if opener is None:
        opener = urllib.request.urlopen
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "morning-board"},
    )
    with opener(request, timeout=timeout) as response:
        raw = response.read(WEATHER_MAX_BYTES + 1)
    if len(raw) > WEATHER_MAX_BYTES:
        raise ValueError("forecast too large")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or "current" not in data or "daily" not in data or "hourly" not in data:
        raise ValueError("forecast shape")
    return data


class WeatherSource:
    """Last good forecast. Fresh for WEATHER_TTL; older copies are the fallback."""

    def __init__(self, fetch=None, clock=None, ttl=WEATHER_TTL, city=WEATHER_CITY):
        self.fetch = fetch or fetch_forecast
        self.clock = clock or time.time
        self.ttl = ttl
        self.city = city
        self._lock = threading.Lock()
        self._forecast = None
        self._fetched_at = None

    def payload(self):
        now = self.clock()
        with self._lock:
            cached = self._cached(now, fresh_only=True)
            if cached is not None:
                return 200, cached
        try:
            forecast = self.fetch()
            body = self._encode(forecast, 0)
        except Exception:
            with self._lock:
                cached = self._cached(self.clock(), fresh_only=False)
                if cached is not None:
                    return 200, cached
            return 502, WEATHER_ERROR
        now = self.clock()
        with self._lock:
            self._forecast = forecast
            self._fetched_at = now
        return 200, body

    def _cached(self, now, fresh_only):
        if self._forecast is None or self._fetched_at is None:
            return None
        age = now - self._fetched_at
        if age < 0:
            age = 0
        if fresh_only and age >= self.ttl:
            return None
        return self._encode(self._forecast, age)

    def _encode(self, forecast, age):
        body = dict(forecast)
        body["city"] = self.city
        body["age_seconds"] = int(age)
        return json.dumps(body).encode("utf-8")


def make_handler(root: Path, quotes: Path, weather: WeatherSource):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self._respond(include_body=True)

        def do_HEAD(self):
            self._respond(include_body=False)

        def _respond(self, include_body: bool):
            path = urlsplit(self.path).path
            if path == "/weather.json":
                status, body = weather.payload()
                self._send(status, body, "application/json; charset=utf-8", include_body)
                return
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


def bind(root: Path, quotes: Path, host: str = "0.0.0.0", port: int = PORT, weather: WeatherSource = None):
    if weather is None:
        weather = WeatherSource()
    server = ThreadingHTTPServer((host, port), make_handler(root, quotes, weather))
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
