#!/usr/bin/env python3
"""Kids' bedroom TVs show the morning board.

On a board day, at the board's alarm time, turn on Dan's Vizio and the girls'
Vizio and cast the morning board. At on-the-road time, power each one off once.

Times come from schedule.json, read fresh on every run. Nothing is hard-coded.
A day with no enabled alarm and leaveBy means nothing runs.

    kids-tv.py plan [--date YYYY-MM-DD] [--json]   print the times, touch no TV
    kids-tv.py tick                                what launchd runs every minute
    kids-tv.py on|cast|off|status [--tv NAME]      one manual action, [--dry-run]

tick sends nothing to a TV outside the window: one power-on and cast per TV
after alarm, one power-off per TV after on-the-road. Each is recorded in a
state file, so a reboot or a second tick never repeats it. It never checks
what the TV is doing, so a kid who switches away is left alone.

Tokens come from ~/.config/vizio/<tv>.env and are used only inside this
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
TVS = {
    "daniel": ("daniel.env", "VIZIO_DANIEL_"),
    "girls": ("girls.env", "VIZIO_GIRLS_"),
}
DEFAULT_TZ = "America/Los_Angeles"
VIZIO_PORT = 7345
# SmartCast power keys: codeset 11, code 1 = power on, code 0 = power off.
POWER_CODES = {"on": 1, "off": 0}

MAX_ATTEMPTS = 4  # per TV, per step, per day
OFF_GRACE = dt.timedelta(minutes=20)  # a late power-off after this is skipped
CAST_SETTLE_SECONDS = 4
CAST_TRIES = 6
CAST_RETRY_SECONDS = 3


class ScheduleError(Exception):
    pass


class TvError(Exception):
    pass


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


def schedule_zone(schedule: dict) -> ZoneInfo:
    return ZoneInfo(schedule.get("timezone") or DEFAULT_TZ)


def plan_for(schedule: dict, day: dt.date) -> Optional[dict]:
    """Return {"label", "on", "off"} for a board day, None when nothing runs.

    A board day is enabled with both alarm and leaveBy set, which is how the
    board decides too. Leave-by is on-the-road time, not downstairs time.
    """
    key = DAY_KEYS[day.weekday()]
    entry = schedule["days"].get(key)
    if not isinstance(entry, dict) or not entry.get("enabled"):
        return None
    if not entry.get("alarm") or not entry.get("leaveBy"):
        return None
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


def load_state(path, day: dt.date) -> dict:
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        if state.get("date") == day.isoformat() and isinstance(state.get("tvs"), dict):
            return state
    except (OSError, ValueError, AttributeError):
        pass
    return empty_state(day)


def save_state(path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def decide(on_at: dt.datetime, off_at: dt.datetime, now: dt.datetime, st: dict) -> Optional[str]:
    """The one step a TV is due for right now: "on", "cast", "off" or None."""
    if st.get("off_done") or st.get("off_tries", 0) >= MAX_ATTEMPTS:
        return None
    if now >= off_at:
        if st.get("power_on_done") and now < off_at + OFF_GRACE:
            return "off"
        return None
    if now < on_at:
        return None
    if not st.get("power_on_done"):
        return "on" if st.get("on_tries", 0) < MAX_ATTEMPTS else None
    if not st.get("cast_done") and st.get("cast_tries", 0) < MAX_ATTEMPTS:
        return "cast"
    return None


def tick(schedule: dict, now: dt.datetime, state_path, actions: Dict[str, Callable],
         log: Callable, dry_run: bool = False) -> int:
    """Do whatever is due. Returns the number of failed steps."""
    day = now.date()
    plan = plan_for(schedule, day)
    if plan is None:
        return 0
    on_at = dt.datetime.combine(day, plan["on"], tzinfo=now.tzinfo)
    off_at = dt.datetime.combine(day, plan["off"], tzinfo=now.tzinfo)
    state = load_state(state_path, day)
    failures = 0
    for name in TVS:
        st = state["tvs"].setdefault(name, {})
        step = decide(on_at, off_at, now, st)
        if step is None:
            continue
        if dry_run:
            print("%s: would %s" % (name, step))
            continue
        try:
            if step == "on":
                actions["on"](name)
                st["power_on_done"] = now.isoformat()
                log("%s: power on sent" % name)
            elif step == "cast":
                actions["cast"](name)
                st["cast_done"] = now.isoformat()
                log("%s: cast sent" % name)
            else:
                actions["off"](name)
                st["off_done"] = now.isoformat()
                log("%s: power off sent" % name)
        except TvError as exc:
            st[step + "_tries"] = st.get(step + "_tries", 0) + 1
            failures += 1
            log("%s: %s failed, try %d of %d: %s" % (name, step, st[step + "_tries"], MAX_ATTEMPTS, exc))
        if step == "on" and st.get("power_on_done"):
            # Power-on went out, so cast now in the same run; a miss retries next tick.
            try:
                actions["cast"](name)
                st["cast_done"] = now.isoformat()
                log("%s: cast sent" % name)
            except TvError as exc:
                st["cast_tries"] = st.get("cast_tries", 0) + 1
                failures += 1
                log("%s: cast failed, try %d of %d: %s" % (name, st["cast_tries"], MAX_ATTEMPTS, exc))
        save_state(state_path, state)
    return failures


# ----------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Kids' bedroom TVs show the morning board.")
    p.add_argument("--schedule", default=None, help="schedule.json (default: this repo's)")
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
    schedule_path = args.schedule or default_schedule_path()
    try:
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
        log("error: %s" % exc)
        sys.stderr.write("error: %s\n" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
