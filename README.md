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

The power-off first stops the cast: it quits the receiver app and waits, 15 seconds at most, until the receiver is idle, because a TV powered off while the board is still casting can turn itself back on. It then powers off and reads the power state 5, 30, 60 and 90 seconds later. Only a reading of on sends another off: it stops the cast and powers off again, up to three power-offs in all. A reading the TV does not answer, or does not give as on or off, is "unknown": it never counts as off and never sends another off (a TV can restart itself a few minutes after standby and be unreachable for a minute or more). If the TV is still on after the third off, or is not read off at 90 seconds, it logs an `ERROR`, writes an alert, and exits nonzero; a later tick does not try again. That 90-second check is the only time the job reads a TV's state after turning it on. The Chromecast's uptime (port 8008, `/setup/eureka_info`, the `uptime` field only) is logged at each on and off, so a restart shows in the log.

Errors are logged with their reason, for example `request failed: URLError(OSError): [Errno 65] No route to host`, and a failed `catt` call logs the last line it printed, with any address redacted. A power-on that gets no answer at all (refused, timed out, no route) may be a TV restarting, so it is retried on each tick for up to 4 minutes before it counts as a failure. If a TV does not answer at its configured address, the script looks it up once by its advertised name on mDNS (`_viziocast._tcp`, with macOS `dns-sd`) and tries there; the names go in the local config as `"mdns_name"` under each TV.

```
python3 scripts/kids-tv.py plan --date 2026-10-29   # print the times, touch no TV
python3 scripts/kids-tv.py tick --dry-run            # print what is due now, send nothing
python3 scripts/kids-tv.py on --tv girls-room             # manual: power on, then cast
python3 scripts/kids-tv.py cast --tv girls-room           # manual: cast only
python3 scripts/kids-tv.py off --tv girls-room            # manual: power off
python3 scripts/kids-tv.py status                    # read each TV's power state
python3 scripts/kids-tv.py info --tv girls-room           # manual: which cast app is running
python3 scripts/kids-tv.py stop --tv girls-room           # manual: stop the cast
python3 scripts/kids-tv.py castcheck --tv all             # manual: cast, read the app, stop (no power)
python3 scripts/kids-tv.py netcheck                       # open each TV and Sonos port once
python3 scripts/kids-tv.py alerts                         # list alerts not yet acknowledged
```

### Casting

The script casts by itself, with only the Python standard library, so it runs under `/usr/bin/python3` (3.9) from launchd. It opens TLS to the Chromecast's port 8009 and speaks the Cast protocol: length-prefixed protobuf `CastMessage`s, encoded by hand, answering the receiver's heartbeat. `cast` launches DashCast (app `84912283`, the app `catt cast_site` uses), connects to the app, sends it the board URL, then reads the receiver status and needs DashCast running. `info` reads the receiver status; the stop quits the running app.

A receiver status the script cannot read (empty, or an app with no id) is "unknown", never idle: the stop is sent anyway, without a session. The same holds with catt, whose `catt info -j` prints `{}` when it cannot reach the device.

`catt` is used only if you ask for it: set `KIDS_TV_CATT` (or `--catt`) to its path, for example `~/Tools/catt-venv/bin/catt`. Under launchd a catt in its own Python may be blocked by macOS Local Network privacy (it then finds no device), which is why casting no longer needs it.

Only one command acts on a TV at a time: each TV has a lock file, `kids-tv-<key>.lock`, beside the state file (`fcntl.flock`). `tick` skips a TV another command holds, with a log line, and tries again next minute. A manual command waits up to 120 seconds, then logs an `ERROR:` and exits 1. With `--tv all`, a TV that fails (unreachable, busy, or an off that does not stick) is logged and the next TV is still done; the command exits 1 if any TV failed.

LaunchAgent `launchd/com.davidcasas.morningboard.kids-tv.plist` runs `tick` once a minute and at login. `tick` reads the schedule and a small state file; it sends to a TV only when a step is due: one power-on and cast per TV after alarm, one power-off per TV after on-the-road. The state file (`~/Library/Application Support/morning-board/kids-tv-state.json`) keeps a reboot or a second tick from repeating a step. It reads a TV's power state only for 90 seconds after a power-off, so a kid who switches away is left alone. A power-on 15 minutes or more after alarm is skipped for the day and logged, and a power-off 20 minutes or more after on-the-road is skipped. Each step is written to the state file before it is sent; if the state file cannot be written, or is damaged, nothing is sent that day and an `ERROR:` is logged once for the day (not every minute; the marker is `kids-tv-error-day.json` beside the log). A failed step is retried on later ticks, up to four times, then logged and dropped for the day.

Alerts go to `~/Library/Application Support/morning-board/kids-tv-alert.json` (override with `KIDS_TV_ALERT` or `--alert`), outside the repo, with an `ALERT:` line in the log. One is written at once for the first failed power-on of the day (after the 4-minute restart window for a TV that does not answer), an off that does not stick, and the Sonos failures below; at most one per TV, step and date. A cast that fails writes one alert on its first try (`cast`) and one more when its tries run out (`cast-final`). The file is `{"alerts": [{"date", "tv", "step", "error", "at"}, ...]}`, newest last, the newest 60 kept, replaced atomically. `step` is `on`, `off`, `cast`, `cast-final`, `netcheck`, `sonos` or `sonos-sync`; `tv` is a TV key, a Sonos room key, or `all`.

`alerts` lists the alerts not yet acknowledged, each with an id `date/tv/step` (`--json` for JSON, `--all` to include acknowledged ones). `alerts --ack ID ...` or `alerts --ack-all` marks them read in `kids-tv-alert-ack.json` beside the alert file. The job never reads that file and `alerts` never writes the alert file, so a reader and the job do not write the same file.

The reader is an alert-check routine outside this repo: at 6:20, 7:30 and 8:45 PM it runs `kids-tv.py alerts --json`, passes anything new on, then acknowledges it with `--ack`. Nothing in this repo sends a message.

### Network check

`netcheck` opens one TCP connection, then closes it, to each port the job uses: each TV's Cast port 8009, its Cast info port 8008 and SmartCast 7345, and each Sonos room's 1400. It sends nothing on them. A port that does not answer is retried for `--settle` seconds (default 0). It logs one line with each result: ok, refused, timed out, no route (`EHOSTUNREACH`), network unreachable or host down. If every connection fails at once with no route to host, it writes one alert, tv `all`, step `netcheck`, starting `Local Network permission blocked`: macOS fails a process that lacks the Local Network permission that way at once. Otherwise each TV or room with a failing port gets its own `netcheck` alert. It exits 1 if anything failed.

`launchd/com.davidcasas.morningboard.kids-tv-netcheck.plist` runs `netcheck --settle 180` at each login, so after the weekly restart. Its program is `/usr/bin/python3` itself, as for the kids-tv job, so it tests the same permission. `launchd/com.davidcasas.morningboard.kids-tv-castcheck.plist` is a hand-run test that casts each TV from launchd, reads the app and stops it; it has no schedule.

Each TV's address and token live in an env file in `~/.config/vizio/` (`<PREFIX>IP` and `<PREFIX>TOKEN`). The repo only knows the neutral keys `boys-room` and `girls-room`, whose defaults are `boys-room.env` with prefix `VIZIO_BOYS_ROOM_` and `girls-room.env` with prefix `VIZIO_GIRLS_ROOM_`. To map a key to a different file, put `{"tvs": {"boys-room": {"env_file": "x.env", "prefix": "VIZIO_X_"}}}` in the mini-local `~/.config/kids-tv/config.json`, which is not in git and holds no tokens. They are read inside the script and never logged. The log is `~/Library/Logs/kids-tv-morning-board.log`.

### Local Network helper app (prototype, not installed)

After a restart, macOS Local Network privacy can block the kids-tv job: launchd starts `/usr/bin/python3`, macOS charges its connections to `com.apple.python3`, and a launchd agent gets no Terminal exemption. `MorningBoardTVHelper.app` is a small app bundle that gives the job its own identity, `com.davidcasas.morningboard.kidstv-helper`, so the permission is granted to that app once and recorded under its name.

- `helper/kids-tv-helper.c` is the app's only executable. It starts `/usr/bin/python3 <abs path>/kids-tv.py ARGS` as a child process (`posix_spawn`, not `exec`), waits for it, passes on SIGTERM, SIGINT and SIGHUP, and exits with the child's status (128+N for signal N). A child keeps its parent's responsible process, so Python, and anything Python starts, counts as the app. It refuses any script not named `kids-tv.py` or not given by absolute path (exit 64).
- `helper/Info.plist` sets `LSUIElement`, `NSLocalNetworkUsageDescription`, and `NSBonjourServices` with `_viziocast._tcp`, the only service the script resolves (`dns-sd -L`). The script connects to configured addresses and never browses for `_googlecast._tcp`, so that is not listed.
- `scripts/build-kids-tv-helper.sh [OUT_DIR]` builds `MorningBoardTVHelper.app` into `build/` (ignored by git) with clang, ad-hoc signs it (`codesign --sign - --identifier com.davidcasas.morningboard.kidstv-helper --options runtime`), verifies it, and prints its cdhash. An ad-hoc signature's designated requirement is that cdhash. The build is reproducible: the same source and toolchain give the same cdhash from any checkout path, so a rebuild from the same commit keeps the grant. A change to `kids-tv-helper.c` or `Info.plist` (or a new clang) gives a new cdhash and needs a new grant. `kids-tv.py` itself is not part of the signature; changing it changes nothing here.
- `launchd/com.davidcasas.morningboard.kids-tv-helper.plist` is the tick job run through the app (it replaces `kids-tv.plist`; never load both). `launchd/com.davidcasas.morningboard.kids-tv-helper-netcheck.plist` is the login netcheck run through the app. Both point at `__HOME__/Applications/MorningBoardTVHelper.app/Contents/MacOS/kids-tv-helper` and set `AssociatedBundleIdentifiers` to the app's identifier.

Nothing installs these yet. `scripts/test_kids_tv_helper.py` checks the plist keys and templates and, on a Mac with clang, builds the app and checks its signature, the cdhash across two builds, and the launcher.

### Calendar run days

Which days the TVs come on is decided by three things together: `schedule.json` has the weekday on with an alarm and leaveBy, the run-days file says `run` is `true` for that date, and the date is not in `kids-tv-skip.json` (the skip list wins). The repo holds no calendar names or ids.

The run-days file, `~/.config/kids-tv/run-days.json`, is written by a separate scheduled job on the mini, atomically, a few evenings a week, looking 14 days ahead. This script only reads it, fresh on every run. Set another path with `"run_days_path"` in `~/.config/kids-tv/config.json`, `KIDS_TV_RUN_DAYS`, or `--run-days`.

```
{"generated_at": "2026-10-28T20:15:00-07:00", "horizon_end": "2026-11-11",
 "days": {"2026-10-29": {"kids_home": true, "no_school": false, "run": true, "why": "free text"}}}
```

Only `run` decides, and only a real `true` runs the TVs. `why` is never read or logged.

Fail-safe: the TVs never come on by default. On a day the schedule has on, if the file is missing, unreadable or not valid JSON, has no entry for the date, has a `run` that is not true or false, or has a `generated_at` that is more than 5 days old (or unreadable, or more than a day in the future), nothing runs that day. One `ERROR:` line is written to the log and to the `.err` file for that day, not one per minute. A `run` of `false` is a normal "no" and is not an error. A weekday the schedule has off never reads the file.

`plan --date YYYY-MM-DD --run-days /tmp/fixture.json` shows what a file would do without touching a TV. `plan` and `tick` both read a `generated_at` without a zone as the schedule's zone (America/Los_Angeles).

### Kids' rooms Sonos alarms

With a `"sonos"` block in the local config, `tick` also keeps the two kids' rooms' native Sonos alarms in step with the run days. Without it, nothing Sonos runs. It uses plain UPnP SOAP on port 1400 (standard library only), and touches only the two rooms named in the local config. The job changes only the one alarm it owns in each room, by ID: `alarm_id` in the local config if set, else the ID it saved in `kids-sonos-alarms.json` beside the state file, else the backup's alarm ID if that alarm is still in the room (then saved). No other alarm is changed.

```
{"sonos": {"backup": "~/.config/kids-sonos/alarms-backup-YYYYMMDD.json",
           "rooms": {"boys-room": {"room": "<Sonos room name>", "ip": "<address>", "alarm_id": "<ID>"},
                     "girls-room": {"room": "<Sonos room name>", "ip": "<address>", "alarm_id": "<ID>"}},
           "play_check": true}}
```

- Evening: on the first tick at or after 20:30, each room's alarm is set for tomorrow. If tomorrow is a run day (schedule, run-days file and skip list, as for the TVs), the alarm is on at the schedule's alarm time with a recurrence of that one weekday (`ON_<d>`, 0 is Sunday). If not, it is off. Only on/off, time and recurrence change; every other field stays as it is. Only while the job owns no alarm in a room, and only when it should be on, is one created from the backup (source, volume, duration, play mode, linked zones); its ID is saved, and the intent is saved first, so a lost answer never makes a second one. If an owned alarm is gone, an alert is written and nothing is created. Nothing is written when the alarm already matches, and every write is re-read and checked. If tomorrow cannot be decided (for example the run-days file is stale), the alarm is left as it is and retried each tick until 23:00; then an alert is written. A failed sync is retried each tick, up to four times, with an alert on the first failure.
- Morning: on a run day, each room is watched from 1 minute before alarm to 2 minutes after it. After the TV part, each tick reads the room's transport state every 5 s for the rest of that tick; the read count is kept in the state across ticks. Each of these reads gives up after 3 s, and a round of reads starts only if it would end, even with every room timing out, within 50 s of the tick's start. So the watch reads never run a tick past 50 s; only a TV part that alone takes longer than that can make the tick longer, and then no reads are made that tick. The one exception is the decide tick after the window (the last read, Play, then the check), which has no budget check: with a slow speaker it can add up to about 22 s per room (3 s read, 8 s Play, 3 s settle, 8 s check). Any `PLAYING` reading means the alarm fired: the room is marked OK for the day and left alone, even after a short play that stopped. A room already playing at 1 minute before alarm counts as fired too. A room never seen playing in the whole window gets one last read on the first tick after it, then is sent Play once (which resumes its current queue) and an alert is written. With `"play_check": false` in the `sonos` block (default `true`), no Play is ever sent; the alert is still written. Each room has its own lock (`kids-tv-sonos-<key>.lock`), so two commands never watch or play the same room.

The state for this is `kids-sonos-state.json` beside the TV state file.

Install as the mini account. This is the step that makes it live:

```
sed -e "s|__REPO__|$HOME/code/morning-board|g" -e "s|__HOME__|$HOME|g" \
  launchd/com.davidcasas.morningboard.kids-tv.plist > ~/Library/LaunchAgents/com.davidcasas.morningboard.kids-tv.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.davidcasas.morningboard.kids-tv.plist
```
