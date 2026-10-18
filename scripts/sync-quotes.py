#!/usr/bin/env python3
"""Export active rows from the private quotes sheet to a file outside the repo.

Reads with the Sheets API, scope spreadsheets.readonly. Reuses an existing
Google OAuth token (the photo-pipeline login or application-default
credentials) when that token can read the sheet. Does not start a second
login unless you pass --login.

The quotes file is never written inside this repo. A failed sheet read
leaves the last good file in place. Logs are counts only.

Install on the Mac mini (this does not install it for you):

  python3 scripts/sync-quotes.py --print-plist

Then copy the printed plist into ~/Library/LaunchAgents/ and bootstrap it.
The committed plist is the same job: boot, and 10:15 local time
(mid-morning Pacific when the mini's clock is Pacific).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SHEET_ID = "1hokFV6gHwsmrA9GkCLqegqiYUCR2NUcI3FlWDj7JZtA"
TAB = "quotes"
SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SUPPORT = Path.home() / "Library" / "Application Support" / "morning-board"
DEFAULT_OUT = SUPPORT / "quotes.json"
TOKEN_PATH = SUPPORT / "google-token.json"

TOKEN_CANDIDATES = [
    os.environ.get("MORNING_BOARD_GOOGLE_TOKEN"),
    str(TOKEN_PATH),
    str(Path.home() / ".config" / "morning-board" / "google-token.json"),
    str(Path.home() / ".config" / "gcloud" / "application_default_credentials.json"),
    str(Path.home() / "Library" / "Application Support" / "gcloud" / "application_default_credentials.json"),
]


def support_dir() -> Path:
    SUPPORT.mkdir(parents=True, exist_ok=True)
    return SUPPORT


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def candidate_paths():
    seen = set()
    for raw in TOKEN_CANDIDATES:
        if not raw:
            continue
        path = Path(raw).expanduser()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            yield path


def token_file():
    for path in candidate_paths():
        try:
            data = load_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and (data.get("refresh_token") or data.get("token")):
            return path, data
    return None, None


def refresh_access_token(data: dict) -> str:
    if data.get("token") and data.get("type") == "access_token":
        return data["token"]
    refresh = data.get("refresh_token")
    client_id = data.get("client_id")
    client_secret = data.get("client_secret")
    if not refresh or not client_id or not client_secret:
        raise RuntimeError("token file has no refresh token")
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("utf-8")
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    access = payload.get("access_token")
    if not access:
        raise RuntimeError("token refresh returned no access token")
    return access


def sheet_values(access_token: str):
    quoted = urllib.parse.quote(TAB + "!A:F", safe="")
    url = (
        "https://sheets.googleapis.com/v4/spreadsheets/"
        + SHEET_ID
        + "/values/"
        + quoted
    )
    request = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer " + access_token},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Do not include the response body. It can contain quote text.
        raise RuntimeError("sheet http " + str(exc.code)) from None
    return payload.get("values") or []


def active_rows(values):
    if not values:
        return []
    header = [str(cell).strip() for cell in values[0]]
    index = {name: pos for pos, name in enumerate(header)}
    needed = ["quote_date", "text", "excerpt_credit", "word_count", "source_url", "active"]
    for name in needed:
        if name not in index:
            raise RuntimeError("sheet is missing a column")

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
        "exportedAt": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "rows": summary["rows"],
        "newest": summary["newest"],
        "wordCountMin": summary["wordMin"],
        "wordCountMax": summary["wordMax"],
        "quotes": rows,
    }


def sync_to(dest: Path, fetch):
    """fetch() returns sheet values or raises. On failure dest is unchanged."""
    try:
        values = fetch()
    except Exception as exc:
        kept = dest.is_file()
        print("sheet read failed; kept last file" if kept else "sheet read failed; no file yet", file=sys.stderr)
        print(type(exc).__name__, file=sys.stderr)
        return 1
    rows = active_rows(values)
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


def login():
    path, data = token_file()
    client_id = os.environ.get("MORNING_BOARD_CLIENT_ID")
    client_secret = os.environ.get("MORNING_BOARD_CLIENT_SECRET")
    if data:
        client_id = client_id or data.get("client_id")
        client_secret = client_secret or data.get("client_secret")
    client_path = support_dir() / "oauth-client.json"
    if (not client_id or not client_secret) and client_path.is_file():
        client = load_json(client_path)
        installed = client.get("installed") or client.get("web") or client
        client_id = client_id or installed.get("client_id")
        client_secret = client_secret or installed.get("client_secret")
    if not client_id or not client_secret:
        print("existing Google client id was not found", file=sys.stderr)
        print("python3 scripts/sync-quotes.py --login", file=sys.stderr)
        return 1
    redirect = "http://127.0.0.1:8766/"
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + query
    print("Open this URL in a browser, then paste the redirected URL:")
    print(auth_url)
    pasted = input("Redirect URL: ").strip()
    parsed = urllib.parse.urlparse(pasted)
    code = urllib.parse.parse_qs(parsed.query).get("code", [None])[0]
    if not code and "code=" in pasted:
        code = pasted.split("code=", 1)[1].split("&", 1)[0]
    if not code:
        print("no authorization code", file=sys.stderr)
        return 1
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    refresh = payload.get("refresh_token") or (data or {}).get("refresh_token")
    if not refresh:
        print("no refresh token", file=sys.stderr)
        return 1
    support_dir()
    TOKEN_PATH.write_text(
        json.dumps(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh,
                "scope": SCOPE,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("saved token outside the repo")
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


def self_check():
    import tempfile

    values = [
        ["quote_date", "text", "excerpt_credit", "word_count", "source_url", "active"],
        ["2026-10-18", "Alpha stand in.", "Here", "3", "", "TRUE"],
        ["2024-01-01", "Beta stand in.", "", "3", "", "FALSE"],
        ["2025-05-01", "Gamma stand in.", "", "3", "", "true"],
        ["", "", "", "", "", "TRUE"],
    ]
    rows = active_rows(values)
    if len(rows) != 2:
        raise SystemExit("self-check row count")
    if rows[0]["quote_date"] != "2026-10-18" or rows[1]["quote_date"] != "2025-05-01":
        raise SystemExit("self-check order")
    if rows[0]["excerpt_credit"] != "Here" or rows[1]["excerpt_credit"] != "":
        raise SystemExit("self-check credit")

    directory = Path(tempfile.mkdtemp(prefix="morning-board-quotes-"))
    dest = directory / "quotes.json"
    atomic_write(dest, build_payload(rows))
    before = dest.read_text(encoding="utf-8")

    def boom():
        raise RuntimeError("offline")

    code = sync_to(dest, boom)
    after = dest.read_text(encoding="utf-8")
    if code != 1 or after != before:
        raise SystemExit("self-check keep")
    print("self-check ok")
    return 0


def main(argv):
    if "--self-check" in argv:
        return self_check()
    if "--print-plist" in argv:
        print_plist()
        return 0
    if "--login" in argv:
        return login()
    dest = DEFAULT_OUT
    if "--out" in argv:
        pos = argv.index("--out")
        if pos + 1 >= len(argv):
            print("missing --out path", file=sys.stderr)
            return 2
        dest = Path(argv[pos + 1]).expanduser()
    path, data = token_file()
    if not data:
        print("python3 scripts/sync-quotes.py --login", file=sys.stderr)
        return 1
    try:
        access = refresh_access_token(data)
    except Exception:
        print("python3 scripts/sync-quotes.py --login", file=sys.stderr)
        return 1

    def fetch():
        return sheet_values(access)

    return sync_to(dest, fetch)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
