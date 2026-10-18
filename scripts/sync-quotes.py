#!/usr/bin/env python3
"""Export active rows from the published quotes CSV to a file outside the repo.

The CSV URL is quotesCsvUrl in the config on the mini:

  ~/Library/Application Support/morning-board/config.json

The repo ships scripts/quotes-config.example.json with that key. Copy it
onto the mini; this script does not read the example.

The quotes file is never written inside this repo. A failed fetch or parse
leaves the last good file in place and exits non-zero. Logs are counts only.

Install on the Mac mini (this does not install it for you):

  python3 scripts/sync-quotes.py --print-plist

Then copy the printed plist into ~/Library/LaunchAgents/ and bootstrap it.
The committed plist is the same job: boot, and 10:15 local time
(mid-morning Pacific when the mini's clock is Pacific).
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

SUPPORT = Path.home() / "Library" / "Application Support" / "morning-board"
DEFAULT_OUT = SUPPORT / "quotes.json"
CONFIG_PATH = SUPPORT / "config.json"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def csv_url_from(path: Path) -> str:
    try:
        data = load_json(path)
    except (OSError, ValueError):
        raise RuntimeError("config unreadable") from None
    if not isinstance(data, dict):
        raise RuntimeError("config unreadable")
    url = data.get("quotesCsvUrl")
    if not isinstance(url, str) or not url.strip():
        raise RuntimeError("config missing quotesCsvUrl")
    return url.strip()


def parse_csv(text: str):
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error:
        raise RuntimeError("csv parse") from None
    if not rows:
        raise RuntimeError("csv empty")
    return rows


def fetch_csv(url: str):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "morning-board-quotes"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError("csv http " + str(exc.code)) from None
    return parse_csv(raw.decode("utf-8-sig"))


def active_rows(values):
    if not values:
        return []
    header = [str(cell).strip() for cell in values[0]]
    index = {name: pos for pos, name in enumerate(header)}
    needed = ["quote_date", "text", "excerpt_credit", "word_count", "source_url", "active"]
    for name in needed:
        if name not in index:
            raise RuntimeError("csv is missing a column")

    def cell(row, name):
        pos = index[name]
        if pos >= len(row) or row[pos] is None:
            return ""
        return str(row[pos]).strip()

    exported = []
    for row in values[1:]:
        if cell(row, "active").upper() != "TRUE":
            continue
        text = cell(row, "text")
        if not text:
            continue
        word_raw = cell(row, "word_count")
        try:
            word_count = int(word_raw)
        except ValueError:
            word_count = len(text.split())
        exported.append(
            {
                "quote_date": cell(row, "quote_date"),
                "text": text,
                "excerpt_credit": cell(row, "excerpt_credit"),
                "word_count": word_count,
                "source_url": cell(row, "source_url"),
                "active": True,
            }
        )

    groups = {}
    for item in exported:
        groups.setdefault(item["quote_date"], []).append(item)
    dates = sorted([stamp for stamp in groups if stamp], reverse=True)
    ordered = []
    for stamp in dates:
        ordered.extend(groups[stamp])
    if "" in groups:
        ordered.extend(groups[""])
    return ordered


def summarize(rows):
    words = [row["word_count"] for row in rows if isinstance(row.get("word_count"), int)]
    dates = [row["quote_date"] for row in rows if row.get("quote_date")]
    return {
        "rows": len(rows),
        "newest": dates[0] if dates else "",
        "wordMin": min(words) if words else None,
        "wordMax": max(words) if words else None,
    }


def atomic_write(dest: Path, payload: dict):
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(dest.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, dest)


def build_payload(rows):
    summary = summarize(rows)
    return {
        "exportedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rows": summary["rows"],
        "newest": summary["newest"],
        "wordCountMin": summary["wordMin"],
        "wordCountMax": summary["wordMax"],
        "quotes": rows,
    }


def sync_to(dest: Path, fetch):
    """fetch() returns CSV rows or raises. On failure dest is unchanged."""
    try:
        values = fetch()
        rows = active_rows(values)
    except Exception as exc:
        kept = dest.is_file()
        print("csv read failed; kept last file" if kept else "csv read failed; no file yet", file=sys.stderr)
        print(type(exc).__name__, file=sys.stderr)
        return 1
    payload = build_payload(rows)
    atomic_write(dest, payload)
    summary = summarize(rows)
    word_min = summary["wordMin"] if summary["wordMin"] is not None else "-"
    word_max = summary["wordMax"] if summary["wordMax"] is not None else "-"
    print(
        "exported rows={rows} newest={newest} words={word_min}-{word_max}".format(
            rows=summary["rows"],
            newest=summary["newest"] or "-",
            word_min=word_min,
            word_max=word_max,
        )
    )
    return 0


def print_plist():
    repo = Path(__file__).resolve().parents[1]
    script = Path(__file__).resolve()
    home = Path.home()
    body = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.davidcasas.morningboard.quotes</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>{script}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>10</integer>
    <key>Minute</key>
    <integer>15</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>{home}/Library/Logs/morning-board-quotes.log</string>
  <key>StandardErrorPath</key>
  <string>{home}/Library/Logs/morning-board-quotes.err</string>
</dict>
</plist>
""".format(script=script, home=home)
    sys.stdout.write(body)
    print(
        "Not installed. Save that plist to {home}/Library/LaunchAgents/com.davidcasas.morningboard.quotes.plist".format(
            home=home
        ),
        file=sys.stderr,
    )
    print("Repo used for the script path: {repo}".format(repo=repo), file=sys.stderr)


def main(argv):
    if "--print-plist" in argv:
        print_plist()
        return 0
    dest = DEFAULT_OUT
    if "--out" in argv:
        pos = argv.index("--out")
        if pos + 1 >= len(argv):
            print("missing --out path", file=sys.stderr)
            return 2
        dest = Path(argv[pos + 1]).expanduser()
    try:
        url = csv_url_from(CONFIG_PATH)
    except RuntimeError as exc:
        kept = dest.is_file()
        print(str(exc), file=sys.stderr)
        print("kept last file" if kept else "no file yet", file=sys.stderr)
        return 1

    def fetch():
        return fetch_csv(url)

    return sync_to(dest, fetch)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
