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
state file, so a reboot or a second tick never repeats it. It never checks
what the TV is doing, so a kid who switches away is left alone.

Which env file belongs to which TV is local to the mini, in
~/.config/kids-tv/config.json ("tvs", and optionally "run_days_path"); the repo only knows the neutral keys
boys-room and girls-room. Tokens come from ~/.config/vizio/<env file> and are used only inside this
script. They are never printed, logged, or put on a command line.
Python 3.9 standard library only. Casting uses the catt binary.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional
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
ON_GRACE = dt.timedelta(minutes=15)  # a power-on later than this after alarm is skipped
OFF_GRACE = dt.timedelta(minutes=20)  # a late power-off after this is skipped
CAST_SETTLE_SECONDS = 4
CAST_TRIES = 6
CAST_RETRY_SECONDS = 3


class ScheduleError(Exception):
    pass


class TvError(Exception):
    pass


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
    tvs = config.get("tvs", {})
    if not isinstance(tvs, dict):
        raise ScheduleError("local config tvs is not an object")
    for key, spec in tvs.items():
        if key not in TVS:
            raise ScheduleError("local config names an unknown TV key")
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


class Tv:
    def __init__(self, name: str, config_dir):
        self.name = name
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
        return text.replace(self._token, "<token>").replace(self._ip, "<ip>")

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
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
            raise TvError("%s: request failed (%s)" % (self.name, exc.__class__.__name__))
        status = (payload.get("STATUS") or {}).get("RESULT")
        if status not in (None, "SUCCESS"):
            raise TvError("%s: TV answered %s" % (self.name, status))
        return payload

    def power_key(self, state: str) -> None:
        self._request("PUT", "/key_command/", {
            "KEYLIST": [{"CODESET": 11, "CODE": POWER_CODES[state], "ACTION": "KEYPRESS"}],
        })

    def power_mode(self) -> str:
        payload = self._request("GET", "/state/device/power_mode")
        items = payload.get("ITEMS") or [{}]
        return {1: "on", 0: "off"}.get(items[0].get("VALUE"), "unknown")

    def cast(self, catt: str) -> None:
        cmd = [catt, "-d", self._ip, "cast_site", BOARD_URL]
        time.sleep(CAST_SETTLE_SECONDS)
        last = ""
        for attempt in range(CAST_TRIES):
            try:
                done = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                                      text=True, timeout=60)
            except (OSError, subprocess.TimeoutExpired) as exc:
                last = exc.__class__.__name__
            else:
                if done.returncode == 0:
                    return
                tail = (done.stderr or done.stdout or "").strip().splitlines()
                last = "exit %d %s" % (done.returncode, self._scrub(tail[-1] if tail else ""))
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


def attempt(name: str, step: str, st: dict, now: dt.datetime, state_path, state: dict,
            actions: Dict[str, Callable], log: Callable) -> bool:
    """Run one step, writing the intent to the state file BEFORE acting.

    If the state cannot be written, StateError is raised and nothing is sent. The
    step is marked done first and unmarked only when the TV reports a failure,
    so a crash or a failed write afterwards can never repeat a send.
    Returns True when the step went out.
    """
    st[DONE_KEY[step]] = now.isoformat()
    st[step + "_tries"] = st.get(step + "_tries", 0) + 1
    save_state(state_path, state)
    try:
        actions[step](name)
    except TvError as exc:
        st.pop(DONE_KEY[step], None)
        log("%s: %s failed, try %d of %d: %s" % (name, step, st[step + "_tries"], MAX_ATTEMPTS, exc))
        try:
            save_state(state_path, state)
        except StateError:
            st[DONE_KEY[step]] = now.isoformat()  # unknown on disk: stay on the safe side
            raise
        return False
    log("%s: %s" % (name, SENT_TEXT[step]))
    return True


def tick(schedule: dict, now: dt.datetime, state_path, actions: Dict[str, Callable],
         log: Callable, dry_run: bool = False) -> int:
    """Do whatever is due. Returns the number of failed steps."""
    day = now.date()
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
            log("ERROR: state file is unreadable, so nothing is sent today")
        for name in TVS:
            st = state["tvs"].setdefault(name, {})
            step = decide(on_at, off_at, now, st)
            if step is None:
                continue
            if step == "skip_on":
                if dry_run:
                    print("%s: would skip power-on, more than %d minutes past alarm" % (name, ON_GRACE.seconds // 60))
                    continue
                st["on_skipped"] = now.isoformat()
                save_state(state_path, state)
                log("%s: power-on skipped, more than %d minutes past alarm" % (name, ON_GRACE.seconds // 60))
                continue
            if dry_run:
                print("%s: would %s" % (name, step))
                continue
            if not attempt(name, step, st, now, state_path, state, actions, log):
                failures += 1
            elif step == "on":
                # Power-on went out, so cast now in the same run; a miss retries next tick.
                if not attempt(name, "cast", st, now, state_path, state, actions, log):
                    failures += 1
    except StateError as exc:
        log("ERROR: %s, so nothing more is sent today" % exc)
        return failures + 1
    return failures


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


# ----------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Kids' bedroom TVs show the morning board.")
    p.add_argument("--schedule", default=None, help="schedule.json (default: this repo's)")
    p.add_argument("--run-days", default=None, help="calendar run-days file (default: KIDS_TV_RUN_DAYS, the local config's run_days_path, or ~/.config/kids-tv/run-days.json)")
    p.add_argument("--config", default=None, help="local config (default: ~/.config/kids-tv/config.json)")
    p.add_argument("--config-dir", default=str(HOME / ".config" / "vizio"))
    p.add_argument("--state", default=str(HOME / "Library" / "Application Support" / "morning-board" / "kids-tv-state.json"))
    p.add_argument("--log", default=str(HOME / "Library" / "Logs" / "kids-tv-morning-board.log"))
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


def make_actions(args) -> Dict[str, Callable]:
    cache: Dict[str, Tv] = {}

    def tv(name: str) -> Tv:
        if name not in cache:
            cache[name] = Tv(name, args.config_dir)
        return cache[name]

    return {
        "on": lambda n: tv(n).power_key("on"),
        "off": lambda n: tv(n).power_key("off"),
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
            day = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(schedule_zone(schedule)).date()
            plan = plan_for(schedule, day)
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
            failed = tick(schedule, now, args.state, make_actions(args), log, dry_run=args.dry_run)
            return 1 if failed else 0
        actions = make_actions(args)
        names = list(TVS) if args.tv == "all" else [args.tv]
        for name in names:
            steps = ["on", "cast"] if args.cmd == "on" else [args.cmd]
            for step in steps:
                if args.dry_run:
                    print("%s: would %s%s" % (name, step, " " + BOARD_URL if step == "cast" else ""))
                    continue
                actions[step](name)
                if step != "status":
                    log("%s: manual %s sent" % (name, step))
                    print("%s: %s sent" % (name, step))
        return 0
    except (ScheduleError, TvError, ValueError) as exc:
        if args.cmd == "tick":
            marker = Path(args.state).with_name("kids-tv-error-day.json")
            report_error_once(str(exc), error_day, marker, log)
        else:
            log("ERROR: %s" % exc)
            sys.stderr.write("ERROR: %s\n" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
