#!/usr/bin/env python3
"""Checks for the LAN board host. No live mini, no quote text from the sheet."""

from __future__ import annotations

import importlib.util
import json
import plistlib
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


serve = load("serve_board", "serve-board.py")
pull = load("pull_board", "pull-board.py")


class BoardHostTests(unittest.TestCase):
    def start(self, root: Path, quotes: Path):
        server = serve.bind(root, quotes, "127.0.0.1", 0)
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

    def test_server_port_is_not_the_quotes_only_port(self):
        self.assertEqual(serve.PORT, 8780)
        self.assertNotEqual(serve.PORT, 8765)

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
