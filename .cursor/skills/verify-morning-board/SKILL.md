---
name: verify-morning-board
description: "Drive the morning-board household kiosk in a real browser the way a person uses the Surface tab. Use when proving behavior of index.html, kiosk.html, or schedule.json, or when asked to verify the clock, adult sheet, or on-page numpad."
---

# Verify morning-board

Morning-board is a static GitHub Pages kiosk. There is no build, no package install, and no test runner. `index.html` redirects to `kiosk.html`. The page a person actually uses is the kiosk: a household clock, a month calendar, weather, and an adult sheet opened from the gear. The Surface has no keyboard, so times are entered only through the on-page numpad (`#timePad`). Weekday alarm and on-the-road times come from `schedule.json` served next to the page.

Drive only an instance this skill started. Two runs can sit side by side when each has its own port, Chrome profile, and `MORNING_BOARD_RUN_ID`. Do not attach to https://davidcasas82.github.io/morning-board/ or to any Chrome that is already open. That tab is the household tablet, and its `localStorage` is the real today's override.

The helper is `control-morning-board`. From the repo root:

```bash
.cursor/skills/verify-morning-board/scripts/control-morning-board <launch|doctor|cleanup|browser>
```

It needs Node 22 or newer (`WebSocket` is global), `python3`, `google-chrome`, `lsof`, and `git`. No npm install.

## Launch

From the repo root, start a private static server and a headless Chrome whose profile is empty:

```bash
.cursor/skills/verify-morning-board/scripts/control-morning-board launch
```

Ready means stdout contains `ready=true`, `url=http://127.0.0.1:<port>/kiosk.html`, and `evidence_dir=...`. The browser opened `/`, followed `index.html`'s redirect to `kiosk.html`, and `#gear` is in the document. stderr prints `export MORNING_BOARD_RUN_ID=<id>`. Export that id and pass it to every later command. Launch chooses a free HTTP port and a free Chrome debugging port unless `MORNING_BOARD_PORT` is set. `MORNING_BOARD_ROOT` overrides the repo root; the default is the repo that contains this skill.

State for the run (pid files, Chrome profile, logs) lives in `/tmp/morning-board-verify/<run-id>/`. Proof artifacts live in `/tmp/morning-board-verify/<run-id>/evidence/`. Override the evidence directory only with `MORNING_BOARD_EVIDENCE_DIR`, and do not point it at the state directory's other files.

Launch fails closed: if the page never reaches `kiosk.html`, it kills the processes it started and exits non-zero. Logs are `/tmp/morning-board-verify/<run-id>/server.log` and `chrome.log`.

## Doctor

Run this before driving, and again whenever the page looks stale. It does not click, type, or change `localStorage`.

```bash
.cursor/skills/verify-morning-board/scripts/control-morning-board doctor
```

Stdout starts with `ok=true` only when all of these hold:

- The recorded Python pid is alive, its command line is `http.server` on this run's port, and `lsof` shows that pid owns the listen socket.
- The recorded Chrome pid is alive, its command line contains this run's `--remote-debugging-port` and `--user-data-dir`, and it owns the debugging port.
- `GET /kiosk.html` is 200, the body hash matches `kiosk.html` on disk, and the body contains `#timePad`, `aria-label="Adult settings"`, and `data-key="done"`.
- `GET /schedule.json` is 200, `timezone` is `America/Los_Angeles`, and Friday is alarm `06:30` / on the road `07:40`.
- `GET /` is the redirect page and mentions `kiosk.html`.
- The live Chrome target URL is this origin's `kiosk.html`, and the document contains `#gear` and `#timePad`.

`browser_timezone=` is the timezone Chrome is using to turn `schedule.json` hours into a countdown. The household clock is correct when that value is `America/Los_Angeles`. A different timezone does not fail the doctor; it does mean countdown windows are shifted. `git_rev=` is the checkout being served. There is no separate build id.

Exit code is 1 when `ok=false`. Do not drive a failed doctor.

## Drive

`browser` talks to the Chrome this run started, using trusted mouse events (pointerdown, which is what the numpad and time fields listen for). Do not call page functions from the console. Prefer ids, `data-key`, and accessible names over coordinates.

```bash
.cursor/skills/verify-morning-board/scripts/control-morning-board browser open --path /
.cursor/skills/verify-morning-board/scripts/control-morning-board browser wait --selector '#gear'
.cursor/skills/verify-morning-board/scripts/control-morning-board browser wait --selector '#scheduleStatus' --text 'Loaded from GitHub'
.cursor/skills/verify-morning-board/scripts/control-morning-board browser click --role button --name "Adult settings"
.cursor/skills/verify-morning-board/scripts/control-morning-board browser click --selector '#oneOffInput'
.cursor/skills/verify-morning-board/scripts/control-morning-board browser click --selector 'button[data-key="2"]'
.cursor/skills/verify-morning-board/scripts/control-morning-board browser text --selector '#timePadPreview'
.cursor/skills/verify-morning-board/scripts/control-morning-board browser value --selector '#oneOffInput'
.cursor/skills/verify-morning-board/scripts/control-morning-board browser attr --selector '#oneOffInput' --name readonly
.cursor/skills/verify-morning-board/scripts/control-morning-board browser attr --selector '#oneOffInput' --name inputmode
.cursor/skills/verify-morning-board/scripts/control-morning-board browser attr --selector body --name data-mode
.cursor/skills/verify-morning-board/scripts/control-morning-board browser active
.cursor/skills/verify-morning-board/scripts/control-morning-board browser storage --key morning-board-today
.cursor/skills/verify-morning-board/scripts/control-morning-board browser storage --key morning-board-oneoff
.cursor/skills/verify-morning-board/scripts/control-morning-board browser screenshot --path /tmp/morning-board-verify/$MORNING_BOARD_RUN_ID/evidence/shot.png --selector '#timePad'
.cursor/skills/verify-morning-board/scripts/control-morning-board browser snapshot --aria --path /tmp/morning-board-verify/$MORNING_BOARD_RUN_ID/evidence/shot.aria.txt
```

Stable handles:

| Handle | What it is |
| --- | --- |
| `#gear` / button name `Adult settings` | Opens the adult sheet. Adds class `open` on `#sheetBg`. |
| `#sheetBg.open` | Sheet is visible. |
| `#scheduleStatus` | `Loaded from GitHub. School mornings here: Mon, Thu, Fri.` after `schedule.json` loads. |
| `#weekRows` | Read-only week. On/off buttons are `disabled`. |
| `#leaveInput` | Today's on-the-road field. Readonly, `inputmode=none`. |
| `#oneOffInput` | One-off leave-by field. Same numpad behavior. |
| `#timePad` | Pad root. Class `hidden` until a time field is opened. |
| `#timePadLabel` | `Today road time` or `One-off leave-by`. |
| `#timePadPreview` | Digits as typed, or `--:--` when empty. |
| `button[data-key="0"]` … `"9"`, `"back"`, `"colon"`, `"done"` | Pad keys. Backspace's accessible name is `Backspace`. Done's name is `Done`. |
| `#applyLeave` | `Use this road time today`. Writes `localStorage` `morning-board-today` and closes the sheet. |
| `#resetLeave` | Clears today's override. Accessible name uses a curly apostrophe: `Clear today’s override`. |
| `#startOneOff` | `Start one-off countdown`. |
| `#clearOneOff` | `Clear one-off`. Leaves the sheet open. |
| `#jumpFive` | `Jump to 5 min left`. |
| `#jumpZero` | `Hit zero`. |
| `#clearPreview` | `Back to live clock`. |
| `#closeSheet` | `Close`. |
| `body` `data-mode` | `clock`, `countdown`, or `late`. |
| `body` `data-band` | `plenty`, `amber`, `orange`, `red`, or `late`. |
| `#status-main` | Date line, `DOWNSTAIRS BY …`, `LEAVE BY …`, or `+M:SS LATE · …`. |
| `#ampm-indicator` | `AM` / `PM` on the wall clock, or `LEFT` during a countdown. |
| `#leave-now` | `DOWNSTAIRS NOW` or `LEAVE NOW`. Shown in late mode. |
| `#cal-header` | Month and year, for example `SEP 2026`. |
| `.cal-day.today` | Today's date number. |

`open --path /` follows the same redirect as the tablet. `open --path /kiosk.html` skips the redirect. `wait` polls until the selector exists; `--text` also requires that substring. `click` scrolls the element into view and dispatches a real left-button press at its center. `storage` only reads `localStorage`.

Recipes for each user-facing feature are in `features/`. Read `features/README.md` first. A proof of one feature does not cover the others.

## Evidence

Put proof in the `evidence_dir` launch printed. That directory is `/tmp/morning-board-verify/<run-id>/evidence/` unless `MORNING_BOARD_EVIDENCE_DIR` is set. Cleanup never deletes it.

For a passing proof, capture both the action and the state it caused:

- Append each command's stdout to `$EVIDENCE/actions.log`.
- Take a screenshot and an ARIA snapshot while the control is mid-action (the numpad is open, the preview digits are on screen) and another pair after the result (pad closed, countdown visible, sheet status updated).
- The ARIA snapshot's first line is the page URL. The tree includes `RootWebArea` name `Morning board` and the control you used.
- Read side effects back through the product, not through a setter. Today's override and one-off leave-by are `localStorage` keys `morning-board-today` and `morning-board-oneoff`. After a write, read the key, then reopen the sheet and read `#scheduleStatus` / `#oneOffStatus` / the week row. `GET /schedule.json` must still show Friday `07:40` after an override or a one-off. Those controls do not edit the repo file.
- Digits on the clock are SVG segments, not text. Prove clock mode with `body` `data-mode`, `#status-main`, `#ampm-indicator`, and `#leave-now`.
- Weather is a real request to Open-Meteo (Irvine fallback `33.6695,-117.8231` when geolocation is denied). Do not stub it. Do not treat a weather failure as proof of the clock or the numpad. A failed fetch shows `FETCH ERROR` in `#weather-desc`.

There is no dry-run mode. Preview (`#jumpFive`, `#jumpZero`) does not change the computer clock and does not write `localStorage`, but it does change the kiosk's displayed mode until `#clearPreview`. Observe `data-mode` and the storage keys; do not infer that from the button name.

## Cleanup

Stop only the server and Chrome recorded in this run's `run.json`, then delete the profile, logs, and `run.json`. Leave the evidence directory in place.

```bash
.cursor/skills/verify-morning-board/scripts/control-morning-board cleanup
```

Stdout includes `cleaned=true` and `evidence_remains=true`. Exit code is 1 if a recorded pid is still alive or the evidence directory is gone. After cleanup, `doctor` must fail because `run.json` is gone. The files under `evidence_dir` must still be readable. Run cleanup after a failed drive too, so the next launch is not sharing a port or a profile.

## Helpers

The only helper is the executable Node script:

```bash
.cursor/skills/verify-morning-board/scripts/control-morning-board launch
.cursor/skills/verify-morning-board/scripts/control-morning-board doctor
.cursor/skills/verify-morning-board/scripts/control-morning-board browser <open|wait|click|text|value|attr|storage|active|screenshot|snapshot>
.cursor/skills/verify-morning-board/scripts/control-morning-board cleanup
```

Optional flags: `--run <id>` (else `MORNING_BOARD_RUN_ID`, else `/tmp/morning-board-verify/active-run-id`), `browser wait --timeout <ms>` (default 8000), `browser screenshot --selector <css>` to scroll that element into view before the shot. Feature recipes in `features/` are the commands to run; this section is the full command surface.
