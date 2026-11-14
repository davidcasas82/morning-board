#!/usr/bin/env python3
"""Checks for the LAN board host. No live mini, no quote text from the sheet."""

from __future__ import annotations

import http.client
import importlib.util
import json
import plistlib
import re
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


serve = load("serve_board", "serve-board.py")
pull = load("pull_board", "pull-board.py")


class BoardHostTests(unittest.TestCase):
    def start(self, root: Path, quotes: Path, weather=None):
        server = serve.bind(root, quotes, "127.0.0.1", 0, weather)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return server.server_address[1]

    def get(self, port, path):
        return urllib.request.urlopen("http://127.0.0.1:%s%s" % (port, path))

    def test_quotes_route_returns_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            root = directory / "checkout"
            root.mkdir()
            (root / "kiosk.html").write_text("<p id=\"clock\">clock</p>", encoding="utf-8")
            (root / "quotes.json").write_text('{"quotes":[{"text":"FROM CHECKOUT"}]}', encoding="utf-8")
            quotes = directory / "quotes.json"
            body = b'{"quotes":[{"text":"Stand in from the quotes file."}]}\n'
            quotes.write_bytes(body)
            port = self.start(root, quotes)
            with self.get(port, "/quotes.json") as response:
                self.assertEqual(response.status, 200)
                self.assertIn("application/json", response.headers["Content-Type"])
                self.assertEqual(response.read(), body)
            with self.get(port, "/kiosk.html") as response:
                self.assertIn(b"clock", response.read())

    def test_missing_quotes_file_does_not_crash_the_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            root = directory / "checkout"
            root.mkdir()
            (root / "index.html").write_text("board up", encoding="utf-8")
            (root / "kiosk.html").write_text("clock up", encoding="utf-8")
            missing = directory / "absent" / "quotes.json"
            port = self.start(root, missing)
            for _ in range(2):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    self.get(port, "/quotes.json")
                self.assertEqual(raised.exception.code, 404)
            with self.get(port, "/index.html") as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"board up")
            with self.get(port, "/kiosk.html") as response:
                self.assertEqual(response.read(), b"clock up")
            (root / "kiosk.html").write_text("clock updated", encoding="utf-8")
            with self.get(port, "/kiosk.html") as response:
                self.assertEqual(response.read(), b"clock updated")

    def test_failed_pull_does_not_delete_the_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "board@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Board Test"], cwd=repo, check=True)
            tracked = repo / "kiosk.html"
            tracked.write_text("clock stays", encoding="utf-8")
            subprocess.run(["git", "add", "kiosk.html"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "clock"], cwd=repo, check=True, capture_output=True)
            extra = repo / "keep.txt"
            extra.write_text("stay", encoding="utf-8")
            tracked.write_text("local edit stays", encoding="utf-8")
            seen = []

            def fail(cmd):
                seen.append(list(cmd))

                class Result:
                    returncode = 1

                return Result()

            code = pull.pull_ff_only(repo, run=fail)
            self.assertEqual(code, 1)
            self.assertEqual(seen[0][-2:], ["pull", "--ff-only"])
            self.assertNotIn("reset", seen[0])
            self.assertNotIn("clean", seen[0])
            self.assertEqual(tracked.read_text(encoding="utf-8"), "local edit stays")
            self.assertEqual(extra.read_text(encoding="utf-8"), "stay")
            self.assertTrue(repo.is_dir())

            real = pull.pull_ff_only(repo)
            self.assertNotEqual(real, 0)
            self.assertEqual(tracked.read_text(encoding="utf-8"), "local edit stays")
            self.assertEqual(extra.read_text(encoding="utf-8"), "stay")
            self.assertTrue((repo / ".git").is_dir())

    def test_example_quotes_url_is_same_origin_path(self):
        example = json.loads((ROOT / "board-config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(example["quotesUrl"], "/quotes.json")
        self.assertNotIn("http://", json.dumps(example))
        self.assertNotIn("https://", example["quotesUrl"])

    def test_page_avoids_color_syntax_newer_than_webview_106(self):
        newer = ("color-mix(", "rgb(from", "hsl(from", "oklch(", "oklab(", "lab(", "lch(", "color(display-p3")
        pages = [ROOT / "kiosk.html", ROOT / "index.html"] + sorted(ROOT.glob("*.js"))
        found = [
            (page.name, token)
            for page in pages
            for token in newer
            if re.search(r"(?<![\w-])" + re.escape(token), page.read_text(encoding="utf-8"), re.IGNORECASE)
        ]
        self.assertEqual(found, [])

    def test_server_port_is_8780(self):
        self.assertEqual(serve.PORT, 8780)

    def forecast(self, temp=72):
        days = ["2026-11-14", "2026-11-15", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"]
        return {
            "current": {
                "time": "2026-11-14T05:00",
                "temperature_2m": temp,
                "apparent_temperature": temp - 1,
                "relative_humidity_2m": 40,
                "wind_speed_10m": 5,
                "weather_code": 1,
                "is_day": 1,
            },
            "hourly": {
                "time": ["2026-11-14T05:00", "2026-11-14T06:00"],
                "temperature_2m": [temp, temp + 1],
            },
            "daily": {
                "time": days,
                "temperature_2m_max": [temp + 3, 76, 77, 78, 79, 80],
                "weather_code": [1, 2, 3, 0, 1, 2],
                "sunrise": ["2026-11-14T06:30"],
                "sunset": ["2026-11-14T17:00"],
            },
        }

    def weather_server(self, fetch, clock):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            root = directory / "checkout"
            root.mkdir()
            (root / "kiosk.html").write_text("clock", encoding="utf-8")
            (root / "weather.json").write_text('{"secret":true}', encoding="utf-8")
            weather = serve.WeatherSource(fetch=fetch, clock=clock)
            port = self.start(root, directory / "quotes.json", weather)
            yield port

    def test_weather_cache_hit_skips_upstream(self):
        clock = {"now": 1000000.0}
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            return self.forecast()

        ports = self.weather_server(fetch, lambda: clock["now"])
        port = next(ports)
        try:
            with self.get(port, "/weather.json") as response:
                self.assertEqual(response.status, 200)
                self.assertIn("application/json", response.headers["Content-Type"])
                body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(body["current"]["temperature_2m"], 72)
            self.assertEqual(body["age_seconds"], 0)
            self.assertEqual(body["city"], "Irvine, California")
            self.assertNotIn("secret", body)
            clock["now"] += 120
            with self.get(port, "/weather.json") as response:
                body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(body["age_seconds"], 120)
            self.assertEqual(body["current"]["temperature_2m"], 72)
            self.assertEqual(calls["n"], 1)
        finally:
            ports.close()

    def test_weather_upstream_failure_serves_the_cached_copy(self):
        clock = {"now": 1000000.0}
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            if calls["n"] > 1:
                raise OSError("upstream down")
            return self.forecast(temp=68)

        ports = self.weather_server(fetch, lambda: clock["now"])
        port = next(ports)
        try:
            with self.get(port, "/weather.json") as response:
                self.assertEqual(json.loads(response.read().decode("utf-8"))["current"]["temperature_2m"], 68)
            clock["now"] += serve.WEATHER_TTL + 45
            with self.get(port, "/weather.json") as response:
                self.assertEqual(response.status, 200)
                body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(body["current"]["temperature_2m"], 68)
            self.assertEqual(body["age_seconds"], serve.WEATHER_TTL + 45)
            self.assertEqual(body["city"], "Irvine, California")
            self.assertEqual(calls["n"], 2)
        finally:
            ports.close()

    def test_weather_upstream_failure_without_cache_is_an_error(self):
        def fetch():
            raise OSError("upstream down")

        ports = self.weather_server(fetch, time.time)
        port = next(ports)
        try:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.get(port, "/weather.json")
            self.assertEqual(raised.exception.code, 502)
            self.assertEqual(json.loads(raised.exception.read().decode("utf-8")), {"error": "weather unavailable"})
            with self.get(port, "/kiosk.html") as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"clock")
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.get(port, "/weather.json")
            self.assertEqual(raised.exception.code, 502)
        finally:
            ports.close()

    def test_slow_weather_fetch_does_not_block_the_board(self):
        started = threading.Event()
        release = threading.Event()

        def fetch():
            started.set()
            release.wait(5)
            return self.forecast()

        ports = self.weather_server(fetch, time.time)
        port = next(ports)
        try:
            def hit_weather():
                with self.get(port, "/weather.json") as response:
                    response.read()

            thread = threading.Thread(target=hit_weather)
            thread.start()
            self.assertTrue(started.wait(2))
            with urllib.request.urlopen("http://127.0.0.1:%s/kiosk.html" % port, timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"clock")
            release.set()
            thread.join(5)
            self.assertFalse(thread.is_alive())
        finally:
            release.set()
            ports.close()

    def test_hung_upstream_times_out(self):
        import socket

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        self.addCleanup(sock.close)
        port = sock.getsockname()[1]

        def hold():
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            time.sleep(30)
            conn.close()

        threading.Thread(target=hold, daemon=True).start()
        started = time.time()
        with self.assertRaises(Exception):
            serve.fetch_forecast(url="http://127.0.0.1:%s/v1/forecast" % port)
        self.assertLess(time.time() - started, serve.WEATHER_TIMEOUT + 2)

    def test_forecast_url_matches_the_old_kiosk_request_and_times_out(self):
        self.assertLessEqual(serve.WEATHER_TIMEOUT, 5)
        self.assertIn("latitude=33.67&", serve.FORECAST_URL)
        self.assertIn("longitude=-117.82&", serve.FORECAST_URL)
        self.assertNotIn("33.66" + "9", serve.FORECAST_URL)
        self.assertNotIn("117.82" + "3", serve.FORECAST_URL)
        for part in (
            "current=temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code,is_day",
            "hourly=temperature_2m",
            "daily=temperature_2m_max,weather_code,sunrise,sunset",
            "temperature_unit=fahrenheit",
            "wind_speed_unit=mph",
            "timezone=auto",
            "forecast_days=7",
            "models=gfs_seamless",
        ):
            self.assertIn(part, serve.FORECAST_URL)
        seen = {}

        class Resp:
            def read(self, n):
                return b'{"current":{},"daily":{},"hourly":{}}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def opener(req, data=None, timeout=None):
            seen["timeout"] = timeout
            seen["url"] = req.full_url
            return Resp()

        data = serve.fetch_forecast(opener=opener)
        self.assertEqual(seen["timeout"], serve.WEATHER_TIMEOUT)
        self.assertEqual(seen["url"], serve.FORECAST_URL)
        self.assertIn("current", data)

    def test_kiosk_reads_weather_from_the_board(self):
        kiosk = (ROOT / "kiosk.html").read_text(encoding="utf-8")
        weather = (ROOT / "weather.js").read_text(encoding="utf-8")
        self.assertIn('src="weather.js?v=2"', kiosk)
        self.assertIn("MorningWeather.load", kiosk)
        self.assertIn(serve.FORECAST_URL, weather)
        self.assertIn("'/weather.json'", weather)
        self.assertIn("nominatim.openstreetmap.org", weather)
        self.assertIn("RETRY_MS = 60 * 1000", weather)
        self.assertIn("STALE_MS = 2 * 60 * 60 * 1000", weather)
        self.assertNotIn("geolocation", kiosk)
        self.assertNotIn("geolocation", weather)
        self.assertNotIn("33.66" + "9", weather)
        self.assertNotIn("117.82" + "3", weather)

    def test_public_tree_has_no_house_level_weather_coordinates(self):
        needles = ("33.66" + "9", "117.82" + "3")
        hits = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in path.parts for part in (".git", "node_modules", "__pycache__")):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if any(needle in text for needle in needles):
                hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits, [])

    def test_only_board_files_are_served(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            root = directory / "checkout"
            (root / "fonts").mkdir(parents=True)
            (root / "scripts").mkdir()
            allowed = {
                "/": b"index page",
                "/index.html": b"index page",
                "/kiosk.html": b"kiosk page",
                "/daily.js": b"daily script",
                "/schedule.json": b'{"weekdays":{}}',
                "/board-config.json": b'{"quotesUrl":"/quotes.json"}',
                "/board-config.example.json": b'{"quotesUrl":"/quotes.json"}',
                "/fonts/IBMPlexSans-Regular.woff2": b"woff2 bytes",
                "/kiosk.html?v=19": b"kiosk page",
                "/quotes.json": b'{"quotes":[]}',
            }
            for path, body in allowed.items():
                name = urlsplit(path).path.lstrip("/") or "index.html"
                if name != "quotes.json":
                    (root / name).write_bytes(body)
            quotes = directory / "quotes.json"
            quotes.write_bytes(allowed["/quotes.json"])
            for name in (
                "google-token.json",
                "oauth-client.json",
                "secret.json",
                ".env",
                "notes.txt",
                "quotes.json",
                "scripts/serve-board.py",
                "fonts/.hidden",
            ):
                (root / name).write_text("SECRET", encoding="utf-8")
            (root / "fonts" / "nested").mkdir()
            (root / "fonts" / "nested" / "x.woff2").write_text("SECRET", encoding="utf-8")
            (directory / "outside.txt").write_text("SECRET", encoding="utf-8")
            (root / "linked.js").symlink_to(directory / "outside.txt")
            port = self.start(root, quotes)

            def raw(path):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                self.addCleanup(conn.close)
                conn.request("GET", path)
                response = conn.getresponse()
                return response.status, response.read()

            for path, body in allowed.items():
                self.assertEqual(raw(path), (200, body), path)
            for path in (
                "/google-token.json",
                "/oauth-client.json",
                "/secret.json",
                "/.env",
                "/notes.txt",
                "/scripts/serve-board.py",
                "/fonts/.hidden",
                "/fonts/nested/x.woff2",
                "/fonts/",
                "/scripts/",
                "/favicon.ico",
                "/linked.js",
                "/../outside.txt",
                "/fonts/../google-token.json",
                "/%2e%2e/outside.txt",
                "/%2E%2E%2Foutside.txt",
                "/fonts%2f..%2fgoogle-token.json",
                "/%252e%252e/outside.txt",
                "/%2egit/config",
                "/kiosk.html%00.js",
            ):
                self.assertEqual(raw(path), (404, b""), path)

    def test_board_jobs_are_user_launch_agents(self):
        board = plistlib.loads((ROOT / "launchd" / "com.davidcasas.morningboard.board.plist").read_bytes())
        pull_job = plistlib.loads((ROOT / "launchd" / "com.davidcasas.morningboard.board-pull.plist").read_bytes())
        self.assertTrue(board["RunAtLoad"])
        self.assertTrue(board["KeepAlive"])
        self.assertTrue(pull_job["RunAtLoad"])
        self.assertEqual(pull_job["StartInterval"], 900)
        self.assertNotIn("KeepAlive", pull_job)
        for name in (
            "com.davidcasas.morningboard.board.plist",
            "com.davidcasas.morningboard.board-pull.plist",
        ):
            text = (ROOT / "launchd" / name).read_text(encoding="utf-8")
            self.assertIn("~/Library/LaunchAgents/", text)
            self.assertNotIn("LaunchDaemon", text)
            self.assertNotIn("sudo launchctl", text)
            self.assertNotIn("/Library/LaunchDaemons/", text)


if __name__ == "__main__":
    unittest.main()
