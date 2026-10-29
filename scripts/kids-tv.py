#!/usr/bin/env python3
"""Kids' bedroom TVs show the morning board.

On a board day, at the board's alarm time, turn on the two bedroom Vizio TVs
and cast the morning board. At on-the-road time, power each one off once.

Times come from schedule.json, read fresh on every run. Nothing is hard-coded.
is_run_day() decides whether today is a run day: schedule.json has the weekday
on, the calendar run-days file says run for that date, and the date is not in
kids-tv-skip.json. No run day means nothing runs. If the run-days file is
missing, unreadable, stale or lacks the date, nothing runs and one ERROR is
logged for the day. See README, "Calendar run days".

    kids-tv.py plan [--date YYYY-MM-DD] [--json]   print the times, touch no TV
    kids-tv.py tick                                what launchd runs every minute
    kids-tv.py on|cast|off|status [--tv NAME]      one manual action, [--dry-run]

tick sends nothing to a TV outside the window: one power-on and cast per TV
after alarm, one power-off per TV after on-the-road. Each is recorded in a
state file, so a reboot or a second tick never repeats it. It reads a TV's
power state only for the 90 s after a power-off, to check the off stuck, so a
kid who switches away is left alone. A first failed power-on, or an off that
does not stick, is written to the alert file at once (see write_alert).

With a "sonos" block in the local config, tick also keeps the two kids' rooms'
Sonos alarms in step with the next run day (each evening from 20:30) and checks
2 minutes after alarm that each room is playing. See the Sonos section below.

Which env file belongs to which TV is local to the mini, in
~/.config/kids-tv/config.json ("tvs", and optionally "run_days_path" and
"sonos"; each TV may also have an "mdns_name"); the repo only knows the
neutral keys boys-room and girls-room. Tokens come from
~/.config/vizio/<env file> and are used only inside this script. They are
never printed, logged, or put on a command line.
Python 3.9 standard library only. Casting uses the catt binary.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Dict, List, Optional
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()

BOARD_URL = "https://davidcasas82.github.io/morning-board/"
DAY_KEYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
# name -> (env file, key prefix). The files hold <PREFIX>IP and <PREFIX>TOKEN.
# These are the defaults; the mini's local config (default_config_path) can map a
# key to a different env file and prefix, so no real name is committed.
TVS = {
    "boys-room": ("boys-room.env", "VIZIO_BOYS_ROOM_"),
    "girls-room": ("girls-room.env", "VIZIO_GIRLS_ROOM_"),
}
DEFAULT_TZ = "America/Los_Angeles"
VIZIO_PORT = 7345
# SmartCast power keys: codeset 11, code 1 = power on, code 0 = power off.
POWER_CODES = {"on": 1, "off": 0}

RUN_DAYS_MAX_AGE = dt.timedelta(days=5)  # an older run-days file is stale: nothing runs
RUN_DAYS_MAX_FUTURE = dt.timedelta(days=1)  # a generated_at further ahead than this is not believed
RUN_DAYS_DEFAULT = HOME / ".config" / "kids-tv" / "run-days.json"
run_days_path_from_config: Optional[str] = None  # set from the local config by apply_local_config

MAX_ATTEMPTS = 4  # per TV, per step, per day
ON_GRACE = dt.timedelta(minutes=15)  # a power-on this long or longer after alarm is skipped
OFF_GRACE = dt.timedelta(minutes=20)  # a power-off this long or longer after on-the-road is skipped
CAST_SETTLE_SECONDS = 4
CAST_TRIES = 6
CAST_RETRY_SECONDS = 3
STOP_WAIT_SECONDS = 15  # after quitting the cast app, wait this long at most for the receiver to go idle
OFF_CHECKS = (5, 30, 60, 90)  # seconds after a power-off at which the power state is read
OFF_SENDS = 3  # power-offs per off step, at most
BACKDROP_APP_ID = "E8C28D3C"  # the Chromecast's idle (ambient) screen
ALERT_KEEP = 60  # newest alert entries kept in the alert file
ON_RESTART_WINDOW = dt.timedelta(minutes=4)  # an unreachable TV may be restarting: keep retrying the on this long
MDNS_SERVICE = "_viziocast._tcp"
MDNS_SECONDS = 4
CAST_INFO_PORT = 8008
TV_MDNS: Dict[str, str] = {}  # TV key -> advertised mDNS name, from the local config only

IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IPV6 = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}\b")


def redact_ips(text: str) -> str:
    return IPV6.sub("<ip>", IPV4.sub("<ip>", text))


def describe_error(exc: BaseException) -> str:
    """Name an error with its reason, for example "URLError(OSError): [Errno 65] No route to host".

    A URLError is named with the error it wraps. errno and strerror are shown
    when present. Callers scrub the result before logging it.
    """
    if isinstance(exc, subprocess.TimeoutExpired):
        return "TimeoutExpired: no answer in %g s" % exc.timeout
    name = exc.__class__.__name__
    if isinstance(exc, urllib.error.HTTPError):
        return "%s: HTTP %s %s" % (name, exc.code, exc.reason)
    reason = exc
    if isinstance(exc, urllib.error.URLError):
        if not isinstance(exc.reason, BaseException):
            return "%s: %s" % (name, exc.reason)
        reason = exc.reason
        name = "%s(%s)" % (name, reason.__class__.__name__)
    if isinstance(reason, OSError) and reason.errno is not None:
        detail = "[Errno %s] %s" % (reason.errno, reason.strerror or os.strerror(reason.errno))
    else:
        detail = str(reason)
    return ("%s: %s" % (name, detail[:200])) if detail else name


def is_unreachable(exc: BaseException) -> bool:
    """Refused, timed out or no route, as opposed to an answer the TV gave."""
    if isinstance(exc, urllib.error.HTTPError):
        return False
    if isinstance(exc, urllib.error.URLError):
        exc = exc.reason if isinstance(exc.reason, BaseException) else ConnectionError()
    return isinstance(exc, OSError) and not isinstance(exc, ssl.SSLError)


def output_tail(done) -> str:
    """The last line catt printed, stderr first, with any IP redacted."""
    lines = (done.stderr or done.stdout or "").strip().splitlines()
    return redact_ips(lines[-1][:200]) if lines else ""


class ScheduleError(Exception):
    pass


class TvError(Exception):
    pass


class TvUnreachable(TvError):
    """No answer at all (refused, timed out, no route): the TV may be restarting."""


class StateError(Exception):
    pass


class RunDaysError(ScheduleError):
    """The calendar run-days file cannot be trusted. Nothing runs that day."""


# ---------------------------------------------------------------- schedule


def default_schedule_path() -> Path:
    return Path(os.environ.get("KIDS_TV_SCHEDULE", ROOT / "schedule.json"))


def load_schedule(path) -> dict:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScheduleError("cannot read schedule file: " + exc.__class__.__name__)
    try:
        data = json.loads(text)
    except ValueError:
        raise ScheduleError("schedule file is not valid JSON")
    if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
        raise ScheduleError("schedule file has no days object")
    return data


def parse_hm(value, what: str) -> dt.time:
    if not isinstance(value, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        raise ScheduleError(what + " is not HH:MM")
    return dt.time(int(value[:2]), int(value[3:]))


def default_config_path() -> Path:
    return Path(os.environ.get("KIDS_TV_CONFIG", HOME / ".config" / "kids-tv" / "config.json"))


def load_local_config(path) -> dict:
    """The mini-local config: {"tvs": {"<key>": {"env_file": ..., "prefix": ...}}}.

    Missing means defaults. It holds no tokens, only which env file is which TV.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise ScheduleError("local config is unreadable or not valid JSON")
    if not isinstance(data, dict):
        raise ScheduleError("local config is not an object")
    return data


def apply_local_config(config: dict) -> None:
    global run_days_path_from_config
    path = config.get("run_days_path")
    if path is not None and (not isinstance(path, str) or not path.strip()):
        raise ScheduleError("local config run_days_path is not a path")
    run_days_path_from_config = path
    apply_sonos_config(config.get("sonos"))
    tvs = config.get("tvs", {})
    if not isinstance(tvs, dict):
        raise ScheduleError("local config tvs is not an object")
    TV_MDNS.clear()
    for key, spec in tvs.items():
        if key not in TVS:
            raise ScheduleError("local config names an unknown TV key")
        mdns_name = spec.get("mdns_name") if isinstance(spec, dict) else None
        if mdns_name is not None:
            if not isinstance(mdns_name, str) or not mdns_name.strip():
                raise ScheduleError("local config has a bad mdns_name for " + key)
            TV_MDNS[key] = mdns_name
        if (not isinstance(spec, dict) or not isinstance(spec.get("env_file"), str)
                or not isinstance(spec.get("prefix"), str)
                or not re.fullmatch(r"[A-Za-z0-9._-]+", spec["env_file"])
                or not re.fullmatch(r"[A-Z0-9_]+", spec["prefix"])):
            raise ScheduleError("local config has a bad TV entry for " + key)
        TVS[key] = (spec["env_file"], spec["prefix"])


def default_skip_path() -> Path:
    return Path(os.environ.get("KIDS_TV_SKIP", ROOT / "kids-tv-skip.json"))


def load_skip_dates(path) -> set:
    """Dates the TVs stay off, from {"skipDates": ["YYYY-MM-DD", ...]}.

    A missing file means no skips. A file that is unreadable or malformed
    raises ScheduleError: the run is logged as an error and nothing runs that
    day, because the TVs staying off is the safe direction.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    except OSError:
        raise ScheduleError("cannot read skip file")
    try:
        data = json.loads(text)
    except ValueError:
        raise ScheduleError("skip file is not valid JSON, so nothing runs")
    dates = data.get("skipDates") if isinstance(data, dict) else None
    if not isinstance(dates, list):
        raise ScheduleError("skip file has no skipDates list, so nothing runs")
    out = set()
    for item in dates:
        try:
            if not isinstance(item, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item):
                raise ValueError
            out.add(dt.date.fromisoformat(item))
        except ValueError:
            raise ScheduleError("skip file has a date that is not YYYY-MM-DD, so nothing runs")
    return out


def default_run_days_path() -> Path:
    """KIDS_TV_RUN_DAYS, else run_days_path in the local config, else the default."""
    chosen = os.environ.get("KIDS_TV_RUN_DAYS") or run_days_path_from_config or RUN_DAYS_DEFAULT
    return Path(chosen).expanduser()


def clock() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_generated_at(value, now: dt.datetime) -> dt.datetime:
    if not isinstance(value, str):
        raise RunDaysError("run-days file has no generated_at timestamp, so nothing runs")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    # Python 3.9's fromisoformat only takes 3 or 6 fractional digits.
    text = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    try:
        stamp = dt.datetime.fromisoformat(text)
    except ValueError:
        raise RunDaysError("run-days generated_at is not an ISO 8601 time, so nothing runs")
    if stamp.tzinfo is None:  # no zone given: read it in the schedule's zone
        stamp = stamp.replace(tzinfo=now.tzinfo or dt.timezone.utc)
    return stamp


def run_day_says_run(path, day: dt.date, now: dt.datetime) -> bool:
    """What the calendar run-days file says about one date.

    Returns True only for a literal true. A literal false is a normal "no" and
    is not an error. Anything that cannot be trusted raises RunDaysError, so
    the caller does nothing that day and logs one error: the file is missing or
    unreadable, is not the expected JSON, has no entry for the date, has a
    non-boolean run, or was generated more than 5 days ago (or more than a day
    in the future, or at an unreadable time). The free-text "why" is never read.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RunDaysError("run-days file is missing, so nothing runs")
    except OSError:
        raise RunDaysError("run-days file is unreadable, so nothing runs")
    try:
        data = json.loads(text)
    except ValueError:
        raise RunDaysError("run-days file is not valid JSON, so nothing runs")
    if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
        raise RunDaysError("run-days file has no days object, so nothing runs")
    age = now - parse_generated_at(data.get("generated_at"), now)
    if age > RUN_DAYS_MAX_AGE:
        raise RunDaysError("run-days file is stale (generated %d days ago), so nothing runs" % age.days)
    if age < -RUN_DAYS_MAX_FUTURE:
        raise RunDaysError("run-days generated_at is in the future, so nothing runs")
    entry = data["days"].get(day.isoformat())
    if entry is None:
        raise RunDaysError("run-days file has no entry for %s, so nothing runs" % day.isoformat())
    if not isinstance(entry, dict) or not isinstance(entry.get("run"), bool):
        raise RunDaysError("run-days entry for %s has no true/false run, so nothing runs" % day.isoformat())
    return entry["run"]


def schedule_zone(schedule: dict) -> ZoneInfo:
    name = schedule.get("timezone") or DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError, TypeError, OSError):
        raise ScheduleError("schedule timezone is not a known zone")


def is_run_day(schedule: dict, day: dt.date, skip_path=None, run_days_path=None,
               now: Optional[dt.datetime] = None) -> bool:
    """Is this a day the TVs should run? The only place that decides.

    All three must hold:
      1. schedule.json has the weekday enabled (a real true, as the board
         decides) with both alarm and leaveBy.
      2. The date is not in the skip file (kids-tv-skip.json, read fresh each
         call). A missing file means no skips; a malformed one raises
         ScheduleError, so nothing runs that day.
      3. The calendar run-days file (see run_day_says_run) says run is true for
         the date. Its errors raise RunDaysError: nothing runs, one error is logged.
    The skip list wins over a run of true. The run-days file is only read for a
    weekday the schedule has on, so a Tuesday with no board raises nothing.
    On and off times still come from schedule.json, so plan_for calls this and
    nothing else decides a run day.
    """
    skipped = day in load_skip_dates(skip_path or default_skip_path())
    entry = schedule["days"].get(DAY_KEYS[day.weekday()])
    if not (isinstance(entry, dict) and entry.get("enabled") is True
            and entry.get("alarm") and entry.get("leaveBy")):
        return False
    if skipped:
        return False
    return run_day_says_run(run_days_path or default_run_days_path(), day, now or clock())


def plan_for(schedule: dict, day: dt.date, now: Optional[dt.datetime] = None) -> Optional[dict]:
    """Return {"label", "on", "off"} for a run day, None when nothing runs.

    The times are alarm and leaveBy: on-the-road time, not downstairs time.
    """
    if not is_run_day(schedule, day, now=now):
        return None
    key = DAY_KEYS[day.weekday()]
    entry = schedule["days"].get(key)
    if not isinstance(entry, dict) or not entry.get("alarm") or not entry.get("leaveBy"):
        raise ScheduleError(key + " is a run day but has no alarm and leaveBy")
    on = parse_hm(entry["alarm"], key + " alarm")
    off = parse_hm(entry["leaveBy"], key + " leaveBy")
    if off <= on:
        raise ScheduleError(key + " leaveBy is not after alarm")
    return {"label": entry.get("label") or "", "on": on, "off": off}


def hm(t: dt.time) -> str:
    return t.strftime("%H:%M")


def render_plan(day: dt.date, plan: Optional[dict]) -> str:
    name = DAY_KEYS[day.weekday()].capitalize()
    if plan is None:
        return "%s %s: no board day, nothing runs" % (day.isoformat(), name)
    head = "%s %s" % (day.isoformat(), name)
    if plan["label"]:
        head += " (" + plan["label"] + ")"
    lines = [head]
    for tv in TVS:
        lines.append("%s: on %s, off %s" % (tv, hm(plan["on"]), hm(plan["off"])))
    return "\n".join(lines)


# ------------------------------------------------------------------- logging


class Log:
    def __init__(self, path):
        self.path = Path(path)

    def __call__(self, message: str, stamp: Optional[dt.datetime] = None) -> None:
        stamp = stamp or dt.datetime.now()
        line = "%s %s\n" % (stamp.strftime("%Y-%m-%d %H:%M:%S"), message)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            sys.stderr.write(line)


# ----------------------------------------------------------------------- TVs


def parse_env(path) -> Dict[str, str]:
    values = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        values[key] = value.strip().strip("'\"")
    return values


class OffDidNotStick(TvError):
    """The power-off went out, but the TV read on after every send, or its state stayed unknown.
    Never retried on a later tick."""


def mdns_lookup(name: str) -> Optional[str]:
    """The address of the TV advertising `name` on _viziocast._tcp, or None. Uses macOS dns-sd."""
    try:
        done = subprocess.run(["dns-sd", "-L", name, MDNS_SERVICE, "local"], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=MDNS_SECONDS)
        out = done.stdout
    except subprocess.TimeoutExpired as exc:  # dns-sd never exits by itself
        out = exc.stdout
    except OSError:
        return None
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    match = re.search(r"can be reached at (\S+?)\.?:\d+", out or "")
    if not match:
        return None
    try:
        return socket.gethostbyname(match.group(1))
    except OSError:
        return None


class Tv:
    def __init__(self, name: str, config_dir, log: Optional[Callable] = None):
        self.name = name
        self._log = log or (lambda message: None)
        self._relocated = False
        env_file, prefix = TVS[name]
        path = Path(config_dir) / env_file
        try:
            env = parse_env(path)
        except OSError:
            raise TvError("%s: cannot read %s" % (name, env_file))
        for suffix in ("IP", "TOKEN"):
            if not env.get(prefix + suffix):
                raise TvError("%s: %s missing from %s" % (name, prefix + suffix, env_file))
        self._ip = env[prefix + "IP"]
        self._token = env[prefix + "TOKEN"]

    def _scrub(self, text: str) -> str:
        return redact_ips(text.replace(self._token, "<token>").replace(self._ip, "<ip>"))

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        """One SmartCast call. If the TV does not answer at its configured address,
        look it up once by its mDNS name (TV_MDNS) and try again there."""
        try:
            return self._request_once(method, path, body)
        except TvUnreachable:
            if not self._relocate():
                raise
            return self._request_once(method, path, body)

    def _relocate(self) -> bool:
        name = TV_MDNS.get(self.name)
        if not name or self._relocated:
            return False
        self._relocated = True
        ip = mdns_lookup(name)
        if not ip or ip == self._ip:
            self._log("%s: no answer at the configured address; mDNS found %s"
                      % (self.name, "the same address" if ip else "nothing"))
            return False
        self._ip = ip
        self._log("%s: no answer at the configured address; using the address mDNS found" % self.name)
        return True

    def _request_once(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        url = "https://%s:%d%s" % (self._ip, VIZIO_PORT, path)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"AUTH": self._token, "Content-Type": "application/json"},
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # the TV's SmartCast certificate is self-signed
        try:
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                payload = json.loads(resp.read().decode() or "{}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            message = "%s: request failed: %s" % (self.name, self._scrub(describe_error(exc)))
            if is_unreachable(exc):
                raise TvUnreachable(message)
            raise TvError(message)
        if not isinstance(payload, dict):
            raise TvError("%s: TV answer is not a JSON object" % self.name)
        status = (payload.get("STATUS") or {}).get("RESULT")
        if status not in (None, "SUCCESS"):
            raise TvError("%s: TV answered %s" % (self.name, status))
        return payload

    def power_key(self, state: str) -> None:
        self._request("PUT", "/key_command/", {
            "KEYLIST": [{"CODESET": 11, "CODE": POWER_CODES[state], "ACTION": "KEYPRESS"}],
        })

    def power_mode(self) -> str:
        """"on", "off", or "unknown" for an answer without a 1 or 0 VALUE. Raises TvError."""
        payload = self._request("GET", "/state/device/power_mode")
        items = payload.get("ITEMS")
        item = items[0] if isinstance(items, list) and items else None
        value = item.get("VALUE") if isinstance(item, dict) else None
        return {1: "on", 0: "off"}.get(value, "unknown") if isinstance(value, int) else "unknown"

    def cast_uptime(self) -> str:
        """The Chromecast's uptime, from port 8008 /setup/eureka_info (only that field). Never raises."""
        url = "http://%s:%d/setup/eureka_info" % (self._ip, CAST_INFO_PORT)
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                uptime = json.loads(resp.read().decode() or "{}").get("uptime")
        except (urllib.error.URLError, OSError, ValueError, AttributeError) as exc:
            return "unreadable (%s)" % self._scrub(describe_error(exc))
        if isinstance(uptime, bool) or not isinstance(uptime, (int, float)):
            return "unreadable (no uptime field)"
        return "%d s" % uptime

    def _catt(self, catt: str, *args: str):
        return subprocess.run([catt, "-d", self._ip] + list(args), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=30)

    def cast_app(self, catt: str) -> Optional[str]:
        """The receiver app running on the TV's Chromecast, or None when it is idle."""
        try:
            done = self._catt(catt, "info", "-j")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TvError("%s: cast status failed: %s" % (self.name, self._scrub(describe_error(exc))))
        try:
            info = json.loads(done.stdout) if done.returncode == 0 else None
        except ValueError:
            info = None
        if not isinstance(info, dict):
            raise TvError("%s: cast status failed: exit %d %s" % (self.name, done.returncode,
                                                                   self._scrub(output_tail(done))))
        app = info.get("app_id")
        return None if not app or app == BACKDROP_APP_ID else str(app)

    def stop_cast(self, catt: str, log: Callable) -> bool:
        """Quit whatever is casting and wait until the receiver is idle, STOP_WAIT_SECONDS at most.

        A TV powered off while the board is still casting can wake itself back up,
        so the off step stops the cast first. Returns True when the receiver reads idle.
        """
        try:
            if self.cast_app(catt) is None:
                return True
        except TvError as exc:
            log("%s, stopping anyway" % exc)
        try:
            done = self._catt(catt, "stop")
            if done.returncode != 0:
                log("%s: cast stop exited %d %s" % (self.name, done.returncode, self._scrub(output_tail(done))))
        except (OSError, subprocess.TimeoutExpired) as exc:
            log("%s: cast stop failed: %s" % (self.name, self._scrub(describe_error(exc))))
        deadline = time.monotonic() + STOP_WAIT_SECONDS
        last = ""
        while True:
            try:
                if self.cast_app(catt) is None:
                    log("%s: cast stopped, receiver idle" % self.name)
                    return True
            except TvError as exc:
                last = " (last read: %s)" % exc
            if time.monotonic() >= deadline:
                log("%s: receiver still not idle after %d s, powering off anyway%s"
                    % (self.name, STOP_WAIT_SECONDS, last))
                return False
            time.sleep(1)

    def power_off_verified(self, catt: str, log: Callable) -> None:
        """Stop the cast, power off, and make sure it stays off.

        After each power-off the power state is read at OFF_CHECKS seconds. If it
        reads on, the cast is stopped again and another power-off is sent, up to
        OFF_SENDS in all. Only a real "on" reading re-sends. An unreachable or
        unrecognised reading is "unknown": it is not counted as off, and it never
        re-sends (a TV that restarts itself from standby is briefly unreachable).
        Not read off at 90 s (still on, or unknown): an ERROR is logged and
        OffDidNotStick is raised, which the caller never retries. This is the only
        time the job reads a TV's state after it turned it on.
        """
        mode = "unknown"
        for send in range(1, OFF_SENDS + 1):
            self.stop_cast(catt, log)
            self.power_key("off")
            sent = time.monotonic()
            readings = []
            for after in OFF_CHECKS:
                time.sleep(max(0.0, sent + after - time.monotonic()))
                try:
                    mode = self.power_mode()
                except TvError:
                    mode = "unknown"
                readings.append("%ds %s" % (after, mode))
                if mode == "on":
                    break
            log("%s: power off %d of %d, then %s" % (self.name, send, OFF_SENDS, ", ".join(readings)))
            if mode != "on":
                break
        if mode == "off":
            return
        if mode == "on":
            message = "%s: still on after %d power-offs" % (self.name, OFF_SENDS)
        else:
            message = "%s: not confirmed off, power state unknown %d s after the off (not re-sent)" % (
                self.name, OFF_CHECKS[-1])
        log("ERROR: " + message)
        raise OffDidNotStick(message)

    def cast(self, catt: str) -> None:
        cmd = [catt, "-d", self._ip, "cast_site", BOARD_URL]
        time.sleep(CAST_SETTLE_SECONDS)
        last = ""
        for attempt in range(CAST_TRIES):
            try:
                done = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                                      text=True, timeout=60)
            except (OSError, subprocess.TimeoutExpired) as exc:
                last = self._scrub(describe_error(exc))
            else:
                if done.returncode == 0:
                    return
                last = "exit %d %s" % (done.returncode, self._scrub(output_tail(done)))
            if attempt + 1 < CAST_TRIES:
                time.sleep(CAST_RETRY_SECONDS)
        raise TvError("%s: cast failed (%s)" % (self.name, last.strip()))


# --------------------------------------------------------------- tick logic


def empty_state(day: dt.date) -> dict:
    return {"date": day.isoformat(), "tvs": {}}


def acted_state(day: dt.date) -> dict:
    """A state that says every step was already done, so nothing is sent today."""
    return {"date": day.isoformat(), "note": "state file was unreadable; nothing sent",
            "tvs": {n: {"power_on_done": "unknown", "cast_done": "unknown", "off_done": "unknown"}
                    for n in TVS}}


def load_state(path, day: dt.date):
    """Return (state, damaged). Today's state. Missing or from another day: empty. Unreadable or corrupt:
    treated as already acted, so a damaged file can never cause a repeat send."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_state(day), False
    except OSError:
        text = None
    state = None
    if text is not None:
        try:
            state = json.loads(text)
        except ValueError:
            state = None
    if isinstance(state, dict) and isinstance(state.get("tvs"), dict) and isinstance(state.get("date"), str):
        return (state if state["date"] == day.isoformat() else empty_state(day)), False
    return acted_state(day), True


def save_state(path, state: dict) -> None:
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise StateError("cannot write state file (%s)" % exc.__class__.__name__)


def decide(on_at: dt.datetime, off_at: dt.datetime, now: dt.datetime, st: dict) -> Optional[str]:
    """The one step a TV is due for right now: "on", "cast", "off" or None."""
    if st.get("off_done") or st.get("off_tries", 0) >= MAX_ATTEMPTS:
        return None
    if st.get("on_skipped"):
        return None
    if now >= off_at:
        if st.get("power_on_done") and now < off_at + OFF_GRACE:
            return "off"
        return None
    if now < on_at:
        return None
    if not st.get("power_on_done"):
        if now >= on_at + ON_GRACE:
            return "skip_on"
        return "on" if st.get("on_tries", 0) < MAX_ATTEMPTS else None
    if not st.get("cast_done") and st.get("cast_tries", 0) < MAX_ATTEMPTS:
        return "cast"
    return None


DONE_KEY = {"on": "power_on_done", "cast": "cast_done", "off": "off_done"}
SENT_TEXT = {"on": "power on sent", "cast": "cast sent", "off": "power off sent"}


def default_alert_path() -> Path:
    return Path(os.environ.get("KIDS_TV_ALERT", HOME / "Library" / "Application Support"
                               / "morning-board" / "kids-tv-alert.json"))


def write_alert(path, day: dt.date, tv: str, step: str, error: str, now: dt.datetime, log: Callable) -> None:
    """Record a failure someone should hear about now: an ALERT line in the log, and an entry in the alert file.

    The file is {"alerts": [{"date", "tv", "step", "error", "at"}, ...]}, newest
    last, the newest ALERT_KEEP kept, replaced atomically. An entry is written
    once per tv, step and date. With path None only the log line is written.
    A file that cannot be read starts over; one that cannot be written is
    noted in the log line. Nothing here raises.
    """
    entry = {"date": day.isoformat(), "tv": tv, "step": step, "error": error, "at": now.isoformat()}
    if path is None:
        log("ALERT: %s: %s: %s" % (tv, step, error))
        return
    path = Path(path)
    alerts: List[dict] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("alerts"), list):
            alerts = [a for a in data["alerts"] if isinstance(a, dict)]
    except (OSError, ValueError):
        pass
    if any((a.get("date"), a.get("tv"), a.get("step")) == (entry["date"], tv, step) for a in alerts):
        return
    note = ""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"alerts": (alerts + [entry])[-ALERT_KEEP:]}, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        note = " (alert file not written: %s)" % exc.__class__.__name__
    log("ALERT: %s: %s: %s%s" % (tv, step, error, note))


def attempt(name: str, step: str, st: dict, now: dt.datetime, state_path, state: dict,
            actions: Dict[str, Callable], log: Callable, alert_path=None) -> bool:
    """Run one step, writing the intent to the state file BEFORE acting.

    If the state cannot be written, StateError is raised and nothing is sent. The
    step is marked done first and unmarked only when the TV reports a failure,
    so a crash or a failed write afterwards can never repeat a send.
    The first failed power-on of the day, and an off that did not stick, are
    also written as an alert (write_alert). Returns True when the step went out.
    """
    st[DONE_KEY[step]] = now.isoformat()
    st[step + "_tries"] = st.get(step + "_tries", 0) + 1
    save_state(state_path, state)
    try:
        actions[step](name)
    except TvError as exc:
        if isinstance(exc, OffDidNotStick):
            # the power-offs went out and the ERROR is logged; a later tick must not repeat them
            write_alert(alert_path, now.date(), name, "off", str(exc), now, log)
            return False
        st.pop(DONE_KEY[step], None)
        restarting = False
        if step == "on" and isinstance(exc, TvUnreachable):
            since = st.setdefault("on_unreachable_since", now.isoformat())
            restarting = now - dt.datetime.fromisoformat(since) < ON_RESTART_WINDOW
        if restarting:
            st["on_tries"] -= 1  # a TV that may be restarting does not use up a try, for ON_RESTART_WINDOW
            log("%s: on failed, TV not answering, maybe restarting; retrying for up to %d min: %s"
                % (name, ON_RESTART_WINDOW.seconds // 60, exc))
        else:
            log("%s: %s failed, try %d of %d: %s" % (name, step, st[step + "_tries"], MAX_ATTEMPTS, exc))
            if step == "on" and not st.get("on_alerted"):
                st["on_alerted"] = now.isoformat()
                write_alert(alert_path, now.date(), name, "on", str(exc), now, log)
        try:
            save_state(state_path, state)
        except StateError:
            st[DONE_KEY[step]] = now.isoformat()  # unknown on disk: stay on the safe side
            raise
        return False
    log("%s: %s" % (name, SENT_TEXT[step]))
    return True


def tick(schedule: dict, now: dt.datetime, state_path, actions: Dict[str, Callable],
         log: Callable, dry_run: bool = False, error_marker=None, alert_path=None) -> int:
    """Do whatever is due. Returns the number of failed steps.

    With error_marker (a path), the state-file errors are reported once per day
    instead of on every minute's tick; see report_error_once. alert_path is
    where write_alert puts alerts (None: log only).
    """
    day = now.date()

    def state_error(message: str) -> None:
        if error_marker is None:
            log("ERROR: " + message)
        else:
            report_error_once(message, day, error_marker, log)

    plan = plan_for(schedule, day, now)
    if plan is None:
        return 0
    on_at = dt.datetime.combine(day, plan["on"], tzinfo=now.tzinfo)
    off_at = dt.datetime.combine(day, plan["off"], tzinfo=now.tzinfo)
    state, corrupt = load_state(state_path, day)
    failures = 0
    try:
        if corrupt and not dry_run:
            save_state(state_path, state)  # so today is marked acted, and tomorrow recovers
            state_error("state file is unreadable, so nothing is sent today")
        for name in TVS:
            st = state["tvs"].setdefault(name, {})
            step = decide(on_at, off_at, now, st)
            if step is None:
                continue
            if step == "skip_on":
                if dry_run:
                    print("%s: would skip power-on, %d minutes or more past alarm" % (name, ON_GRACE.seconds // 60))
                    continue
                st["on_skipped"] = now.isoformat()
                save_state(state_path, state)
                log("%s: power-on skipped, %d minutes or more past alarm" % (name, ON_GRACE.seconds // 60))
                continue
            if dry_run:
                print("%s: would %s" % (name, step))
                continue
            if not attempt(name, step, st, now, state_path, state, actions, log, alert_path):
                failures += 1
            elif step == "on":
                # Power-on went out, so cast now in the same run; a miss retries next tick.
                if not attempt(name, "cast", st, now, state_path, state, actions, log, alert_path):
                    failures += 1
    except StateError as exc:
        state_error("%s, so nothing more is sent today" % exc)
        return failures + 1
    return failures


def error_marker_path(args) -> Path:
    """Beside the log, not the state file: it must work when the state directory is the problem."""
    return Path(args.log).with_name("kids-tv-error-day.json")


def report_error_once(message: str, day: dt.date, marker_path, log: Callable) -> None:
    """Log an error and write it to stderr (launchd's .err file), once per day.

    tick runs every minute, so the same problem is reported only on the first
    run of the day. The marker is a small file beside the state file. If it
    cannot be read or written, the error is reported anyway: a noisy error is
    better than a silent one.
    """
    marker = Path(marker_path)
    seen: List[str] = []
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("date") == day.isoformat() and isinstance(data.get("seen"), list):
            seen = [m for m in data["seen"] if isinstance(m, str)]
    except (OSError, ValueError):
        pass
    if message in seen:
        return
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker.with_suffix(".tmp")
        tmp.write_text(json.dumps({"date": day.isoformat(), "seen": seen + [message]}), encoding="utf-8")
        os.replace(tmp, marker)
    except OSError:
        pass
    log("ERROR: %s" % message)
    sys.stderr.write("ERROR: %s\n" % message)


# --------------------------------------------------------------------- Sonos
#
# With a "sonos" block in the local config, tick also keeps the kids' rooms'
# native Sonos alarms in step with the run days, over plain UPnP SOAP on port
# 1400 (standard library only; /usr/bin/python3 has no soco):
#
#   {"sonos": {"backup": "~/.config/kids-sonos/alarms-backup-YYYYMMDD.json",
#              "rooms": {"boys-room": {"room": "<Sonos room name>", "ip": "<address>"},
#                        "girls-room": {...}}}}
#
# Room names and addresses live only there. A room's alarm is found by the
# room's UUID (from the backup), so no other room's alarm is ever read for a
# write or changed. Without the block, nothing here runs.

SONOS_PORT = 1400
SONOS_TIMEOUT = 8
SONOS_SYNC_AT = dt.time(20, 30)  # the evening sync runs on the first tick at or after this
SONOS_CHECK_AFTER = dt.timedelta(minutes=2)  # after alarm, check each room is playing
SONOS_PLAY_SETTLE_SECONDS = 3
ALARM_CLOCK = ("/AlarmClock/Control", "urn:schemas-upnp-org:service:AlarmClock:1")
AV_TRANSPORT = ("/MediaRenderer/AVTransport/Control", "urn:schemas-upnp-org:service:AVTransport:1")
ALARM_FIELDS = ("StartLocalTime", "Duration", "Recurrence", "Enabled", "RoomUUID", "ProgramURI",
                "ProgramMetaData", "PlayMode", "Volume", "IncludeLinkedZones")
sonos_config: Optional[dict] = None  # set by apply_local_config


class SonosError(Exception):
    pass


def apply_sonos_config(block) -> None:
    global sonos_config
    if block is None:
        sonos_config = None
        return
    if (not isinstance(block, dict) or not isinstance(block.get("backup"), str)
            or not isinstance(block.get("rooms"), dict)):
        raise ScheduleError("local config sonos needs a backup path and rooms")
    rooms = {}
    for key, spec in block["rooms"].items():
        if key not in TVS:
            raise ScheduleError("local config sonos names an unknown room key")
        if not isinstance(spec, dict) or not all(isinstance(spec.get(f), str) and spec[f] for f in ("room", "ip")):
            raise ScheduleError("local config has a bad sonos entry for " + key)
        rooms[key] = {"room": spec["room"], "ip": spec["ip"]}
    sonos_config = {"backup": block["backup"], "rooms": rooms}


def sonos_day(day: dt.date) -> str:
    """Sonos recurrence digits count 0 = Sunday to 6 = Saturday."""
    return str((day.weekday() + 1) % 7)


class Speaker:
    """One Sonos player, by address. Errors are SonosError, with any address redacted."""

    def __init__(self, key: str, ip: str):
        self.key = key
        self._ip = ip

    def call(self, service, action: str, args=()) -> Dict[str, str]:
        path, urn = service
        inner = "".join("<%s>%s</%s>" % (k, escape(str(v)), k) for k, v in args)
        envelope = ('<?xml version="1.0" encoding="utf-8"?>'
                    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                    '<u:%s xmlns:u="%s">%s</u:%s></s:Body></s:Envelope>' % (action, urn, inner, action))
        req = urllib.request.Request(
            "http://%s:%d%s" % (self._ip, SONOS_PORT, path), data=envelope.encode("utf-8"), method="POST",
            headers={"Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": '"%s#%s"' % (urn, action)})
        try:
            with urllib.request.urlopen(req, timeout=SONOS_TIMEOUT) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            try:
                found = re.search(rb"<errorCode>(\d+)</errorCode>", exc.read() or b"")
            except (OSError, AttributeError):
                found = None
            raise SonosError("%s: %s failed: HTTP %s%s" % (self.key, action, exc.code,
                                                           " UPnP error " + found.group(1).decode() if found else ""))
        except (urllib.error.URLError, OSError) as exc:
            raise SonosError("%s: %s failed: %s" % (self.key, action,
                                                     redact_ips(describe_error(exc).replace(self._ip, "<ip>"))))
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            raise SonosError("%s: %s answer is not XML" % (self.key, action))
        for el in root.iter():
            if el.tag.endswith(action + "Response"):
                return {child.tag.split("}")[-1]: child.text or "" for child in el}
        raise SonosError("%s: %s answer has no response" % (self.key, action))

    def list_alarms(self) -> List[dict]:
        text = self.call(ALARM_CLOCK, "ListAlarms").get("CurrentAlarmList") or "<Alarms/>"
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            raise SonosError("%s: alarm list is not XML" % self.key)
        alarms = []
        for el in root.iter("Alarm"):
            alarm = dict(el.attrib)
            if "StartTime" in alarm:  # older firmware lists StartTime; Create and Update say StartLocalTime
                alarm["StartLocalTime"] = alarm.pop("StartTime")
            alarms.append(alarm)
        return alarms

    def create_alarm(self, fields: dict) -> str:
        return self.call(ALARM_CLOCK, "CreateAlarm", [(f, fields[f]) for f in ALARM_FIELDS]).get("AssignedID", "")

    def update_alarm(self, alarm: dict) -> None:
        self.call(ALARM_CLOCK, "UpdateAlarm", [("ID", alarm["ID"])] + [(f, alarm.get(f, "")) for f in ALARM_FIELDS])

    def transport_state(self) -> str:
        return self.call(AV_TRANSPORT, "GetTransportInfo", [("InstanceID", 0)]).get("CurrentTransportState", "")

    def play(self) -> None:
        self.call(AV_TRANSPORT, "Play", [("InstanceID", 0), ("Speed", 1)])


def load_alarm_templates(path) -> Dict[str, dict]:
    """Sonos room name -> that room's alarm in the backup, as CreateAlarm fields plus its old ID."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        out = {}
        for a in data["alarms"]:
            out[a["room"]] = {
                "ID": str(a["alarm_id"]), "StartLocalTime": a["start_time"], "Duration": a["duration"],
                "Recurrence": a["recurrence"], "Enabled": "1" if a["enabled"] else "0",
                "RoomUUID": a["zone_uid"], "ProgramURI": a["program_uri"],
                "ProgramMetaData": a["program_metadata"], "PlayMode": a["play_mode"],
                "Volume": str(a["volume"]), "IncludeLinkedZones": "1" if a["include_linked_zones"] else "0"}
        return out
    except (OSError, ValueError, KeyError, TypeError):
        raise SonosError("sonos alarm backup is unreadable or missing a field")


def find_room_alarm(alarms: List[dict], template: dict) -> Optional[dict]:
    """This room's alarm: same room UUID, and the backup's ID, else the same source (lowest ID)."""
    mine = [a for a in alarms if a.get("RoomUUID") == template["RoomUUID"]]
    for alarm in mine:
        if alarm.get("ID") == template["ID"]:
            return alarm
    same = [a for a in mine if a.get("ProgramURI") == template["ProgramURI"]]
    same.sort(key=lambda a: int(a["ID"]) if str(a.get("ID", "")).isdigit() else 0)
    return same[0] if same else None


def alarm_text(want: dict) -> str:
    if want["Enabled"] == "0":
        return "off"
    return "on %s %s" % (want["StartLocalTime"][:5], want["Recurrence"])


def sync_room_alarm(sp: Speaker, template: dict, alarm_time: Optional[dt.time], day: dt.date) -> str:
    """Make this room's alarm right for `day`: on at alarm_time for that weekday only, or off (None).

    Only Enabled, StartLocalTime and Recurrence change; every other field of an
    existing alarm stays as it is. A missing alarm is created from the backup
    (only when it should be on). Nothing is written when it already matches.
    Every write is re-read and checked. Returns what happened; raises SonosError.
    """
    if alarm_time is None:
        want = {"Enabled": "0"}
    else:
        want = {"Enabled": "1", "StartLocalTime": alarm_time.strftime("%H:%M:00"), "Recurrence": "ON_" + sonos_day(day)}
    current = find_room_alarm(sp.list_alarms(), template)
    if current is None and alarm_time is None:
        return "no alarm, so nothing to turn off"
    if current is not None and all(current.get(k) == v for k, v in want.items()):
        return "already " + alarm_text(dict(current, **want))
    if current is None:
        fields = {f: template[f] for f in ALARM_FIELDS}
        fields.update(want)
        target_id, verb = sp.create_alarm(fields), "created from the backup"
    else:
        fields = {f: current.get(f, "") for f in ALARM_FIELDS}
        fields.update(want)
        target_id, verb = current["ID"], "updated"
        sp.update_alarm(dict(fields, ID=target_id))
    after = find_room_alarm(sp.list_alarms(), dict(template, ID=target_id))
    if after is None or after.get("ID") != target_id or any(after.get(k) != v for k, v in want.items()):
        raise SonosError("%s: alarm %s, but the re-read does not match" % (sp.key, verb))
    return "%s, re-read: %s" % (verb, alarm_text(after))


def sonos_state_path(args) -> Path:
    return Path(args.state).with_name("kids-sonos-state.json")


def load_sonos_state(path, day: dt.date):
    """Return (state, damaged), as load_state does: damaged means treated as all done today."""
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = None
    except (OSError, ValueError):
        state = False
    if state is None or (isinstance(state, dict) and state.get("date") != day.isoformat()):
        return {"date": day.isoformat(), "sync": {}, "check": {}}, False
    if (isinstance(state, dict) and isinstance(state.get("sync"), dict)
            and isinstance(state.get("check"), dict)):
        return state, False
    rooms = list(TVS)
    return {"date": day.isoformat(), "note": "state file was unreadable; nothing done",
            "sync": {k: {"done": "unknown"} for k in rooms}, "check": {k: "unknown" for k in rooms}}, True


def check_room_playing(sp: Speaker, now: dt.datetime, alert_path, log: Callable) -> int:
    """After alarm on a run day: is the room playing? If not, Play, and alert either way."""
    try:
        mode = sp.transport_state()
        if mode == "PLAYING":
            log("%s: sonos playing after alarm" % sp.key)
            return 0
        sp.play()
        time.sleep(SONOS_PLAY_SETTLE_SECONDS)
        after = sp.transport_state()
        error = "room was %s %d min after alarm; Play sent, now %s" % (
            mode or "unknown", SONOS_CHECK_AFTER.seconds // 60, after or "unknown")
        failed = 0 if after == "PLAYING" else 1
    except SonosError as exc:
        error, failed = str(exc), 1
    write_alert(alert_path, now.date(), sp.key, "sonos", error, now, log)
    return failed


def sonos_tick(schedule: dict, now: dt.datetime, state_path, alert_path, log: Callable,
               dry_run: bool = False, report: Optional[Callable] = None,
               speaker: Optional[Callable] = None) -> int:
    """The Sonos part of a tick. Returns the number of failed steps; never raises.

    Morning: on a run day, from 2 minutes after alarm until ON_GRACE after
    alarm, check each room once (check_room_playing).
    Evening: from SONOS_SYNC_AT, once per room per day, set each room's alarm
    for tomorrow (sync_room_alarm): on at tomorrow's alarm if tomorrow is a run
    day (is_run_day: schedule, run-days file and skip list), off if not. If
    tomorrow cannot be decided (for example the run-days file is stale), the
    alarm is left as it is and an alert is written. A failed sync is retried
    on later ticks, up to MAX_ATTEMPTS, with an alert on the first failure.
    """
    if not sonos_config:
        return 0
    rooms = sonos_config["rooms"]
    report = report or (lambda message: log("ERROR: " + message))
    speaker = speaker or (lambda key: Speaker(key, rooms[key]["ip"]))
    day = now.date()
    state, damaged = load_sonos_state(state_path, day)
    failures = 0
    try:
        if damaged:
            if not dry_run:
                save_state(state_path, state)
                report("sonos state file is unreadable, so no Sonos step runs today")
            return 0 if dry_run else 1
        try:
            plan = plan_for(schedule, day, now)
        except ScheduleError:
            plan = None  # the TV part reports today's schedule errors
        if plan:
            alarm_at = dt.datetime.combine(day, plan["on"], tzinfo=now.tzinfo)
            if alarm_at + SONOS_CHECK_AFTER <= now < alarm_at + ON_GRACE:
                for key in rooms:
                    if state["check"].get(key):
                        continue
                    if dry_run:
                        print("%s: would check the Sonos room is playing" % key)
                        continue
                    state["check"][key] = now.isoformat()
                    save_state(state_path, state)
                    failures += check_room_playing(speaker(key), now, alert_path, log)
        if now.time() >= SONOS_SYNC_AT:
            failures += sonos_evening_sync(schedule, now, state, state_path, alert_path, log, dry_run, speaker)
    except StateError as exc:
        report("sonos: %s, so no Sonos step runs" % exc)
        return failures + 1
    return failures


def sonos_evening_sync(schedule, now, state, state_path, alert_path, log, dry_run, speaker) -> int:
    rooms = sonos_config["rooms"]
    pending = [k for k in rooms if not state["sync"].get(k, {}).get("done")
               and state["sync"].get(k, {}).get("tries", 0) < MAX_ATTEMPTS]
    if not pending:
        return 0
    day = now.date()
    tomorrow = day + dt.timedelta(days=1)
    try:
        plan = plan_for(schedule, tomorrow, now)
    except ScheduleError as exc:
        for key in pending:
            if dry_run:
                print("%s: would leave the Sonos alarm unchanged: %s" % (key, exc))
                continue
            state["sync"][key] = {"done": now.isoformat(), "result": "left unchanged"}
            save_state(state_path, state)
            write_alert(alert_path, day, key, "sonos-sync",
                        "cannot tell whether %s runs (%s); alarm left unchanged" % (tomorrow.isoformat(), exc), now, log)
        return len(pending) if not dry_run else 0
    alarm_time = plan["on"] if plan else None
    failures = 0
    for key in pending:
        if dry_run:
            print("%s: would set the Sonos alarm for %s: %s" % (key, tomorrow.isoformat(),
                                                               "on " + hm(alarm_time) if alarm_time else "off"))
            continue
        st = state["sync"].setdefault(key, {})
        st["tries"] = st.get("tries", 0) + 1
        save_state(state_path, state)
        try:
            template = load_alarm_templates(sonos_config["backup"]).get(rooms[key]["room"])
            if template is None:
                raise SonosError("%s: the sonos alarm backup has no alarm for this room" % key)
            result = sync_room_alarm(speaker(key), template, alarm_time, tomorrow)
        except SonosError as exc:
            failures += 1
            log("%s: sonos alarm for %s failed, try %d of %d: %s" % (key, tomorrow.isoformat(), st["tries"],
                                                                    MAX_ATTEMPTS, exc))
            if st["tries"] == 1:
                write_alert(alert_path, day, key, "sonos-sync", str(exc), now, log)
            save_state(state_path, state)
            continue
        st["done"] = now.isoformat()
        st["result"] = result
        save_state(state_path, state)
        log("%s: sonos alarm for %s: %s" % (key, tomorrow.isoformat(), result))
    return failures


# ----------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Kids' bedroom TVs show the morning board.")
    p.add_argument("--schedule", default=None, help="schedule.json (default: this repo's)")
    p.add_argument("--run-days", default=None, help="calendar run-days file (default: KIDS_TV_RUN_DAYS, the local config's run_days_path, or ~/.config/kids-tv/run-days.json)")
    p.add_argument("--config", default=None, help="local config (default: ~/.config/kids-tv/config.json)")
    p.add_argument("--config-dir", default=str(HOME / ".config" / "vizio"))
    p.add_argument("--state", default=str(HOME / "Library" / "Application Support" / "morning-board" / "kids-tv-state.json"))
    p.add_argument("--log", default=str(HOME / "Library" / "Logs" / "kids-tv-morning-board.log"))
    p.add_argument("--alert", default=None, help="alert file (default: KIDS_TV_ALERT, or "
                   "~/Library/Application Support/morning-board/kids-tv-alert.json)")
    p.add_argument("--catt", default=os.environ.get("KIDS_TV_CATT", str(HOME / "Tools" / "catt-venv" / "bin" / "catt")))
    sub = p.add_subparsers(dest="cmd", required=True)
    plan = sub.add_parser("plan", help="print on and off times for a date; touches no TV")
    plan.add_argument("--date", help="YYYY-MM-DD (default: today)")
    plan.add_argument("--json", action="store_true")
    t = sub.add_parser("tick", help="do what is due now (launchd runs this)")
    t.add_argument("--now", help="ISO local time, for dry runs")
    t.add_argument("--dry-run", action="store_true", help="print the due steps, send nothing, save nothing")
    for cmd, text in (("on", "power on, then cast"), ("cast", "cast the board only"),
                      ("off", "power off"), ("status", "read the power state")):
        a = sub.add_parser(cmd, help=text + " (manual; does not touch the daily state)")
        a.add_argument("--tv", choices=list(TVS) + ["all"], default="all")
        a.add_argument("--dry-run", action="store_true", help="print the steps, send nothing")
    return p


def make_actions(args, log: Callable) -> Dict[str, Callable]:
    cache: Dict[str, Tv] = {}

    def tv(name: str) -> Tv:
        if name not in cache:
            cache[name] = Tv(name, args.config_dir, log)
        return cache[name]

    def on(name: str) -> None:
        tv(name).power_key("on")
        log("%s: cast uptime at on: %s" % (name, tv(name).cast_uptime()))

    def off(name: str) -> None:
        log("%s: cast uptime at off: %s" % (name, tv(name).cast_uptime()))
        tv(name).power_off_verified(args.catt, log)

    return {
        "on": on,
        "off": off,
        "cast": lambda n: tv(n).cast(args.catt),
        "status": lambda n: print("%s: %s" % (n, tv(n).power_mode())),
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    log = Log(args.log)
    if args.run_days:
        os.environ["KIDS_TV_RUN_DAYS"] = args.run_days
    schedule_path = args.schedule or default_schedule_path()
    error_day = dt.date.today()
    try:
        apply_local_config(load_local_config(args.config or default_config_path()))
        if args.cmd == "plan":
            schedule = load_schedule(schedule_path)
            # now carries the schedule's zone (America/Los_Angeles), as in tick, so a
            # generated_at without a zone is read as LA time by both.
            now = dt.datetime.now(schedule_zone(schedule))
            day = dt.date.fromisoformat(args.date) if args.date else now.date()
            plan = plan_for(schedule, day, now)
            if args.json:
                out = None if plan is None else {"date": day.isoformat(), "label": plan["label"],
                                                  "tvs": {n: {"on": hm(plan["on"]), "off": hm(plan["off"])} for n in TVS}}
                print(json.dumps(out))
            else:
                print(render_plan(day, plan))
            return 0
        if args.cmd == "tick":
            schedule = load_schedule(schedule_path)
            zone = schedule_zone(schedule)
            now = dt.datetime.fromisoformat(args.now).replace(tzinfo=zone) if args.now else dt.datetime.now(zone)
            error_day = now.date()
            alert_path = args.alert or default_alert_path()
            tv_error = None
            try:
                failed = tick(schedule, now, args.state, make_actions(args, log), log, dry_run=args.dry_run,
                              error_marker=error_marker_path(args), alert_path=alert_path)
            except (ScheduleError, TvError, ValueError) as exc:
                tv_error, failed = exc, 1
            # The Sonos part runs even when today's TV part could not (it may be about tomorrow).
            failed += sonos_tick(schedule, now, sonos_state_path(args), alert_path, log, dry_run=args.dry_run,
                                 report=lambda m: report_error_once(m, error_day, error_marker_path(args), log))
            if tv_error is not None:
                raise tv_error
            return 1 if failed else 0
        actions = make_actions(args, log)
        names = list(TVS) if args.tv == "all" else [args.tv]
        stuck = []
        for name in names:
            steps = ["on", "cast"] if args.cmd == "on" else [args.cmd]
            for step in steps:
                if args.dry_run:
                    print("%s: would %s%s" % (name, step, " " + BOARD_URL if step == "cast" else ""))
                    continue
                try:
                    actions[step](name)
                except OffDidNotStick:
                    stuck.append(name)  # power_off_verified already logged the ERROR; go on to the next TV
                    sys.stderr.write("ERROR: %s: off did not stick\n" % name)
                    continue
                if step != "status":
                    log("%s: manual %s sent" % (name, step))
                    print("%s: %s sent" % (name, step))
        return 1 if stuck else 0
    except (ScheduleError, TvError, ValueError) as exc:
        if args.cmd == "tick":
            report_error_once(str(exc), error_day, error_marker_path(args), log)
        else:
            log("ERROR: %s" % exc)
            sys.stderr.write("ERROR: %s\n" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
