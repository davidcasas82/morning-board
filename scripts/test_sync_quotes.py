#!/usr/bin/env python3
"""Synthetic checks for the quotes CSV sync. No live sheet."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "sync_quotes",
    Path(__file__).with_name("sync-quotes.py"),
)
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)

HEADER = "quote_date,text,excerpt_credit,word_count,source_url,active"
SAMPLE = "\n".join(
    [
        HEADER,
        "2026-10-18,Alpha stand in.,Here,3,,TRUE",
        "2024-01-01,Beta stand in.,,3,,FALSE",
        '2025-05-01,"Gamma, stand in.",,3,,true',
        ",Delta stand in.,,2,,TRUE",
        ",,,,," + "TRUE",
    ]
)


class SyncQuotesTests(unittest.TestCase):
    def test_parse_keeps_active_rows(self):
        rows = sync.active_rows(sync.parse_csv(SAMPLE))
        self.assertEqual(len(rows), 3)
        self.assertEqual([row["quote_date"] for row in rows], ["2026-10-18", "2025-05-01", ""])
        self.assertEqual(rows[0]["excerpt_credit"], "Here")
        self.assertEqual(rows[1]["text"], "Gamma, stand in.")
        self.assertTrue(all(row["active"] is True for row in rows))
        self.assertTrue(all("Beta" not in row["text"] for row in rows))

    def test_blank_quote_date_is_kept(self):
        rows = sync.active_rows(sync.parse_csv(SAMPLE))
        blank = [row for row in rows if row["quote_date"] == ""]
        self.assertEqual(len(blank), 1)
        self.assertEqual(blank[0]["text"], "Delta stand in.")
        self.assertEqual(blank[0]["word_count"], 2)
        payload = sync.build_payload(rows)
        self.assertEqual(payload["rows"], 3)
        self.assertEqual(payload["newest"], "2026-10-18")
        self.assertEqual(payload["quotes"][-1]["quote_date"], "")

    def test_fetch_failure_keeps_last_file(self):
        directory = Path(tempfile.mkdtemp(prefix="morning-board-quotes-"))
        dest = directory / "quotes.json"
        rows = sync.active_rows(sync.parse_csv(SAMPLE))
        sync.atomic_write(dest, sync.build_payload(rows))
        before = dest.read_text(encoding="utf-8")

        def boom():
            raise RuntimeError("offline")

        code = sync.sync_to(dest, boom)
        after = dest.read_text(encoding="utf-8")
        self.assertEqual(code, 1)
        self.assertEqual(after, before)
        self.assertFalse((directory / "quotes.json.tmp").exists())

    def test_parse_failure_keeps_last_file(self):
        directory = Path(tempfile.mkdtemp(prefix="morning-board-quotes-"))
        dest = directory / "quotes.json"
        rows = sync.active_rows(sync.parse_csv(SAMPLE))
        sync.atomic_write(dest, sync.build_payload(rows))
        before = dest.read_text(encoding="utf-8")

        def bad():
            return sync.parse_csv("not,a,quote,sheet\n1,2,3,4\n")

        code = sync.sync_to(dest, bad)
        self.assertEqual(code, 1)
        self.assertEqual(dest.read_text(encoding="utf-8"), before)
        saved = json.loads(before)
        self.assertEqual(saved["rows"], 3)


if __name__ == "__main__":
    unittest.main()
