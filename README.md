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

`scripts/kids-tv.py` turns on the two bedroom Vizio TVs at the board's alarm time, casts the board to them, and powers each off once at on-the-road time (not downstairs time). It reads `schedule.json` fresh on every run, so a schedule change carries over. Monday through Friday are on, with Tuesday and Wednesday using the Monday/Thursday times (06:00 / 07:10) and Friday at 06:30 / 07:40. Saturday and Sunday stay off. A day whose `enabled` is not a real `true`, or that has no alarm or leaveBy, runs nothing. A date listed in `kids-tv-skip.json` (`{"skipDates": ["2026-10-30"]}`, read fresh each run) also runs nothing; a missing file means no skips, and a malformed one logs an error and runs nothing that day. It does not touch the board, the frame or the TCL TV. The alarm and leaveBy a run day actually uses come from `resolved_day_times` (see "Local times").

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
python3 scripts/kids-tv.py healthcheck                    # morning on/off checks, if one is due
python3 scripts/kids-tv.py healthcheck --slot evening     # the 8:45 PM check
```

### Casting

The script casts by itself, with only the Python standard library, so it runs under `/usr/bin/python3` (3.9) from launchd. It opens TLS to the Chromecast's port 8009 and speaks the Cast protocol: length-prefixed protobuf `CastMessage`s, encoded by hand, answering the receiver's heartbeat. `cast` launches DashCast (app `84912283`, the app `catt cast_site` uses), connects to the app, sends it the board URL, then reads the receiver status and needs DashCast running. `info` reads the receiver status; the stop quits the running app.

A receiver status the script cannot read (empty, or an app with no id) is "unknown", never idle: the stop is sent anyway, without a session. The same holds with catt, whose `catt info -j` prints `{}` when it cannot reach the device.

`catt` is used only if you ask for it: set `KIDS_TV_CATT` (or `--catt`) to its path, for example `~/Tools/catt-venv/bin/catt`. Under launchd a catt in its own Python may be blocked by macOS Local Network privacy (it then finds no device), which is why casting no longer needs it.

Only one command acts on a TV at a time: each TV has a lock file, `kids-tv-<key>.lock`, beside the state file (`fcntl.flock`). `tick` skips a TV another command holds, with a log line, and tries again next minute. A manual command waits up to 120 seconds, then logs an `ERROR:` and exits 1. With `--tv all`, a TV that fails (unreachable, busy, or an off that does not stick) is logged and the next TV is still done; the command exits 1 if any TV failed.

LaunchAgent `launchd/com.davidcasas.morningboard.kids-tv.plist` runs `tick` once a minute and at login, through `MorningBoardTVHelper.app` (see "Local Network helper app" below). `tick` reads the schedule and a small state file; it sends to a TV only when a step is due: one power-on and cast per TV after alarm, one power-off per TV after on-the-road. The state file (`~/Library/Application Support/morning-board/kids-tv-state.json`) keeps a reboot or a second tick from repeating a step. It reads a TV's power state only for 90 seconds after a power-off, so a kid who switches away is left alone. A power-on 15 minutes or more after alarm is skipped for the day and logged, and a power-off 20 minutes or more after on-the-road is skipped. Each step is written to the state file before it is sent; if the state file cannot be written, or is damaged, nothing is sent that day and an `ERROR:` is logged once for the day (not every minute; the marker is `kids-tv-error-day.json` beside the log). A failed step is retried on later ticks, up to four times, then logged and dropped for the day.

Alerts go to `~/Library/Application Support/morning-board/kids-tv-alert.json` (override with `KIDS_TV_ALERT` or `--alert`), outside the repo, with an `ALERT:` line in the log. One is written at once for the first failed power-on of the day (after the 4-minute restart window for a TV that does not answer), an off that does not stick, and the Sonos failures below; at most one per TV, step and date. A cast that fails writes one alert on its first try (`cast`) and one more when its tries run out (`cast-final`). The file is `{"alerts": [{"date", "tv", "step", "error", "at"}, ...]}`, newest last, the newest 60 kept, replaced atomically. `step` is `on`, `off`, `cast`, `cast-final`, `netcheck`, `sonos`, `sonos-sync`, or `times`; `tv` is a TV key, a Sonos room key, or `all`.

`alerts` lists the alerts not yet acknowledged, each with an id `date/tv/step` (`--json` for JSON, `--all` to include acknowledged ones). `alerts --ack ID ...` or `alerts --ack-all` marks them read in `kids-tv-alert-ack.json` beside the alert file. The job never reads that file and `alerts` never writes the alert file, so a reader and the job do not write the same file.

### Health check

`healthcheck` is the local stand-in for the old outside alert check. It reads logs and state. It sends nothing to a TV or a speaker, and it never reads `~/.config/vizio`. With no `--slot`, it asks `resolved_day_times(schedule, day)` when an on or off check is due, and runs each one once per day (the marker is `kids-tv-healthcheck-ran.json` beside the status file). `--slot morning-on`, `morning-off`, or `evening` runs that check immediately, which is how the tests call it.

That is the same `resolved_day_times` as the TV job (see "Local times"): a date wins over the weekday, which wins over `schedule.json`. A missing file is the schedule. A file that exists but will not parse raises `TimesError` after one alert, and the healthcheck treats that morning as not a run day. The reason is "not a run day, alert already sent". Later runs, including the 5-minute job and the evening job, do not POST and do not write that alert again. Saturday and Sunday are never a morning check.

The on check is due at alarm plus 10 minutes (`ON_CHECK_AFTER`), after power-on retries (about four minutes) and cast retries (about seven). The off check is due at `leaveBy` plus `OFF_GRACE` (20 minutes). Those are not clock times. In `schedule.json`, Monday and Thursday are alarm `06:00` / leaveBy `07:10`, so the off check is due at 07:30. Friday is alarm `06:30` / leaveBy `07:40`, so the off check is due at 08:00. A check at 07:30 on Friday is not an off check.

On a run day, the on check wants both TVs turned on and cast. If `config.json` has a `sonos` block, both kids' rooms must have fired or been sent Play. Text the check writes uses the room key, not the room name from the local config. The off check wants both TVs off. It runs only when today is a run day; otherwise it passes the TV part. The evening check is daily at 20:45. With Sonos, tonight's sync must have set both rooms for tomorrow: on at tomorrow's alarm when tomorrow is a run day, off when it is not. With Sonos off, a run-day tomorrow is flagged because the alarms are not automated. Every check that runs also flags unacknowledged alerts (`alerts --json`), except a first-try `on` or `cast` alert whose step later succeeded. The on check flags a morning-board log not written in the last 10 minutes. The off check and the evening check do not: the job writes only when it acts, so a clean off whose last line is the off itself (07:14 for a 07:30 check) and a clean evening whose last line is the 20:30 sync are not failures. A retry line (`error`, `URLError`, or `No route to host`) is not a failure when that step later succeeded. An `ALERT:`, `No route to host`, or `Local Network` line in the last 2 hours still fails when the step did not recover. Also flagged: a helper netcheck log whose latest line since boot does not say every expected target is ok (`N targets ok`; 6 without Sonos, 8 with it), and a `com.davidcasas.morningboard.kids-tv` job whose `ProgramArguments` does not start at `~/Applications/MorningBoardTVHelper.app/Contents/MacOS/kids-tv-helper`.

It writes `~/Library/Application Support/morning-board/kids-tv-healthcheck.json`: `result` (`pass` or `fail`), a plain-English `reason`, and `evidence`. Exit status is non-zero on failure. If that result is a failure and `~/.config/kids-tv/alert-webhook.env` exists, is mode `0600`, and sets `WEBHOOK_URL`, the short JSON is POSTed once per distinct failure. The same failure is not sent again. That record is `kids-tv-healthcheck-sent.json` beside the status file. The check does not write `kids-tv-alert-ack.json`, so it does not mark alerts read for the other check. No webhook file means the status file only. Nothing here creates the webhook file.

`launchd/com.davidcasas.morningboard.kids-tv-healthcheck.plist` runs `healthcheck` every 5 minutes (`StartInterval` 300) so a morning check runs on the first wake after it is due. `launchd/com.davidcasas.morningboard.kids-tv-healthcheck-evening.plist` runs `healthcheck --slot evening` at 20:45 every day. Neither is installed by cloning this repo. Install, as the mini account, only after a yes, and keep a backup of anything already there:

```
cd ~/code/morning-board
uid=$(id -u); la="$HOME/Library/LaunchAgents"; p=com.davidcasas.morningboard
stamp=$(date +%Y%m%d%H%M%S)
mkdir -p "$la"
for job in kids-tv-healthcheck kids-tv-healthcheck-evening; do
  dst="$la/$p.$job.plist"
  if [ -f "$dst" ]; then
    cp -p "$dst" "$dst.bak-$stamp"
  fi
  launchctl bootout "gui/$uid/$p.$job" 2>/dev/null || true
  sed -e "s|__REPO__|$HOME/code/morning-board|g" -e "s|__HOME__|$HOME|g" \
    "launchd/$p.$job.plist" > "$dst"
  launchctl bootstrap "gui/$uid" "$dst"
done
```

Uninstall the same way: boot each job out, copy the installed plist to `$dst.bak-$stamp` if it is there, then remove it.

```
cd ~/code/morning-board
uid=$(id -u); la="$HOME/Library/LaunchAgents"; p=com.davidcasas.morningboard
stamp=$(date +%Y%m%d%H%M%S)
for job in kids-tv-healthcheck kids-tv-healthcheck-evening; do
  dst="$la/$p.$job.plist"
  launchctl bootout "gui/$uid/$p.$job" 2>/dev/null || true
  if [ -f "$dst" ]; then
    cp -p "$dst" "$dst.bak-$stamp"
    rm -f "$dst"
  fi
done
```

After `sed`, the installed files are exactly:

- `~/Library/LaunchAgents/com.davidcasas.morningboard.kids-tv-healthcheck.plist`
  - `Label` = `com.davidcasas.morningboard.kids-tv-healthcheck`
  - `ProgramArguments` = `/usr/bin/python3`, `~/code/morning-board/scripts/kids-tv.py`, `healthcheck`
  - `StartInterval` = `300`
  - `StandardOutPath` = `~/Library/Logs/kids-tv-healthcheck.out`
  - `StandardErrorPath` = `~/Library/Logs/kids-tv-healthcheck.err`
- `~/Library/LaunchAgents/com.davidcasas.morningboard.kids-tv-healthcheck-evening.plist`
  - `Label` = `com.davidcasas.morningboard.kids-tv-healthcheck-evening`
  - `ProgramArguments` = `/usr/bin/python3`, `~/code/morning-board/scripts/kids-tv.py`, `healthcheck`, `--slot`, `evening`
  - `StartCalendarInterval` / `Hour` = `20`, `Minute` = `45` (no `Weekday`, so every day)
  - `StandardOutPath` = `~/Library/Logs/kids-tv-healthcheck-evening.out`
  - `StandardErrorPath` = `~/Library/Logs/kids-tv-healthcheck-evening.err`

If one of those plists was already installed, the installer also writes `….plist.bak-YYYYMMDDHHMMSS` next to it, a copy of the previous file. It does not edit the kids-tv job plist, `times.json`, or `alert-webhook.env`.

### Network check

`netcheck` opens one TCP connection, then closes it, to each port the job uses: each TV's Cast port 8009, its Cast info port 8008 and SmartCast 7345, and each Sonos room's 1400. It sends nothing on them. A port that does not answer is retried for `--settle` seconds (default 0). It logs one line with each result: ok, refused, timed out, no route (`EHOSTUNREACH`), network unreachable or host down. If every connection fails at once with no route to host, it writes one alert, tv `all`, step `netcheck`, starting `Local Network permission blocked`: macOS fails a process that lacks the Local Network permission that way at once. Otherwise each TV or room with a failing port gets its own `netcheck` alert. It exits 1 if anything failed.

`launchd/com.davidcasas.morningboard.kids-tv-helper-netcheck.plist` runs `netcheck --settle 180` at each login, so after the weekly restart, through the helper app like the kids-tv job, so it tests the same permission. It logs to `~/Library/Logs/kids-tv-helper-netcheck.log`, not the job's log. `launchd/com.davidcasas.morningboard.kids-tv-netcheck.plist` is the same check run by plain `/usr/bin/python3`; install it instead only after rolling back to plain python3. `launchd/com.davidcasas.morningboard.kids-tv-castcheck.plist` and `launchd/com.davidcasas.morningboard.kids-tv-helper-castcheck.plist` are one-off tests that cast each TV from launchd, read the app and stop it. Neither has `RunAtLoad`: load one, run it with `launchctl kickstart gui/$(id -u)/<label>`, and remove it, all inside a test window agreed beforehand.

Each TV's address and token live in an env file in `~/.config/vizio/` (`<PREFIX>IP` and `<PREFIX>TOKEN`). The repo only knows the neutral keys `boys-room` and `girls-room`, whose defaults are `boys-room.env` with prefix `VIZIO_BOYS_ROOM_` and `girls-room.env` with prefix `VIZIO_GIRLS_ROOM_`. To map a key to a different file, put `{"tvs": {"boys-room": {"env_file": "x.env", "prefix": "VIZIO_X_"}}}` in the mini-local `~/.config/kids-tv/config.json`, which is not in git and holds no tokens. They are read inside the script and never logged. The log is `~/Library/Logs/kids-tv-morning-board.log`.

### Local Network helper app

The kids-tv job runs through `MorningBoardTVHelper.app`, a small app bundle with its own identity, `com.davidcasas.morningboard.kidstv-helper`. macOS Local Network privacy then judges the app, not Python.

Why: run directly, launchd starts `/usr/bin/python3`, and macOS charges its connections to `com.apple.python3`. A launchd agent gets no Terminal exemption. That grant goes stale. `/usr/bin/python3` is a stub that runs the Python inside Xcode or the Command Line Tools, and an Xcode or CLT update replaces that binary. At each boot macOS rebuilds the list of binaries the grant covers. If the grant does not point at the real binary, the list can miss it, and after a reboot every connection fails at once with no route to host. When an app starts a helper tool, macOS counts the app as the responsible code, and it identifies the app by its own bundle id and executable ([TN3179](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy)). A Python update does not touch that.

- `helper/kids-tv-helper.c` is the app's only executable. It starts `/usr/bin/python3 SCRIPT ARGS` as a child process (`posix_spawn`, not `exec`), waits for it, passes on SIGTERM, SIGINT and SIGHUP, and exits with the child's status (128+N for signal N). A child keeps its parent's responsible process, so Python, and anything Python starts, counts as the app. SCRIPT is compiled in at build time: the app runs only the `kids-tv.py` it was built for and refuses any other first argument (exit 64). It removes every `PYTHON*` environment variable before Python starts. The three signals are blocked from just before the spawn until the child's pid is known, so one that arrives in between is passed on, not lost; the child starts with no blocked signals and their default actions.
- `helper/Info.plist` sets `LSUIElement`, `NSLocalNetworkUsageDescription` (the text in the Allow prompt), and `NSBonjourServices` with `_viziocast._tcp`, the only service the script resolves (`dns-sd -L`). The script connects to configured addresses and never browses for `_googlecast._tcp`, so that is not listed.
- `scripts/build-kids-tv-helper.sh SCRIPT [OUT_DIR]` builds `MorningBoardTVHelper.app` into `build/` (ignored by git) with clang, ad-hoc signs it (`codesign --sign - --identifier com.davidcasas.morningboard.kidstv-helper --options runtime`), verifies it, and prints its cdhash. SCRIPT is the absolute path of the installed `kids-tv.py`, for example `$HOME/code/morning-board/scripts/kids-tv.py`; it must exist. The build is reproducible: the same source, SCRIPT and toolchain give the same binary from any checkout. A different SCRIPT is a different binary. The contents of `kids-tv.py` are not part of the app; editing it changes nothing here.
- Templates: `launchd/com.davidcasas.morningboard.kids-tv.plist` (the job), `launchd/com.davidcasas.morningboard.kids-tv-helper-netcheck.plist` (the login netcheck) and `launchd/com.davidcasas.morningboard.kids-tv-helper-castcheck.plist` (test windows only) all start `__HOME__/Applications/MorningBoardTVHelper.app/Contents/MacOS/kids-tv-helper` with the script as its first argument.

Install, as the mini account, before the job (no sudo):

```
cd ~/code/morning-board
scripts/build-kids-tv-helper.sh "$HOME/code/morning-board/scripts/kids-tv.py"
mkdir -p ~/Applications
rm -rf ~/Applications/MorningBoardTVHelper.app
ditto build/MorningBoardTVHelper.app ~/Applications/MorningBoardTVHelper.app
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f ~/Applications/MorningBoardTVHelper.app
```

The first time the app connects, macOS shows "Allow “MorningBoardTVHelper” to find devices on local networks?" on the mini's screen. Someone has to click Allow there; until then every connection fails. The prompt stays up until it is answered. The login netcheck is the easy way to raise it.

Caveats:

- Right after boot, the first lookup of the app's identity can come back empty; a retry about 1.5 s later finds it. A connection in those first seconds can fail. `tick` retries on the next minute, and the netcheck's 180 s settle covers it.
- The netcheck retries a failing port for 180 s (`--settle 180`) before it alerts. A block shows up as an alert about 3 minutes after login, not at once.
- A rebuild that changes the binary (new `kids-tv-helper.c` or `Info.plist`, a different SCRIPT, or a new clang) gives the app a new UUID and cdhash. Install it with the same steps above: they replace the app and re-register it with `lsregister -f`. Then restart the mini: the identity list macOS checks is rebuilt at boot, and the restart picks up the new UUID. macOS may show the Allow prompt once more, and David has to click Allow at the mini. Until then the job may be blocked.
- `AssociatedBundleIdentifiers`, which the netcheck and castcheck templates set, is ignored for an executable without a Team ID (ad-hoc signed). It only changes how Login Items lists the agent; Local Network does not use it. The job's template does not set it.

Roll back to plain python3, as the mini account. The job's template is used with its first `ProgramArguments` entry set back to `/usr/bin/python3`; the helper netcheck is removed and the plain one installed:

```
cd ~/code/morning-board
uid=$(id -u); la=~/Library/LaunchAgents; p=com.davidcasas.morningboard
# the job
launchctl bootout gui/$uid/$p.kids-tv 2>/dev/null || true
sed -e "s|__HOME__/Applications/MorningBoardTVHelper.app/Contents/MacOS/kids-tv-helper|/usr/bin/python3|" \
    -e "s|__REPO__|$HOME/code/morning-board|g" -e "s|__HOME__|$HOME|g" \
    launchd/$p.kids-tv.plist > $la/$p.kids-tv.plist
plutil -extract ProgramArguments.0 raw $la/$p.kids-tv.plist   # prints /usr/bin/python3
launchctl bootstrap gui/$uid $la/$p.kids-tv.plist
# the netcheck
launchctl bootout gui/$uid/$p.kids-tv-helper-netcheck 2>/dev/null || true
rm -f $la/$p.kids-tv-helper-netcheck.plist
launchctl bootout gui/$uid/$p.kids-tv-netcheck 2>/dev/null || true
sed -e "s|__REPO__|$HOME/code/morning-board|g" -e "s|__HOME__|$HOME|g" \
    launchd/$p.kids-tv-netcheck.plist > $la/$p.kids-tv-netcheck.plist
launchctl bootstrap gui/$uid $la/$p.kids-tv-netcheck.plist
```

The plain netcheck runs at once and then at each login. Python then needs its own Local Network grant (Settings > Privacy & Security > Local Network, or the prompt), and that grant can go stale again as described above. The app can stay installed; nothing else runs it. To remove it as well, `rm -rf ~/Applications/MorningBoardTVHelper.app`, then turn its entry off in Settings > Privacy & Security > Local Network. To go back to the helper, run the install steps here and below again.

`scripts/test_kids_tv_helper.py` checks the templates (the job runs through the app and keeps every other key) and, on a Mac with clang, builds the app and checks its signature, the cdhash across two builds, and the launcher.

### Calendar run days

Which days the TVs come on is decided by three things together: `schedule.json` has the weekday on with an alarm and leaveBy, the run-days file says `run` is `true` for that date, and the date is not in `kids-tv-skip.json` (the skip list wins). Monday through Friday are on, so the calendar can add a Tuesday or Wednesday as well as cancel a Monday. Saturday and Sunday stay off in `schedule.json`. The run-days job writes run = kids_home AND not no_school, from the private calendar (see the run-days routine). A weekend it does not mark must not wake the kids. The repo holds no calendar ids.

The run-days file, `~/.config/kids-tv/run-days.json`, is written by a separate scheduled job on the mini, atomically, a few evenings a week, looking 14 days ahead. This script only reads it, fresh on every run. Set another path with `"run_days_path"` in `~/.config/kids-tv/config.json`, `KIDS_TV_RUN_DAYS`, or `--run-days`.

```
{"generated_at": "2026-10-28T20:15:00-07:00", "horizon_end": "2026-11-11",
 "days": {"2026-10-29": {"kids_home": true, "no_school": false, "run": true, "why": "free text"}}}
```

Only `run` decides, and only a real `true` runs the TVs. `why` is never read or logged.

Fail-safe: the TVs never come on by default. On a day the schedule has on, if the file is missing, unreadable or not valid JSON, has no entry for the date, has a `run` that is not true or false, or has a `generated_at` that is more than 5 days old (or unreadable, or more than a day in the future), nothing runs that day. One `ERROR:` line is written to the log and to the `.err` file for that day, not one per minute. A `run` of `false` is a normal "no" and is not an error. A weekday the schedule has off never reads the file.

`plan --date YYYY-MM-DD --run-days /tmp/fixture.json` shows what a file would do without touching a TV. `plan` and `tick` both read a `generated_at` without a zone as the schedule's zone (America/Los_Angeles).

### Local times

`resolved_day_times` is the only function that picks an alarm and a leaveBy. `plan`, the TV on/off tick, and the evening Sonos sync all go through it (the tick and the sync via `plan_for`). Defaults stay in `schedule.json`. A file on the mini, `~/.config/kids-tv/times.json`, can override one weekday or one date. The date wins over the weekday, and the weekday wins over the schedule. The file is not in git. `tick` does not create it. It appears only when someone runs `times set`.

```
kids-tv.py times set friday --alarm 06:30 --leave 07:40
kids-tv.py times set 2026-11-13 --alarm 07:00 --leave 08:10
kids-tv.py times show
```

Both times are `HH:MM`, leaveBy has to be after the alarm, the alarm has to be from 05:00 to 09:00, and leaveBy has to be from 06:00 to 10:00. `times set` refuses Saturday, Sunday, and a weekend date, because weekends never run. The write is atomic. A missing file means the schedule times. A file that exists but cannot be read or parsed, or that fails validation, means the morning does not start: no TV comes on, and one alert (`schedule` / `times`). A TV that already came on still powers off at the leaveBy saved at power-on, or at `schedule.json`'s leaveBy if that was not saved. The evening Sonos sync runs that check for tomorrow, writes the same alert the night before, and turns tomorrow's kids' alarms off. Set another path with `KIDS_TV_TIMES` or `--times`. When the job opens the board it puts that day's alarm and leaveBy on the page address (`?alarm=06:20&leaveBy=07:25`). The page uses those when both are present, and otherwise follows `schedule.json`.

### Kids' rooms Sonos alarms

With a `"sonos"` block in the local config, `tick` also keeps the two kids' rooms' native Sonos alarms in step with the run days. Without it, nothing Sonos runs. It uses plain UPnP SOAP on port 1400 (standard library only), and touches only the two rooms named in the local config. The job changes only the one alarm it owns in each room, by ID: `alarm_id` in the local config if set, else the ID it saved in `kids-sonos-alarms.json` beside the state file, else the backup's alarm ID if that alarm is still in the room (then saved). No other alarm is changed.

```
{"sonos": {"backup": "~/.config/kids-sonos/alarms-backup-YYYYMMDD.json",
           "rooms": {"boys-room": {"room": "<Sonos room name>", "ip": "<address>", "alarm_id": "<ID>"},
                     "girls-room": {"room": "<Sonos room name>", "ip": "<address>", "alarm_id": "<ID>"}},
           "play_check": true}}
```

- Evening: on the first tick at or after 20:30, each room's alarm is set for tomorrow. If tomorrow is a run day (schedule, run-days file and skip list, as for the TVs), the alarm is on at that day's resolved alarm time (`resolved_day_times`: schedule, then a weekday override, then a one-date override) with a recurrence of that one weekday (`ON_<d>`, 0 is Sunday). If not, it is off. Only on/off, time and recurrence change; every other field stays as it is. Only while the job owns no alarm in a room, and only when it should be on, is one created from the backup (source, volume, duration, play mode, linked zones); its ID is saved, and the intent is saved first, so a lost answer never makes a second one. If an owned alarm is gone, an alert is written and nothing is created. Nothing is written when the alarm already matches, and every write is re-read and checked. If tomorrow cannot be decided (for example the run-days file is stale), the alarm is left as it is and retried each tick until 23:00; then an alert is written. A times file that exists but cannot be trusted is not that path: one alert is written that night for tomorrow, and tomorrow's kids' alarms are turned off. A failed sync is retried each tick, up to four times, with an alert on the first failure.
- Morning: on a run day, each room is watched from 1 minute before alarm to 2 minutes after it. After the TV part, each tick reads the room's transport state every 5 s for the rest of that tick; the read count is kept in the state across ticks. Each of these reads gives up after 3 s, and a round of reads starts only if it would end, even with every room timing out, within 50 s of the tick's start. So the watch reads never run a tick past 50 s; only a TV part that alone takes longer than that can make the tick longer, and then no reads are made that tick. The one exception is the decide tick after the window (the last read, Play, then the check), which has no budget check: with a slow speaker it can add up to about 22 s per room (3 s read, 8 s Play, 3 s settle, 8 s check). Any `PLAYING` reading means the alarm fired: the room is marked OK for the day and left alone, even after a short play that stopped. A room already playing at 1 minute before alarm counts as fired too. A room never seen playing in the whole window gets one last read on the first tick after it, then is sent Play once (which resumes its current queue) and an alert is written. With `"play_check": false` in the `sonos` block (default `true`), no Play is ever sent; the alert is still written. Each room has its own lock (`kids-tv-sonos-<key>.lock`), so two commands never watch or play the same room.

The state for this is `kids-sonos-state.json` beside the TV state file.

Install as the mini account, after the helper app (see "Local Network helper app"). This is the step that makes it live. It installs the job and its login netcheck, and removes the older plain-python netcheck agent if it is there. It can be run again; each agent is booted out before it is loaded:

```
cd ~/code/morning-board
uid=$(id -u); la=~/Library/LaunchAgents; p=com.davidcasas.morningboard
launchctl bootout gui/$uid/$p.kids-tv-netcheck 2>/dev/null || true
rm -f $la/$p.kids-tv-netcheck.plist
for job in kids-tv kids-tv-helper-netcheck; do
  launchctl bootout gui/$uid/$p.$job 2>/dev/null || true
  sed -e "s|__REPO__|$HOME/code/morning-board|g" -e "s|__HOME__|$HOME|g" \
    launchd/$p.$job.plist > $la/$p.$job.plist
  launchctl bootstrap gui/$uid $la/$p.$job.plist
done
```

The castcheck templates are not installed here. They are for test windows only: load, `launchctl kickstart`, then boot out and remove.
