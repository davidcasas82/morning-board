#!/usr/bin/env python3
"""Run the page's node tests from the Python suite."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@unittest.skipIf(NODE is None, "node is not on PATH; skipped test-daily.js and test-quotes.js")
class NodeSuiteTests(unittest.TestCase):
    def test_node_suites_pass(self):
        for name in ("test-daily.js", "test-quotes.js"):
            with self.subTest(name):
                result = subprocess.run([NODE, name], cwd=ROOT, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, "%s failed:\n%s%s" % (name, result.stdout, result.stderr))


if __name__ == "__main__":
    unittest.main()
