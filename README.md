# morning-board

Household morning countdown for an old Surface Pro. One shared clock — not a clock per kid.

**Launch:** open [https://davidcasas82.github.io/morning-board/](https://davidcasas82.github.io/morning-board/) in Edge or Chrome and leave that tab open. Wi‑Fi is required for the URL, weather, and weekday-time updates. Fullscreen / F11 is optional. Not Assigned Access, not `--kiosk`.

Weekday **alarm** and **on-the-road** times live in [schedule.json](schedule.json). Change the file and push (or ask Grok to). The open tab polls it about once a minute. Downstairs is 15 minutes before on-the-road. Friday 6:35 / 7:45 is confirmed; Mon–Thu `6:15` / `7:15` are still placeholders until you replace them.

The gear on the page is today-only override and countdown preview. It does not write the week.

## Home network host

Port **8780** serves the board's own files on the LAN: `index.html`, `kiosk.html`, the `*.js` files, `schedule.json`, `board-config*.json`, `fonts/` and `/quotes.json`. Every other path is a 404. `GET /quotes.json` on 8780 reads `~/Library/Application Support/morning-board/quotes.json`. That file is not in git. Do not put a LAN address in the repo.

The page requests same-origin `/quotes.json`. On GitHub Pages that URL 404s, the quote fetch fails, and the clock stays up. The example config uses that path.

This does not change the frame's start URL. Leave the frame on [the Pages board](https://davidcasas82.github.io/morning-board/) until you change it by hand.

The server reads files from disk, so HTML updates do not need a restart. A second LaunchAgent runs `git pull --ff-only` every 15 minutes and at login. If the pull fails, the files already on disk stay.

Both jobs are user LaunchAgents in `~/Library/LaunchAgents/`. A user agent starts after login, not at the boot chime, so auto-login for `casasmacmini-server` is a dependency. Install does not use sudo. Cloning the repo does not install them.

1. Replace `__REPO__` and `__HOME__` in `launchd/com.davidcasas.morningboard.board.plist` and `launchd/com.davidcasas.morningboard.board-pull.plist`.
2. Copy both files to `~/Library/LaunchAgents/`.
3. As `casasmacmini-server`, bootstrap them:

```
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.davidcasas.morningboard.board.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.davidcasas.morningboard.board-pull.plist
```

From another machine on the LAN, open `http://<mini>:8780/`. The CSV sync LaunchAgent is unchanged.

## Kids' bedroom TVs

`scripts/kids-tv.py` turns on the two bedroom Vizio TVs at the board's alarm time, casts the board to them, and powers each off once at on-the-road time (not downstairs time). It reads `schedule.json` fresh on every run, so a schedule change carries over. A day whose `enabled` is not a real `true`, or that has no alarm or leaveBy, runs nothing. A date listed in `kids-tv-skip.json` (`{"skipDates": ["2026-10-30"]}`, read fresh each run) also runs nothing; a missing file means no skips, and a malformed one logs an error and runs nothing that day. It does not touch the board, the frame or the TCL TV.

```
python3 scripts/kids-tv.py plan --date 2026-10-29   # print the times, touch no TV
python3 scripts/kids-tv.py tick --dry-run            # print what is due now, send nothing
python3 scripts/kids-tv.py on --tv girls-room             # manual: power on, then cast
python3 scripts/kids-tv.py cast --tv girls-room           # manual: cast only
python3 scripts/kids-tv.py off --tv girls-room            # manual: power off
python3 scripts/kids-tv.py status                    # read each TV's power state
```

LaunchAgent `launchd/com.davidcasas.morningboard.kids-tv.plist` runs `tick` once a minute and at login. `tick` only reads the schedule and a small state file; it sends to a TV only when a step is due: one power-on and cast per TV after alarm, one power-off per TV after on-the-road. The state file (`~/Library/Application Support/morning-board/kids-tv-state.json`) keeps a reboot or a second tick from repeating a step. Nothing polls a TV, and a kid who switches away is left alone. A power-on more than 15 minutes after alarm is skipped for the day and logged, and after 20 minutes past on-the-road a missed power-off is skipped. Each step is written to the state file before it is sent; if the state file cannot be written, or is damaged, nothing is sent that day and an error is logged. A failed step is retried on later ticks, up to four times, then logged and dropped for the day.

Each TV's address and token live in an env file in `~/.config/vizio/` (`<PREFIX>IP` and `<PREFIX>TOKEN`). The repo only knows the neutral keys `boys-room` and `girls-room`, whose defaults are `boys-room.env` with prefix `VIZIO_BOYS_ROOM_` and `girls-room.env` with prefix `VIZIO_GIRLS_ROOM_`. To map a key to a different file, put `{"tvs": {"boys-room": {"env_file": "x.env", "prefix": "VIZIO_X_"}}}` in the mini-local `~/.config/kids-tv/config.json`, which is not in git and holds no tokens. They are read inside the script and never logged. Casting uses the `catt` binary at `~/Tools/catt-venv/bin/catt` (override with `KIDS_TV_CATT`). The log is `~/Library/Logs/kids-tv-morning-board.log`.

### Calendar run days

Which days the TVs come on is decided by three things together: `schedule.json` has the weekday on with an alarm and leaveBy, the run-days file says `run` is `true` for that date, and the date is not in `kids-tv-skip.json` (the skip list wins). The repo holds no calendar names or ids.

The run-days file, `~/.config/kids-tv/run-days.json`, is written by a scheduled Grok job (owned by the CTO agent) on the mini, atomically, a few evenings a week, looking 14 days ahead. This script only reads it, fresh on every run. Set another path with `"run_days_path"` in `~/.config/kids-tv/config.json`, `KIDS_TV_RUN_DAYS`, or `--run-days`.

```
{"generated_at": "2026-10-28T20:15:00-07:00", "horizon_end": "2026-11-11",
 "days": {"2026-10-29": {"kids_home": true, "no_school": false, "run": true, "why": "free text"}}}
```

Only `run` decides, and only a real `true` runs the TVs. `why` is never read or logged.

Fail-safe: the TVs never come on by default. On a day the schedule has on, if the file is missing, unreadable or not valid JSON, has no entry for the date, has a `run` that is not true or false, or has a `generated_at` that is more than 5 days old (or unreadable, or more than a day in the future), nothing runs that day. One `ERROR:` line is written to the log and to the `.err` file for that day, not one per minute. A `run` of `false` is a normal "no" and is not an error. A weekday the schedule has off never reads the file.

`plan --date YYYY-MM-DD --run-days /tmp/fixture.json` shows what a file would do without touching a TV.

Install as the mini account. This is the step that makes it live:

```
sed -e "s|__REPO__|$HOME/code/morning-board|g" -e "s|__HOME__|$HOME|g" \
  launchd/com.davidcasas.morningboard.kids-tv.plist > ~/Library/LaunchAgents/com.davidcasas.morningboard.kids-tv.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.davidcasas.morningboard.kids-tv.plist
```
