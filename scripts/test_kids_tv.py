#!/usr/bin/env python3
"""Checks for the kids' TV job. No TV, no network, no real tokens."""

from __future__ import annotations

import datetime as dt
import importlib.util
import io
import json
import os
import plistlib
import re
import sys
import tempfile
import unittest
import io
import subprocess
import urllib.error
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kids_tv", Path(__file__).with_name("kids-tv.py"))
kt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kt)

LA = ZoneInfo("America/Los_Angeles")
SCHEDULE = {
    "timezone": "America/Los_Angeles",
    "days": {
        "monday": {"enabled": True, "label": "School day", "alarm": "06:00", "leaveBy": "07:10"},
        "tuesday": {"enabled": False, "label": "Not here", "alarm": None, "leaveBy": None},
        "wednesday": {"enabled": False, "alarm": None, "leaveBy": None},
        "thursday": {"enabled": True, "label": "School day", "alarm": "06:00", "leaveBy": "07:10"},
        "friday": {"enabled": True, "label": "Late start Friday", "alarm": "06:30", "leaveBy": "07:40"},
        "saturday": {"enabled": False, "alarm": None, "leaveBy": None},
        "sunday": {"enabled": False, "alarm": None, "leaveBy": None},
    },
}
THU = dt.date(2026, 10, 29)
TUE = dt.date(2026, 10, 27)
FRI = dt.date(2026, 10, 30)


_NO_SKIP_DIR = tempfile.TemporaryDirectory()
# The module's clock: the evening a (made-up) run-days file was written.
CLOCK = dt.datetime(2026, 10, 28, 21, 0, tzinfo=LA)
_clock_patch = mock.patch.object(kt, "clock", lambda: CLOCK)
# Default state, lock and log paths come from kt.HOME: keep them in a temp dir, never the mini's real ones.
_home_patch = mock.patch.object(kt, "HOME", Path(_NO_SKIP_DIR.name, "home"))


def run_days_doc(generated_at, days):
    """A run-days document. days: {"YYYY-MM-DD": run}; every other field is made up."""
    return {"generated_at": generated_at, "horizon_end": "2026-11-11",
            "days": {d: {"kids_home": bool(r), "no_school": not r, "run": r, "why": "made-up note"}
                     for d, r in days.items()}}


def every_day_runs(first=dt.date(2026, 10, 1), count=100):
    return {(first + dt.timedelta(days=i)).isoformat(): True for i in range(count)}


def setUpModule():
    # Other tests must not read the repo's real skip file, the mini's real local config,
    # or the mini's real run-days file. Run-days says run for every day, and the clock is fixed.
    os.environ["KIDS_TV_SKIP"] = str(Path(_NO_SKIP_DIR.name, "none.json"))
    os.environ["KIDS_TV_CONFIG"] = str(Path(_NO_SKIP_DIR.name, "no-config.json"))
    fixture = Path(_NO_SKIP_DIR.name, "run-days.json")
    fixture.write_text(json.dumps(run_days_doc(CLOCK.isoformat(), every_day_runs())))
    os.environ["KIDS_TV_RUN_DAYS"] = str(fixture)
    os.environ["KIDS_TV_ALERT"] = str(Path(_NO_SKIP_DIR.name, "alert.json"))  # never the mini's real alert file
    _clock_patch.start()
    _home_patch.start()


def tearDownModule():
    _clock_patch.stop()
    _home_patch.stop()
    os.environ.pop("KIDS_TV_SKIP", None)
    os.environ.pop("KIDS_TV_CONFIG", None)
    os.environ.pop("KIDS_TV_RUN_DAYS", None)
    os.environ.pop("KIDS_TV_ALERT", None)
    _NO_SKIP_DIR.cleanup()


def at(day, h, m, s=0):
    return dt.datetime(day.year, day.month, day.day, h, m, s, tzinfo=LA)


class Recorder:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def actions(self):
        def make(step):
            def run(name):
                self.calls.append((step, name))
                if (step, name) in self.fail:
                    raise kt.TvError(name + ": boom")
            return run
        return {step: make(step) for step in ("on", "cast", "off")}


class PlanTests(unittest.TestCase):
    def test_board_day(self):
        plan = kt.plan_for(SCHEDULE, THU)
        self.assertEqual((plan["on"], plan["off"]), (dt.time(6, 0), dt.time(7, 10)))

    def test_friday_late_start(self):
        plan = kt.plan_for(SCHEDULE, FRI)
        self.assertEqual((plan["on"], plan["off"]), (dt.time(6, 30), dt.time(7, 40)))

    def test_non_board_days(self):
        for day in (TUE, dt.date(2026, 10, 28), dt.date(2026, 10, 31), dt.date(2026, 11, 1)):
            self.assertIsNone(kt.plan_for(SCHEDULE, day), day)

    def test_is_run_day_is_the_only_gate(self):
        self.assertTrue(kt.is_run_day(SCHEDULE, THU))
        self.assertFalse(kt.is_run_day(SCHEDULE, TUE))
        with mock.patch.object(kt, "is_run_day", return_value=False):
            self.assertIsNone(kt.plan_for(SCHEDULE, THU))
        with mock.patch.object(kt, "is_run_day", return_value=True):
            self.assertEqual(kt.hm(kt.plan_for(SCHEDULE, THU)["on"]), "06:00")
            with self.assertRaises(kt.ScheduleError):
                kt.plan_for(SCHEDULE, TUE)

    def test_enabled_without_times_is_no_board_day(self):
        sched = json.loads(json.dumps(SCHEDULE))
        sched["days"]["thursday"]["leaveBy"] = None
        self.assertIsNone(kt.plan_for(sched, THU))

    def test_off_is_on_the_road_not_downstairs(self):
        self.assertEqual(kt.hm(kt.plan_for(SCHEDULE, THU)["off"]), "07:10")

    def test_bad_time_and_backwards_times_raise(self):
        sched = json.loads(json.dumps(SCHEDULE))
        sched["days"]["thursday"]["alarm"] = "6am"
        with self.assertRaises(kt.ScheduleError):
            kt.plan_for(sched, THU)
        sched["days"]["thursday"].update(alarm="08:00", leaveBy="07:10")
        with self.assertRaises(kt.ScheduleError):
            kt.plan_for(sched, THU)

    def test_render(self):
        self.assertEqual(kt.render_plan(TUE, None), "2026-10-27 Tuesday: no board day, nothing runs")
        text = kt.render_plan(FRI, kt.plan_for(SCHEDULE, FRI))
        self.assertIn("boys-room: on 06:30, off 07:40", text)
        self.assertIn("girls-room: on 06:30, off 07:40", text)


class SkipTests(unittest.TestCase):
    def skip_file(self, content):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name, "kids-tv-skip.json")
        if content is not None:
            path.write_text(content)
        return path

    def test_listed_date_is_skipped_and_neighbours_are_not(self):
        path = self.skip_file('{"skipDates": ["2026-10-30"]}')
        self.assertFalse(kt.is_run_day(SCHEDULE, FRI, path))
        self.assertTrue(kt.is_run_day(SCHEDULE, THU, path))
        self.assertTrue(kt.is_run_day(SCHEDULE, dt.date(2026, 11, 6), path))  # next Friday

    def test_skip_applies_through_plan_and_tick(self):
        path = self.skip_file('{"skipDates": ["2026-10-30"]}')
        with mock.patch.dict(os.environ, {"KIDS_TV_SKIP": str(path)}):
            self.assertIsNone(kt.plan_for(SCHEDULE, FRI))
            self.assertIsNotNone(kt.plan_for(SCHEDULE, THU))
            rec = Recorder()
            with tempfile.TemporaryDirectory() as tmp:
                kt.tick(SCHEDULE, at(FRI, 6, 31), Path(tmp, "s.json"), rec.actions(), kt.Log(Path(tmp, "l.log")))
            self.assertEqual(rec.calls, [])

    def test_read_fresh_each_call(self):
        path = self.skip_file('{"skipDates": []}')
        self.assertTrue(kt.is_run_day(SCHEDULE, FRI, path))
        path.write_text('{"skipDates": ["2026-10-30"]}')
        self.assertFalse(kt.is_run_day(SCHEDULE, FRI, path))

    def test_missing_file_means_no_skips(self):
        path = self.skip_file(None)
        self.assertTrue(kt.is_run_day(SCHEDULE, FRI, path))

    def test_malformed_file_logs_one_error_and_nothing_runs(self):
        for bad in ("{not json", "[]", "{}", '{"skipDates": "2026-10-30"}',
                    '{"skipDates": ["tomorrow"]}', '{"skipDates": [20261030]}',
                    '{"skipDates": ["2026-13-45"]}'):
            path = self.skip_file(bad)
            with self.assertRaises(kt.ScheduleError, msg=bad):
                kt.is_run_day(SCHEDULE, THU, path)

    def test_malformed_file_through_main_logs_error_and_sends_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            skip = Path(tmp, "skip.json")
            skip.write_text("{oops")
            sched = Path(tmp, "schedule.json")
            sched.write_text(json.dumps(SCHEDULE))
            log = Path(tmp, "l.log")
            state = Path(tmp, "s.json")
            with mock.patch.dict(os.environ, {"KIDS_TV_SKIP": str(skip)}):
                code = kt.main(["--schedule", str(sched), "--log", str(log), "--state", str(state),
                                "tick", "--now", "2026-10-29T06:05:00"])
            self.assertEqual(code, 1)
            self.assertEqual(len(log.read_text().strip().splitlines()), 1)
            self.assertIn("skip file", log.read_text())
            self.assertFalse(state.exists())

    def test_repo_skip_file_lists_oct_30_only(self):
        self.assertEqual(kt.load_skip_dates(ROOT / "kids-tv-skip.json"), {dt.date(2026, 10, 30)})


class ConfigAndNitTests(unittest.TestCase):
    def test_enabled_must_be_a_real_true(self):
        for bad in ("false", "true", 1, "yes", None):
            sched = json.loads(json.dumps(SCHEDULE))
            sched["days"]["thursday"]["enabled"] = bad
            self.assertFalse(kt.is_run_day(sched, THU), repr(bad))
            self.assertIsNone(kt.plan_for(sched, THU))

    def test_bad_timezone_is_a_clean_error_not_a_traceback(self):
        for bad in ("Nowhere/Land", 5, ["x"]):
            with self.assertRaises(kt.ScheduleError):
                kt.schedule_zone({"days": {}, "timezone": bad})
        with tempfile.TemporaryDirectory() as tmp:
            sched = Path(tmp, "schedule.json")
            sched.write_text(json.dumps(dict(SCHEDULE, timezone="Nowhere/Land")))
            rc = kt.main(["--schedule", str(sched), "--log", tmp + "/log", "--state", tmp + "/s.json",
                          "--config", tmp + "/none.json", "tick"])
            self.assertEqual(rc, 1)
            self.assertIn("timezone", Path(tmp, "log").read_text())

    def test_local_config_maps_a_neutral_key_to_an_env_file(self):
        saved = dict(kt.TVS)
        self.addCleanup(lambda: (kt.TVS.clear(), kt.TVS.update(saved)))
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp, "config.json")
            cfg.write_text(json.dumps({"tvs": {"boys-room": {"env_file": "tv-one.env", "prefix": "VIZIO_ONE_"}}}))
            kt.apply_local_config(kt.load_local_config(cfg))
            self.assertEqual(kt.TVS["boys-room"], ("tv-one.env", "VIZIO_ONE_"))
            Path(tmp, "tv-one.env").write_text("VIZIO_ONE_IP=10.0.0.1\nVIZIO_ONE_TOKEN=abc\n")
            kt.Tv("boys-room", tmp)  # loads without error

    def test_local_config_missing_is_defaults_and_bad_is_an_error(self):
        self.assertEqual(kt.load_local_config("/nonexistent/config.json"), {})
        with self.assertRaises(kt.ScheduleError):
            kt.apply_local_config({"tvs": {"not-a-tv": {"env_file": "a.env", "prefix": "A_"}}})
        with self.assertRaises(kt.ScheduleError):
            kt.apply_local_config({"tvs": {"boys-room": {"env_file": "../x.env", "prefix": "A_"}}})


class LoadTests(unittest.TestCase):
    def test_reads_file_fresh_each_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "schedule.json")
            path.write_text(json.dumps(SCHEDULE))
            self.assertEqual(kt.hm(kt.plan_for(kt.load_schedule(path), THU)["on"]), "06:00")
            sched = json.loads(json.dumps(SCHEDULE))
            sched["days"]["thursday"]["alarm"] = "06:15"
            path.write_text(json.dumps(sched))
            self.assertEqual(kt.hm(kt.plan_for(kt.load_schedule(path), THU)["on"]), "06:15")

    def test_missing_file(self):
        with self.assertRaises(kt.ScheduleError):
            kt.load_schedule("/nonexistent/schedule.json")

    def test_bad_json_and_no_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "schedule.json")
            path.write_text("{nope")
            with self.assertRaises(kt.ScheduleError):
                kt.load_schedule(path)
            path.write_text("{}")
            with self.assertRaises(kt.ScheduleError):
                kt.load_schedule(path)

    def test_missing_file_runs_nothing_and_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = kt.main(["--schedule", tmp + "/nope.json", "--log", tmp + "/log",
                          "--state", tmp + "/state.json", "tick"])
            self.assertEqual(rc, 1)
            self.assertFalse(Path(tmp, "state.json").exists())
            self.assertIn("cannot read schedule file", Path(tmp, "log").read_text())

    def test_repo_schedule_json_parses_for_every_day(self):
        sched = kt.load_schedule(ROOT / "schedule.json")
        for offset in range(7):
            kt.plan_for(sched, THU + dt.timedelta(days=offset))


class RunDaysTests(unittest.TestCase):
    """The calendar run-days file: run must be a literal true, and anything doubtful means no run."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "run-days.json"

    def write(self, doc):
        self.path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
        return self.path

    def fresh(self, days, generated_at=None):
        return self.write(run_days_doc(generated_at or CLOCK.isoformat(), days))

    def check(self, day=THU, **kw):
        return kt.is_run_day(SCHEDULE, day, self.dir / "no-skip.json", self.path, kw.get("now", CLOCK))

    def test_run_true_runs(self):
        self.fresh({"2026-10-29": True})
        self.assertTrue(self.check())

    def test_run_false_does_not_run_and_is_not_an_error(self):
        self.fresh({"2026-10-29": False})
        self.assertFalse(self.check())

    def test_no_school_with_run_false_does_not_run(self):
        doc = run_days_doc(CLOCK.isoformat(), {"2026-10-29": False})
        doc["days"]["2026-10-29"].update(kids_home=True, no_school=True, run=False)
        self.write(doc)
        self.assertFalse(self.check())

    def test_run_true_needs_the_schedule_too(self):
        self.fresh({"2026-10-27": True, "2026-10-29": True})
        self.assertFalse(self.check(TUE))  # schedule has Tuesday off
        sched = json.loads(json.dumps(SCHEDULE))
        sched["days"]["thursday"]["enabled"] = False
        self.assertFalse(kt.is_run_day(sched, THU, self.dir / "x.json", self.path, CLOCK))

    def test_off_schedule_day_never_reads_the_run_days_file(self):
        self.assertFalse(self.check(TUE))  # file does not exist, and no error

    def test_date_absent_is_an_error(self):
        self.fresh({"2026-10-30": True})
        with self.assertRaises(kt.RunDaysError) as ctx:
            self.check()
        self.assertIn("2026-10-29", str(ctx.exception))

    def test_missing_file_is_an_error(self):
        with self.assertRaises(kt.RunDaysError):
            self.check()

    def test_unreadable_file_is_an_error(self):
        self.path.mkdir()  # a directory where the file should be
        with self.assertRaises(kt.RunDaysError):
            self.check()

    def test_malformed_files_are_errors(self):
        good = run_days_doc(CLOCK.isoformat(), {"2026-10-29": True})
        cases = ["{not json", "[]", "{}", '{"days": []}', '{"generated_at": "x"}']
        for key in ('"true"', "1", "null", "[]"):
            doc = json.loads(json.dumps(good))
            cases.append(json.dumps(doc).replace('"run": true', '"run": ' + key))
        doc = json.loads(json.dumps(good)); doc["days"]["2026-10-29"] = "yes"; cases.append(json.dumps(doc))
        for bad_stamp in (None, 5, "yesterday", ""):
            doc = json.loads(json.dumps(good)); doc["generated_at"] = bad_stamp; cases.append(json.dumps(doc))
        for case in cases:
            self.write(case)
            with self.assertRaises(kt.RunDaysError, msg=case[:80]):
                self.check()

    def test_stale_generated_at_is_an_error_and_fresh_is_fine(self):
        for days_old, ok in ((4, True), (5, True), (6, False)):
            stamp = (CLOCK - dt.timedelta(days=days_old)).isoformat()
            self.fresh({"2026-10-29": True}, stamp)
            if ok:
                self.assertTrue(self.check(), days_old)
            else:
                with self.assertRaises(kt.RunDaysError) as ctx:
                    self.check()
                self.assertIn("stale", str(ctx.exception))

    def test_generated_at_in_the_future_is_an_error(self):
        self.fresh({"2026-10-29": True}, (CLOCK + dt.timedelta(hours=23)).isoformat())
        self.assertTrue(self.check())
        self.fresh({"2026-10-29": True}, (CLOCK + dt.timedelta(days=2)).isoformat())
        with self.assertRaises(kt.RunDaysError):
            self.check()

    def test_timestamp_spellings_are_accepted(self):
        for stamp in ("2026-10-28T20:15:00-07:00", "2026-10-29T03:15:00Z", "2026-10-28T20:15:00.123456789-07:00",
                      "2026-10-28T20:15:00.5+00:00", "2026-10-28T20:15:00"):
            self.fresh({"2026-10-29": True}, stamp)
            self.assertTrue(self.check(), stamp)

    def test_skip_list_overrides_run_true(self):
        self.fresh({"2026-10-30": True})
        skip = self.dir / "skip.json"
        skip.write_text('{"skipDates": ["2026-10-30"]}')
        self.assertFalse(kt.is_run_day(SCHEDULE, FRI, skip, self.path, CLOCK))
        # and a skipped day does not need a run-days entry at all
        self.fresh({"2026-10-29": True})
        self.assertFalse(kt.is_run_day(SCHEDULE, FRI, skip, self.path, CLOCK))

    def test_run_days_read_fresh_each_call(self):
        self.fresh({"2026-10-29": False})
        self.assertFalse(self.check())
        self.fresh({"2026-10-29": True})
        self.assertTrue(self.check())

    def test_path_comes_from_env_then_local_config_then_default(self):
        saved = kt.run_days_path_from_config
        self.addCleanup(lambda: setattr(kt, "run_days_path_from_config", saved))
        with mock.patch.dict(os.environ, {"KIDS_TV_RUN_DAYS": ""}):
            self.assertEqual(kt.default_run_days_path(), Path.home() / ".config" / "kids-tv" / "run-days.json")
            cfg = self.dir / "config.json"
            cfg.write_text(json.dumps({"run_days_path": str(self.dir / "from-config.json")}))
            kt.apply_local_config(kt.load_local_config(cfg))
            self.assertEqual(kt.default_run_days_path(), self.dir / "from-config.json")
            with mock.patch.dict(os.environ, {"KIDS_TV_RUN_DAYS": str(self.path)}):
                self.assertEqual(kt.default_run_days_path(), self.path)
            for bad in (5, "", ["x"]):
                with self.assertRaises(kt.ScheduleError):
                    kt.apply_local_config({"run_days_path": bad})

    def test_plan_for_and_tick_follow_the_file(self):
        self.fresh({"2026-10-29": False, "2026-10-30": True})
        with mock.patch.dict(os.environ, {"KIDS_TV_RUN_DAYS": str(self.path)}):
            self.assertIsNone(kt.plan_for(SCHEDULE, THU, CLOCK))
            self.assertIsNotNone(kt.plan_for(SCHEDULE, FRI, CLOCK))
            rec = Recorder()
            kt.tick(SCHEDULE, at(THU, 6, 1), self.dir / "s.json", rec.actions(), kt.Log(self.dir / "l.log"))
            self.assertEqual(rec.calls, [])

    def run_tick_main(self, now, log, state):
        sched = self.dir / "schedule.json"
        sched.write_text(json.dumps(SCHEDULE))
        return kt.main(["--schedule", str(sched), "--run-days", str(self.path), "--log", str(log),
                        "--state", str(state), "tick", "--now", now])

    def test_error_is_logged_once_per_day_not_every_minute_and_goes_to_stderr(self):
        log, state = self.dir / "l.log", self.dir / "s.json"
        err = io.StringIO()
        with mock.patch.dict(os.environ), mock.patch.object(sys, "stderr", err):
            for minute in range(0, 30):
                self.assertEqual(self.run_tick_main("2026-10-29T06:%02d:00" % minute, log, state), 1)
            lines = log.read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertIn("ERROR:", lines[0])
            self.assertIn("run-days file is missing", lines[0])
            self.assertEqual(len(err.getvalue().splitlines()), 1)
            # a new day logs again; a different problem the same day also logs
            self.assertEqual(self.run_tick_main("2026-10-30T06:31:00", log, state), 1)
            self.assertEqual(len(log.read_text().splitlines()), 2)
            self.path.write_text("{oops")
            self.assertEqual(self.run_tick_main("2026-10-30T06:32:00", log, state), 1)
            self.assertEqual(len(log.read_text().splitlines()), 3)
        self.assertFalse(state.exists())  # nothing was sent, so no state either

    def test_nothing_is_sent_when_the_file_is_missing(self):
        log, state = self.dir / "l.log", self.dir / "s.json"
        with mock.patch.dict(os.environ), mock.patch.object(sys, "stderr", io.StringIO()), \
                mock.patch.object(kt, "Tv", side_effect=AssertionError("must not touch a TV")):
            for minute in (0, 5, 60, 70):
                self.run_tick_main("2026-10-29T0%d:%02d:00" % (6 + minute // 60, minute % 60), log, state)

    def test_error_text_never_carries_the_why_note(self):
        doc = run_days_doc(CLOCK.isoformat(), {"2026-10-29": True})
        doc["days"]["2026-10-29"]["why"] = "SECRET-NOTE"
        doc["days"]["2026-10-29"]["run"] = "maybe"
        self.write(doc)
        log, state = self.dir / "l.log", self.dir / "s.json"
        with mock.patch.dict(os.environ), mock.patch.object(sys, "stderr", io.StringIO()) as err:
            self.run_tick_main("2026-10-29T06:00:00", log, state)
        self.assertNotIn("SECRET-NOTE", log.read_text())
        self.assertNotIn("SECRET-NOTE", err.getvalue())

    def test_repo_has_no_calendar_ids_custody_words_or_school_names(self):
        bad = re.compile("|".join(["group" + r"\.calendar", "dad'?s" + " house", "mom'?s" + " house", "kid" + " rotation"]), re.I)
        for path in (Path(kt.__file__), Path(__file__), ROOT / "README.md", ROOT / "kids-tv-skip.json"):
            self.assertIsNone(bad.search(path.read_text()), path.name)


class GraceAndStateErrorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_power_on_grace_edge_is_fifteen_minutes_or_more(self):
        for minute, second, sent in ((14, 59, True), (15, 0, False)):
            rec, logs = Recorder(), []
            state = self.dir / ("s%d.json" % minute)
            kt.tick(SCHEDULE, at(THU, 6, minute, second), state, rec.actions(), logs.append)
            self.assertEqual(bool(rec.calls), sent, (minute, second))
            if not sent:
                self.assertTrue(all("15 minutes or more past alarm" in m for m in logs if "skipped" in m))

    def test_unwritable_state_error_is_logged_once_a_day_not_every_minute(self):
        blocker = self.dir / "blocker"
        blocker.write_text("a file, so a state file under it can never be written")
        state, log, sched = blocker / "s.json", self.dir / "l.log", self.dir / "schedule.json"
        sched.write_text(json.dumps(SCHEDULE))
        rec = Recorder()

        def run(now):
            with mock.patch.object(kt, "make_actions", return_value=rec.actions()), \
                    mock.patch.object(sys, "stderr", io.StringIO()) as err:
                code = kt.main(["--schedule", str(sched), "--log", str(log), "--state", str(state),
                                "tick", "--now", now])
            return code, err.getvalue()

        for minute in range(0, 20):
            code, err = run("2026-10-29T06:%02d:00" % minute)
            self.assertEqual(code, 1)
            self.assertEqual(err.count("ERROR:"), 1 if minute == 0 else 0)
        self.assertEqual(rec.calls, [])  # nothing was sent while the state could not be written
        lines = log.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("cannot write state file", lines[0])
        run("2026-10-30T06:30:00")  # a new day logs again
        self.assertEqual(len(log.read_text().splitlines()), 2)


class TickTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name, "state.json")
        self.logs = []

    def run_tick(self, now, rec, dry=False):
        return kt.tick(SCHEDULE, now, self.state, rec.actions(), lambda m: self.logs.append(m), dry)

    def test_nothing_before_alarm_or_on_non_board_day(self):
        rec = Recorder()
        self.run_tick(at(THU, 5, 59, 59), rec)
        self.run_tick(at(TUE, 6, 30), rec)
        self.assertEqual(rec.calls, [])
        self.assertFalse(self.state.exists())

    def test_alarm_turns_both_on_then_casts(self):
        rec = Recorder()
        self.assertEqual(self.run_tick(at(THU, 6, 0), rec), 0)
        self.assertEqual(rec.calls, [("on", "boys-room"), ("cast", "boys-room"), ("on", "girls-room"), ("cast", "girls-room")])

    def test_no_repeat_and_no_recast_between_on_and_off(self):
        rec = Recorder()
        for minute in range(0, 70):
            self.run_tick(at(THU, 6, 0) + dt.timedelta(minutes=minute), rec)
        self.assertEqual(len(rec.calls), 4)

    def test_one_power_off_per_tv_at_on_the_road_time(self):
        rec = Recorder()
        for minute in range(0, 90):
            self.run_tick(at(THU, 6, 0) + dt.timedelta(minutes=minute), rec)
        self.assertEqual([c for c in rec.calls if c[0] == "off"], [("off", "boys-room"), ("off", "girls-room")])
        self.assertEqual(rec.calls[-2:], [("off", "boys-room"), ("off", "girls-room")])

    def test_friday_waits_for_its_own_times(self):
        rec = Recorder()
        self.run_tick(at(FRI, 6, 0), rec)
        self.assertEqual(rec.calls, [])
        self.run_tick(at(FRI, 6, 30), rec)
        self.assertEqual(rec.calls[0], ("on", "boys-room"))
        rec.calls.clear()
        self.run_tick(at(FRI, 7, 10), rec)
        self.assertEqual(rec.calls, [])
        self.run_tick(at(FRI, 7, 40), rec)
        self.assertEqual(rec.calls, [("off", "boys-room"), ("off", "girls-room")])

    def test_survives_a_reboot_state_is_on_disk(self):
        rec = Recorder()
        self.run_tick(at(THU, 6, 0), rec)
        rec2 = Recorder()
        self.run_tick(at(THU, 6, 5), rec2)
        self.assertEqual(rec2.calls, [])

    def test_late_boot_inside_the_window_still_turns_on(self):
        rec = Recorder()
        self.run_tick(at(THU, 6, 10), rec)
        self.assertEqual(rec.calls[0], ("on", "boys-room"))

    def test_power_on_past_the_grace_window_is_skipped_and_logged_once(self):
        rec = Recorder()
        for minute in (16, 17, 40):
            self.run_tick(at(THU, 6, 0) + dt.timedelta(minutes=minute), rec)
        self.assertEqual(rec.calls, [])
        self.assertEqual(len([m for m in self.logs if "power-on skipped" in m]), 2)  # one per TV
        self.run_tick(at(THU, 7, 11), rec)
        self.assertEqual(rec.calls, [])  # and no power-off for a TV that never came on
        self.run_tick(at(THU, 6, 14), Recorder())  # inside grace on the same day is not retroactive

    def test_last_minute_inside_grace_still_turns_on(self):
        rec = Recorder()
        self.run_tick(at(THU, 6, 14, 59), rec)
        self.assertEqual(rec.calls[0], ("on", "boys-room"))

    def test_intent_is_on_disk_before_the_tv_is_touched(self):
        seen = []

        def spy(name):
            seen.append(json.loads(self.state.read_text())["tvs"][name].get("power_on_done"))

        actions = {"on": spy, "cast": lambda n: None, "off": lambda n: None}
        kt.tick(SCHEDULE, at(THU, 6, 0), self.state, actions, self.logs.append)
        self.assertEqual(len(seen), 2)
        self.assertTrue(all(seen))

    def test_unwritable_state_sends_nothing_and_logs_an_error(self):
        rec = Recorder()
        blocker = Path(self.tmp.name, "afile")
        blocker.write_text("x")
        bad_state = Path(blocker, "state.json")  # a file where a directory should be
        failures = kt.tick(SCHEDULE, at(THU, 6, 0), bad_state, rec.actions(), self.logs.append)
        self.assertEqual(rec.calls, [])
        self.assertEqual(failures, 1)
        self.assertEqual(len([m for m in self.logs if m.startswith("ERROR:")]), 1)

    def test_state_write_failing_after_a_send_does_not_repeat(self):
        rec = Recorder()
        real = kt.save_state
        calls = {"n": 0}

        def flaky(path, state):
            calls["n"] += 1
            if calls["n"] > 1:
                raise kt.StateError("cannot write state file (OSError)")
            real(path, state)

        with mock.patch.object(kt, "save_state", flaky):
            self.run_tick(at(THU, 6, 0), rec)
        self.assertEqual(rec.calls, [("on", "boys-room")])  # on went out; nothing after the write failed
        rec2 = Recorder()
        self.run_tick(at(THU, 6, 1), rec2)
        self.assertEqual([c for c in rec2.calls if c[:2] == ("on", "boys-room")], [])

    def test_corrupt_state_file_means_already_acted(self):
        self.state.write_text("{garbage")
        rec = Recorder()
        for minute in (0, 1, 70):
            self.run_tick(at(THU, 6, 0) + dt.timedelta(minutes=minute), rec)
        self.assertEqual(rec.calls, [])
        self.assertEqual(len([m for m in self.logs if m.startswith("ERROR:")]), 1)
        # the next day starts clean
        self.run_tick(at(FRI, 6, 30), rec)
        self.assertEqual(rec.calls[0], ("on", "boys-room"))

    def test_boot_after_off_time_does_nothing(self):
        rec = Recorder()
        self.run_tick(at(THU, 7, 11), rec)
        self.assertEqual(rec.calls, [])

    def test_no_power_off_if_it_never_came_on(self):
        rec = Recorder(fail={("on", "boys-room")} | {("on", "girls-room")})
        for minute in range(0, 80):
            self.run_tick(at(THU, 6, 0) + dt.timedelta(minutes=minute), rec)
        self.assertFalse([c for c in rec.calls if c[0] == "off"])

    def test_late_power_off_is_skipped(self):
        rec = Recorder()
        self.run_tick(at(THU, 6, 0), rec)
        rec.calls.clear()
        self.run_tick(at(THU, 9, 0), rec)
        self.assertEqual(rec.calls, [])

    def test_failed_on_retries_then_stops(self):
        rec = Recorder(fail={("on", "boys-room")})
        failures = 0
        for minute in range(0, 20):
            failures += self.run_tick(at(THU, 6, 0) + dt.timedelta(minutes=minute), rec)
        self.assertEqual(len([c for c in rec.calls if c == ("on", "boys-room")]), kt.MAX_ATTEMPTS)
        self.assertEqual(failures, kt.MAX_ATTEMPTS)
        self.assertEqual(len([c for c in rec.calls if c == ("on", "girls-room")]), 1)
        self.assertTrue(any("boys-room: on failed" in m for m in self.logs))

    def test_failed_cast_retries_the_cast_only(self):
        rec = Recorder(fail={("cast", "girls-room")})
        self.run_tick(at(THU, 6, 0), rec)
        self.run_tick(at(THU, 6, 1), rec)
        self.assertEqual(len([c for c in rec.calls if c == ("on", "girls-room")]), 1)
        self.assertEqual(len([c for c in rec.calls if c == ("cast", "girls-room")]), 2)

    def test_new_day_resets_state(self):
        rec = Recorder()
        self.run_tick(at(THU, 6, 0), rec)
        self.run_tick(at(FRI, 6, 30), rec)
        self.assertEqual(len([c for c in rec.calls if c[0] == "on"]), 4)

    def test_dry_run_sends_and_saves_nothing(self):
        rec = Recorder()
        with mock.patch("builtins.print") as shown:
            self.run_tick(at(THU, 6, 0), rec, dry=True)
        self.assertEqual(rec.calls, [])
        self.assertFalse(self.state.exists())
        shown.assert_any_call("boys-room: would on")


class SecretTests(unittest.TestCase):
    TOKEN = "SECRETTOKEN123"
    IP = "10.9.8.7"

    def make_tv(self, tmp):
        Path(tmp, "boys-room.env").write_text(
            "VIZIO_BOYS_ROOM_IP=%s\nVIZIO_BOYS_ROOM_TOKEN='%s'\n" % (self.IP, self.TOKEN))
        return kt.Tv("boys-room", tmp)

    def test_env_parse_and_missing_key_names_only_the_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_tv(tmp)
            Path(tmp, "girls-room.env").write_text("VIZIO_GIRLS_ROOM_IP=%s\n" % self.IP)
            with self.assertRaises(kt.TvError) as ctx:
                kt.Tv("girls-room", tmp)
            self.assertIn("VIZIO_GIRLS_ROOM_TOKEN", str(ctx.exception))
            self.assertNotIn(self.IP, str(ctx.exception))

    def test_errors_never_carry_the_token_or_ip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tv = self.make_tv(tmp)
            with mock.patch("urllib.request.urlopen",
                            side_effect=urllib.error.URLError("https://%s %s" % (self.IP, self.TOKEN))):
                with self.assertRaises(kt.TvError) as ctx:
                    tv.power_key("off")
            self.assertNotIn(self.TOKEN, str(ctx.exception))
            self.assertNotIn(self.IP, str(ctx.exception))

    def test_token_goes_in_a_header_not_the_url_or_catt_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            tv = self.make_tv(tmp)
            seen = {}

            class Resp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return b'{"STATUS":{"RESULT":"SUCCESS"}}'

            def fake(req, timeout, context):
                seen["req"] = req
                return Resp()

            with mock.patch("urllib.request.urlopen", fake):
                tv.power_key("off")
            req = seen["req"]
            self.assertNotIn(self.TOKEN, req.full_url)
            self.assertEqual(req.get_header("Auth"), self.TOKEN)
            self.assertEqual(json.loads(req.data)["KEYLIST"][0]["CODE"], 0)
            with mock.patch.object(kt.time, "sleep"), mock.patch.object(kt.subprocess, "run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                tv.cast("/fake/catt")
            self.assertNotIn(self.TOKEN, " ".join(run.call_args[0][0]))
            self.assertEqual(run.call_args[0][0][-2:], ["cast_site", kt.BOARD_URL])

    def test_failed_cast_message_is_scrubbed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tv = self.make_tv(tmp)
            bad = mock.Mock(returncode=1, stdout="", stderr="no device at %s" % self.IP)
            with mock.patch.object(kt.time, "sleep"), mock.patch.object(kt.subprocess, "run", return_value=bad):
                with self.assertRaises(kt.TvError) as ctx:
                    tv.cast("/fake/catt")
            self.assertNotIn(self.IP, str(ctx.exception))


class OffSticksTests(unittest.TestCase):
    """The off step stops the cast, powers off, and checks it stayed off. No TV, no network."""

    DASHCAST = "84912283"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        Path(self.tmp.name, "boys-room.env").write_text("VIZIO_BOYS_ROOM_IP=10.9.8.7\nVIZIO_BOYS_ROOM_TOKEN=t\n")
        self.tv = kt.Tv("boys-room", self.tmp.name)
        self.now = 0.0
        self.events = []  # (seconds, what)
        self.logs = []
        self.app = None  # what the receiver reports: None is idle
        self.power = lambda t: "off"  # power state at fake time t
        patches = [
            mock.patch.object(kt.time, "sleep", self.sleep),
            mock.patch.object(kt.time, "monotonic", lambda: self.now),
            mock.patch.object(self.tv, "_catt", self.catt),
            mock.patch.object(self.tv, "power_key", lambda state: self.events.append((self.now, "key " + state))),
            mock.patch.object(self.tv, "power_mode", self.read_power),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def sleep(self, seconds):
        self.now += max(0.0, seconds)

    def read_power(self):
        mode = self.power(self.now)
        self.events.append((self.now, "read " + mode))
        return mode

    def catt(self, catt, *args):
        self.events.append((self.now, "catt " + " ".join(args)))
        self.now += 1.0  # a catt call takes a moment
        if args[0] == "stop":
            self.app = None
        if args[0] == "info":
            return mock.Mock(returncode=0, stdout=json.dumps({"app_id": self.app}), stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    def off(self):
        self.tv.power_off_verified("/fake/catt", self.logs.append)

    def names(self):
        return [what for _, what in self.events]

    def test_cast_is_stopped_before_power_off(self):
        self.app = self.DASHCAST
        self.off()
        names = self.names()
        self.assertLess(names.index("catt stop"), names.index("key off"))
        # the receiver was read idle after the stop and before the power-off
        self.assertIn("catt info -j", names[names.index("catt stop"):names.index("key off")])
        self.assertTrue(any("receiver idle" in m for m in self.logs))

    def test_idle_receiver_is_not_stopped(self):
        self.off()
        self.assertNotIn("catt stop", self.names())

    def test_success_first_try_reads_at_5_30_60_90(self):
        self.off()
        sent = [t for t, what in self.events if what == "key off"]
        self.assertEqual(len(sent), 1)
        reads = [t - sent[0] for t, what in self.events if what.startswith("read")]
        self.assertEqual(reads, [5, 30, 60, 90])
        self.assertEqual(self.logs[-1], "boys-room: power off 1 of 3, then 5s off, 30s off, 60s off, 90s off")

    def test_bounce_back_stops_the_cast_again_and_retries(self):
        self.app = self.DASHCAST

        def power(t):
            sent = [x for x, what in self.events if what == "key off"]
            if len(sent) == 1 and t - sent[0] >= 30:
                self.app = self.DASHCAST  # the receiver woke the TV back up
                return "on"
            return "off"

        self.power = power
        self.off()
        names = self.names()
        self.assertEqual(names.count("key off"), 2)
        self.assertEqual(names.count("catt stop"), 2)
        second_off = len(names) - 1 - names[::-1].index("key off")
        self.assertLess(len(names) - 1 - names[::-1].index("catt stop"), second_off)
        self.assertIn("boys-room: power off 1 of 3, then 5s off, 30s on", self.logs)
        self.assertTrue(self.logs[-1].startswith("boys-room: power off 2 of 3"))
        self.assertFalse(any(m.startswith("ERROR") for m in self.logs))

    def test_three_bounces_log_error_and_raise(self):
        self.power = lambda t: "on"
        with self.assertRaises(kt.OffDidNotStick):
            self.off()
        self.assertEqual(self.names().count("key off"), kt.OFF_SENDS)
        self.assertEqual(kt.OFF_SENDS, 3)
        self.assertEqual(self.logs[-1], "ERROR: boys-room: still on after 3 power-offs")

    def test_stop_wait_is_bounded(self):
        self.app = self.DASHCAST

        def stubborn(catt, *args):
            self.events.append((self.now, "catt " + " ".join(args)))
            self.now += 1.0
            return mock.Mock(returncode=0, stdout=json.dumps({"app_id": self.DASHCAST}), stderr="")

        with mock.patch.object(self.tv, "_catt", stubborn):
            self.off()
        first_off = [t for t, what in self.events if what == "key off"][0]
        self.assertLessEqual(first_off, kt.STOP_WAIT_SECONDS + 3)
        self.assertTrue(any("not idle" in m for m in self.logs))

    def test_manual_off_that_does_not_stick_exits_nonzero(self):
        log = Path(self.tmp.name, "kids-tv.log")
        with mock.patch.object(kt.Tv, "power_off_verified", side_effect=kt.OffDidNotStick("boys-room: still on")), \
                mock.patch.object(kt.Tv, "cast_uptime", return_value="1 s"), mock.patch("sys.stderr"):
            rc = kt.main(["--config-dir", self.tmp.name, "--log", str(log), "off", "--tv", "boys-room"])
        self.assertEqual(rc, 1)

    def test_tick_never_repeats_an_off_that_did_not_stick(self):
        state = Path(self.tmp.name, "state.json")
        logs = []
        offs = []

        def off(name):
            offs.append(name)
            raise kt.OffDidNotStick(name + ": still on after 3 power-offs")

        actions = {"on": lambda n: None, "cast": lambda n: None, "off": off}
        kt.tick(SCHEDULE, at(THU, 6, 0), state, actions, logs.append)
        failures = [kt.tick(SCHEDULE, at(THU, 7, 10) + dt.timedelta(minutes=m), state, actions, logs.append)
                    for m in range(0, 25)]
        self.assertEqual(offs, ["boys-room", "girls-room"])
        self.assertEqual(failures[0], 2)
        self.assertEqual(sum(failures[1:]), 0)
        saved = json.loads(state.read_text())["tvs"]
        self.assertTrue(saved["boys-room"]["off_done"])


# ------------------------------------------------------------ follow-up tests

IP = "10.9.8.7"


def tv_dir(tmp, keys=("boys-room", "girls-room")):
    for key in keys:
        prefix = "VIZIO_%s_" % key.upper().replace("-", "_")
        Path(tmp, key + ".env").write_text("%sIP=%s\n%sTOKEN=t0ken\n" % (prefix, IP, prefix))


class Resp:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


class ErrorDetailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tv_dir(self.tmp.name)
        self.tv = kt.Tv("boys-room", self.tmp.name)

    def test_describe_error_has_errno_and_strerror(self):
        exc = urllib.error.URLError(OSError(65, "No route to host"))
        self.assertEqual(kt.describe_error(exc), "URLError(OSError): [Errno 65] No route to host")
        self.assertEqual(kt.describe_error(ConnectionRefusedError(61, "Connection refused")),
                         "ConnectionRefusedError: [Errno 61] Connection refused")
        self.assertEqual(kt.describe_error(subprocess.TimeoutExpired(["catt", "-d", IP], 30)),
                         "TimeoutExpired: no answer in 30 s")

    def test_request_logs_the_full_reason_and_no_address(self):
        exc = urllib.error.URLError(OSError(65, "No route to host"))
        with mock.patch("urllib.request.urlopen", side_effect=exc):
            with self.assertRaises(kt.TvUnreachable) as ctx:
                self.tv.power_key("on")
        self.assertIn("[Errno 65] No route to host", str(ctx.exception))
        self.assertNotIn(IP, str(ctx.exception))

    def test_unreachable_versus_an_answer(self):
        self.assertTrue(kt.is_unreachable(urllib.error.URLError(ConnectionRefusedError(61, "refused"))))
        self.assertTrue(kt.is_unreachable(urllib.error.URLError("timed out")))
        self.assertTrue(kt.is_unreachable(ConnectionResetError()))
        self.assertFalse(kt.is_unreachable(urllib.error.HTTPError("u", 403, "Forbidden", {}, None)))
        self.assertFalse(kt.is_unreachable(ValueError("bad json")))
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 403, "Forbidden", {}, None)):
            with self.assertRaises(kt.TvError) as ctx:
                self.tv.power_key("on")
        self.assertNotIsInstance(ctx.exception, kt.TvUnreachable)
        self.assertIn("HTTP 403", str(ctx.exception))

    def test_catt_failure_carries_its_stderr_tail_with_any_address_redacted(self):
        bad = mock.Mock(returncode=1, stdout="", stderr="trace\\nNo device at 192.168.7.50 or %s\\n" % IP)
        with mock.patch.object(kt.subprocess, "run", return_value=bad):
            with self.assertRaises(kt.TvError) as ctx:
                self.tv.cast_app("/fake/catt")
        text = str(ctx.exception)
        self.assertIn("exit 1", text)
        self.assertIn("No device at <ip>", text)
        self.assertNotIn("192.168", text)
        self.assertNotIn(IP, text)

    def test_catt_timeout_is_named(self):
        with mock.patch.object(kt.subprocess, "run", side_effect=subprocess.TimeoutExpired(["catt", IP], 30)):
            with self.assertRaises(kt.TvError) as ctx:
                self.tv.cast_app("/fake/catt")
        self.assertIn("TimeoutExpired: no answer in 30 s", str(ctx.exception))
        self.assertNotIn(IP, str(ctx.exception))

    def test_ipv6_short_forms_are_redacted_and_times_are_not(self):
        for text in ("fe80::1", "::1", "fe80::1%en0", "2001:db8::8a2e:370:7334", "2001:db8:0:0:0:0:2:1",
                     "::ffff:10.1.2.3", "fe80::1%en0.", "10.1.2.3"):
            self.assertEqual(kt.redact_ips("at %s now" % text), "at <ip>%s now" % ("." if text.endswith(".") else ""), text)
        for text in ("06:00:00", "2026-11-02T06:00:00-08:00", "ON_145 06:00", "a:b", "07:30:01.123"):
            self.assertEqual(kt.redact_ips(text), text)

    def test_http_client_errors_are_tv_errors(self):
        import http.client
        with mock.patch("urllib.request.urlopen", side_effect=http.client.RemoteDisconnected("closed")):
            with self.assertRaises(kt.TvUnreachable):
                self.tv.power_key("on")
        with mock.patch("urllib.request.urlopen", side_effect=http.client.BadStatusLine("x")):
            with self.assertRaises(kt.TvError) as ctx:
                self.tv.power_key("on")
        self.assertIn("BadStatusLine", str(ctx.exception))

class PowerModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tv_dir(self.tmp.name)
        self.tv = kt.Tv("boys-room", self.tmp.name)

    def test_odd_answers_read_unknown(self):
        cases = [({"ITEMS": [{"VALUE": 1}]}, "on"), ({"ITEMS": [{"VALUE": 0}]}, "off"), ({}, "unknown"),
                 ({"ITEMS": "x"}, "unknown"), ({"ITEMS": [5]}, "unknown"), ({"ITEMS": []}, "unknown"),
                 ({"ITEMS": [{"VALUE": "1"}]}, "unknown"), ({"ITEMS": [{"VALUE": 2}]}, "unknown")]
        for payload, want in cases:
            with mock.patch.object(self.tv, "_request", return_value=payload):
                self.assertEqual(self.tv.power_mode(), want, payload)

    def test_a_non_object_answer_is_an_error(self):
        with mock.patch("urllib.request.urlopen", return_value=Resp(b"[1]")):
            with self.assertRaises(kt.TvError):
                self.tv.power_mode()


class OffVerifyFollowUpTests(OffSticksTests):
    """Unknown is not off, never re-sends; cast status failing during the stop-wait."""

    def test_unreachable_readings_never_resend_and_do_not_count_as_off(self):
        def unreachable():
            self.events.append((self.now, "read failed"))
            raise kt.TvUnreachable("boys-room: request failed: URLError(OSError): [Errno 65] No route to host")

        with mock.patch.object(self.tv, "power_mode", unreachable):
            with self.assertRaises(kt.OffDidNotStick) as ctx:
                self.off()
        self.assertEqual(self.names().count("key off"), 1)
        self.assertIn("not confirmed off", str(ctx.exception))
        self.assertEqual(self.logs[-2], "boys-room: power off 1 of 3, then 5s unknown, 30s unknown, 60s unknown, 90s unknown")
        self.assertTrue(self.logs[-1].startswith("ERROR: boys-room: not confirmed off"))

    def test_unknown_reading_then_off_is_off(self):
        self.power = lambda t: "unknown" if t < 40 else "off"
        self.off()
        self.assertEqual(self.names().count("key off"), 1)
        self.assertFalse(any(m.startswith("ERROR") for m in self.logs))

    def test_unknown_after_an_on_bounce_stops_resending(self):
        def power(t):
            sent = [x for x, what in self.events if what == "key off"]
            return "on" if len(sent) == 1 else "unknown"

        self.power = power
        with self.assertRaises(kt.OffDidNotStick):
            self.off()
        self.assertEqual(self.names().count("key off"), 2)

    def test_cast_status_timing_out_during_stop_wait_still_powers_off(self):
        self.app = self.DASHCAST
        calls = {"n": 0}

        def slow(catt, *args):
            self.events.append((self.now, "catt " + " ".join(args)))
            self.now += 1.0
            calls["n"] += 1
            if args[0] == "info" and calls["n"] > 1:
                raise subprocess.TimeoutExpired(["catt", IP, "info"], 30)
            return mock.Mock(returncode=0, stdout=json.dumps({"app_id": self.DASHCAST}), stderr="")

        with mock.patch.object(self.tv, "_catt", slow):
            self.off()
        self.assertEqual(self.names().count("key off"), 1)
        line = [m for m in self.logs if "not idle" in m][0]
        self.assertIn("no answer in 30 s", line)
        self.assertNotIn(IP, " ".join(self.logs))

    def test_cast_status_failing_before_and_during_stop_wait_still_powers_off(self):
        def broken(catt, *args):
            self.events.append((self.now, "catt " + " ".join(args)))
            self.now += 1.0
            if args[0] == "info":
                return mock.Mock(returncode=2, stdout="", stderr="Error: device %s not found" % IP)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.tv, "_catt", broken):
            self.off()
        names = self.names()
        self.assertLess(names.index("catt stop"), names.index("key off"))
        self.assertTrue(any("stopping anyway" in m and "exit 2" in m for m in self.logs))
        self.assertTrue(any("not idle" in m and "exit 2" in m for m in self.logs))
        self.assertNotIn(IP, " ".join(self.logs))


class ManualOffAllTests(unittest.TestCase):
    def test_off_all_keeps_going_logs_once_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            tv_dir(tmp)
            log = Path(tmp, "kids-tv.log")
            tried = []

            def off(self_tv, catt, log_fn):
                tried.append(self_tv.name)
                if self_tv.name == "boys-room":
                    log_fn("ERROR: boys-room: still on after 3 power-offs")
                    raise kt.OffDidNotStick("boys-room: still on after 3 power-offs")

            with mock.patch.object(kt.Tv, "power_off_verified", off), \
                    mock.patch.object(kt.Tv, "cast_uptime", return_value="5 s"), \
                    mock.patch("sys.stderr"), mock.patch("builtins.print"):
                rc = kt.main(["--config-dir", tmp, "--log", str(log), "off", "--tv", "all"])
            self.assertEqual(rc, 1)
            self.assertEqual(tried, ["boys-room", "girls-room"])
            text = log.read_text()
            self.assertEqual(text.count("still on"), 1)
            self.assertIn("girls-room: manual off sent", text)

    def test_off_all_goes_on_past_an_unreachable_first_tv(self):
        with tempfile.TemporaryDirectory() as tmp:
            tv_dir(tmp)
            log = Path(tmp, "kids-tv.log")
            tried = []

            def off(self_tv, catt, log_fn):
                tried.append(self_tv.name)
                if self_tv.name == "boys-room":
                    raise kt.TvUnreachable("boys-room: request failed: URLError(OSError): [Errno 64] Host is down")

            with mock.patch.object(kt.Tv, "power_off_verified", off), \
                    mock.patch.object(kt.Tv, "cast_uptime", return_value="5 s"), \
                    mock.patch("sys.stderr"), mock.patch("builtins.print"):
                rc = kt.main(["--config-dir", tmp, "--log", str(log), "--state", tmp + "/s.json",
                              "off", "--tv", "all"])
            self.assertEqual(rc, 1)
            self.assertEqual(tried, ["boys-room", "girls-room"])
            text = log.read_text()
            self.assertEqual(text.count("Host is down"), 1)
            self.assertIn("ERROR: boys-room: request failed", text)
            self.assertIn("girls-room: manual off sent", text)

    def test_on_all_skips_the_cast_of_a_tv_whose_on_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tv_dir(tmp)
            sent = []

            def key(self_tv, state):
                if self_tv.name == "boys-room":
                    raise kt.TvError("boys-room: TV answered BLOCKED")
                sent.append((self_tv.name, "on"))

            with mock.patch.object(kt.Tv, "power_key", key), \
                    mock.patch.object(kt.Tv, "cast", lambda self_tv, catt: sent.append((self_tv.name, "cast"))), \
                    mock.patch.object(kt.Tv, "cast_uptime", return_value="5 s"), \
                    mock.patch("sys.stderr"), mock.patch("builtins.print"):
                rc = kt.main(["--config-dir", tmp, "--log", tmp + "/l.log", "--state", tmp + "/s.json",
                              "on", "--tv", "all"])
            self.assertEqual(rc, 1)
            self.assertEqual(sent, [("girls-room", "on"), ("girls-room", "cast")])

    def test_make_actions_uses_the_log_it_is_given(self):
        logs = []
        args = mock.Mock(config_dir="/nonexistent", catt="/fake/catt", log="/nonexistent/should-not-be-used.log")
        with mock.patch.object(kt, "Log", side_effect=AssertionError("a second Log")), \
                mock.patch.object(kt.Tv, "__init__", lambda self, name, d, log=None: setattr(self, "name", name)), \
                mock.patch.object(kt.Tv, "cast_uptime", return_value="42 s"), \
                mock.patch.object(kt.Tv, "power_key"), \
                mock.patch.object(kt.Tv, "power_off_verified", lambda self, catt, log: log("off verified")):
            actions = kt.make_actions(args, logs.append)
            actions["on"]("girls-room")
            actions["off"]("girls-room")
        self.assertEqual(logs, ["girls-room: cast uptime at on: 42 s", "girls-room: cast uptime at off: 42 s",
                                "off verified"])


class UptimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tv_dir(self.tmp.name)
        self.tv = kt.Tv("girls-room", self.tmp.name)

    def test_reads_only_the_uptime_field(self):
        body = json.dumps({"uptime": 223.9, "name": "a room", "ssid": "home"}).encode()
        seen = {}

        def fake(url, timeout):
            seen["url"] = url
            return Resp(body)

        with mock.patch("urllib.request.urlopen", fake):
            self.assertEqual(self.tv.cast_uptime(), "223 s")
        self.assertTrue(seen["url"].endswith(":8008/setup/eureka_info"))

    def test_unreadable_uptime_never_raises_and_has_no_address(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError(OSError(61, "Connection refused"))):
            text = self.tv.cast_uptime()
        self.assertIn("[Errno 61] Connection refused", text)
        self.assertNotIn(IP, text)
        with mock.patch("urllib.request.urlopen", return_value=Resp(b'{"name": "x"}')):
            self.assertEqual(self.tv.cast_uptime(), "unreadable (no uptime field)")


class MdnsTests(unittest.TestCase):
    OUT = (b"Lookup Girls._viziocast._tcp.local\n"
           b" 7:30:01.123  Girls._viziocast._tcp.local. can be reached at Girls-tv.local.:7345 (interface 7)\n")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tv_dir(self.tmp.name)
        self.addCleanup(kt.TV_MDNS.clear)

    GET = (b"Timestamp     A/R  Flags  IF  Hostname         Address    TTL\n"
           b" 7:30:02.001  Add  40000002  7  Girls-tv.local.  10.1.1.9   120\n")

    def test_lookup_parses_dns_sd_after_its_timeout(self):
        def fake(cmd, **kw):
            self.assertEqual(kw["timeout"], kt.MDNS_SECONDS)
            raise subprocess.TimeoutExpired(cmd, 4, output=self.OUT if cmd[1] == "-L" else self.GET)

        with mock.patch.object(kt.subprocess, "run", side_effect=fake) as run, \
                mock.patch.object(kt.socket, "gethostbyname", side_effect=AssertionError("unbounded lookup")):
            self.assertEqual(kt.mdns_lookup("Girls"), "10.1.1.9")
        self.assertEqual([c[0][0] for c in run.call_args_list],
                         [["dns-sd", "-L", "Girls", "_viziocast._tcp", "local"], ["dns-sd", "-G", "v4", "Girls-tv.local"]])

    def test_host_lookup_that_finds_nothing_in_time(self):
        with mock.patch.object(kt.subprocess, "run",
                               side_effect=lambda cmd, **kw: (_ for _ in ()).throw(
                                   subprocess.TimeoutExpired(cmd, 4, output=self.OUT if cmd[1] == "-L" else b""))):
            self.assertIsNone(kt.mdns_lookup("Girls"))

    def test_lookup_finds_nothing(self):
        with mock.patch.object(kt.subprocess, "run", side_effect=subprocess.TimeoutExpired(["dns-sd"], 4, output=b"")):
            self.assertIsNone(kt.mdns_lookup("Girls"))
        with mock.patch.object(kt.subprocess, "run", side_effect=OSError()):
            self.assertIsNone(kt.mdns_lookup("Girls"))

    def test_unreachable_tv_is_found_by_name_and_retried_there(self):
        kt.apply_local_config({"tvs": {"girls-room": {"env_file": "girls-room.env", "prefix": "VIZIO_GIRLS_ROOM_",
                                                      "mdns_name": "Girls"}}})
        logs = []
        tv = kt.Tv("girls-room", self.tmp.name, logs.append)
        hosts = []

        def fake(req, timeout, context):
            hosts.append(req.host)
            if req.host.startswith(IP):
                raise urllib.error.URLError(OSError(65, "No route to host"))
            return Resp(b'{"STATUS": {"RESULT": "SUCCESS"}}')

        with mock.patch("urllib.request.urlopen", fake), \
                mock.patch.object(kt, "mdns_lookup", return_value="10.1.1.9") as lookup:
            tv.power_key("on")
        lookup.assert_called_once_with("Girls")
        self.assertEqual(hosts, [IP + ":7345", "10.1.1.9:7345"])
        self.assertEqual(logs, ["girls-room: no answer at the configured address; using the address mDNS found"])

    def test_no_name_means_no_lookup(self):
        tv = kt.Tv("girls-room", self.tmp.name)
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError(OSError(65, "No route"))), \
                mock.patch.object(kt, "mdns_lookup") as lookup:
            with self.assertRaises(kt.TvUnreachable):
                tv.power_key("on")
        lookup.assert_not_called()

    def test_bad_mdns_name_in_config_is_an_error(self):
        with self.assertRaises(kt.ScheduleError):
            kt.apply_local_config({"tvs": {"girls-room": {"env_file": "g.env", "prefix": "G_", "mdns_name": ""}}})


class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tv_dir(self.tmp.name)

    def hold(self, name):
        """Hold a TV's lock as another kids-tv.py would (flock is per open file)."""
        import fcntl
        handle = open(Path(self.tmp.name, "kids-tv-%s.lock" % name), "a")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(handle.close)
        return handle

    def test_lock_is_exclusive_and_released(self):
        with kt.tv_lock(self.tmp.name, "boys-room") as first:
            self.assertTrue(first)
            with kt.tv_lock(self.tmp.name, "boys-room") as second:
                self.assertFalse(second)
            with kt.tv_lock(self.tmp.name, "girls-room") as other:
                self.assertTrue(other)
        with kt.tv_lock(self.tmp.name, "boys-room") as again:
            self.assertTrue(again)

    def test_tick_skips_a_locked_tv_and_does_it_next_minute(self):
        state = Path(self.tmp.name, "state.json")
        logs = []
        rec = Recorder()
        handle = self.hold("boys-room")
        kt.tick(SCHEDULE, at(THU, 6, 0), state, rec.actions(), logs.append)
        self.assertEqual(rec.calls, [("on", "girls-room"), ("cast", "girls-room")])
        self.assertIn("boys-room: another kids-tv command is using this TV; on skipped this tick", logs)
        handle.close()
        kt.tick(SCHEDULE, at(THU, 6, 1), state, rec.actions(), logs.append)
        self.assertEqual(rec.calls[2:], [("on", "boys-room"), ("cast", "boys-room")])

    def test_manual_command_waits_then_gives_up_and_still_does_the_other_tv(self):
        self.hold("boys-room")
        clock = {"t": 0.0}

        def sleep(seconds):
            clock["t"] += seconds

        log = Path(self.tmp.name, "l.log")
        with mock.patch.object(kt.time, "monotonic", lambda: clock["t"]), \
                mock.patch.object(kt.time, "sleep", sleep), \
                mock.patch.object(kt.Tv, "power_off_verified", lambda self_tv, catt, log: None), \
                mock.patch.object(kt.Tv, "cast_uptime", return_value="5 s"), \
                mock.patch("sys.stderr"), mock.patch("builtins.print"):
            rc = kt.main(["--config-dir", self.tmp.name, "--log", str(log), "--state",
                          str(Path(self.tmp.name, "s.json")), "off", "--tv", "all"])
        self.assertEqual(rc, 1)
        self.assertGreaterEqual(clock["t"], kt.LOCK_WAIT_SECONDS)
        self.assertLess(clock["t"], kt.LOCK_WAIT_SECONDS + 2)
        text = log.read_text()
        self.assertIn("ERROR: boys-room: another kids-tv command kept this TV busy for 120 s; manual off not sent", text)
        self.assertIn("girls-room: manual off sent", text)

    def test_manual_command_gets_the_lock_once_it_is_free(self):
        handle = self.hold("girls-room")
        clock = {"t": 0.0}

        def sleep(seconds):
            clock["t"] += seconds
            if clock["t"] >= 5:
                handle.close()

        with mock.patch.object(kt.time, "monotonic", lambda: clock["t"]), mock.patch.object(kt.time, "sleep", sleep), \
                mock.patch.object(kt.Tv, "power_mode", return_value="on"), mock.patch("builtins.print") as shown:
            rc = kt.main(["--config-dir", self.tmp.name, "--log", self.tmp.name + "/l.log", "--state",
                          self.tmp.name + "/s.json", "status", "--tv", "girls-room"])
        self.assertEqual(rc, 0)
        shown.assert_any_call("girls-room: on")


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.alert = Path(self.tmp.name, "sub", "kids-tv-alert.json")
        self.state = Path(self.tmp.name, "state.json")
        self.logs = []

    def alerts(self):
        return json.loads(self.alert.read_text())["alerts"] if self.alert.exists() else []

    def test_path_is_overridable_and_defaults_outside_the_repo(self):
        with mock.patch.dict(os.environ, {"KIDS_TV_ALERT": str(self.alert)}):
            self.assertEqual(kt.default_alert_path(), self.alert)
        with mock.patch.dict(os.environ):
            os.environ.pop("KIDS_TV_ALERT", None)
            path = kt.default_alert_path()
        self.assertTrue(str(path).endswith("Library/Application Support/morning-board/kids-tv-alert.json"))
        self.assertNotIn(str(ROOT), str(path))

    def test_once_per_tv_step_and_day(self):
        now = at(THU, 6, 0)
        for _ in range(3):
            kt.write_alert(self.alert, THU, "girls-room", "on", "girls-room: request failed", now, self.logs.append)
        kt.write_alert(self.alert, THU, "girls-room", "off", "girls-room: still on", now, self.logs.append)
        kt.write_alert(self.alert, FRI, "girls-room", "on", "girls-room: request failed", now, self.logs.append)
        self.assertEqual([(a["date"], a["tv"], a["step"]) for a in self.alerts()],
                         [("2026-10-29", "girls-room", "on"), ("2026-10-29", "girls-room", "off"),
                          ("2026-10-30", "girls-room", "on")])
        self.assertEqual(set(self.alerts()[0]), {"date", "tv", "step", "error", "at"})
        self.assertEqual(len([m for m in self.logs if m.startswith("ALERT: ")]), 3)
        self.assertFalse(Path(self.tmp.name, "sub", "kids-tv-alert.tmp").exists())

    def test_damaged_file_starts_over_and_unwritable_is_noted(self):
        self.alert.parent.mkdir()
        self.alert.write_text("{nope")
        kt.write_alert(self.alert, THU, "boys-room", "on", "x", at(THU, 6, 0), self.logs.append)
        self.assertEqual(len(self.alerts()), 1)
        blocker = Path(self.tmp.name, "afile")
        blocker.write_text("x")
        kt.write_alert(Path(blocker, "a.json"), THU, "boys-room", "on", "x", at(THU, 6, 0), self.logs.append)
        self.assertIn("alert file not written", self.logs[-1])

    def tick(self, now, actions):
        return kt.tick(SCHEDULE, now, self.state, actions, self.logs.append, alert_path=self.alert)

    def test_first_failed_on_alerts_at_once_and_only_once(self):
        rec = Recorder(fail={("on", "boys-room")})
        self.tick(at(THU, 6, 0), rec.actions())
        self.assertEqual([(a["tv"], a["step"]) for a in self.alerts()], [("boys-room", "on")])
        self.assertIn("boom", self.alerts()[0]["error"])
        for minute in range(1, 10):
            self.tick(at(THU, 6, minute), rec.actions())
        self.assertEqual(len(self.alerts()), 1)
        self.assertEqual(len([c for c in rec.calls if c == ("on", "boys-room")]), kt.MAX_ATTEMPTS)

    def test_off_that_does_not_stick_alerts(self):
        def off(name):
            raise kt.OffDidNotStick(name + ": still on after 3 power-offs")

        actions = {"on": lambda n: None, "cast": lambda n: None, "off": off}
        self.tick(at(THU, 6, 0), actions)
        self.tick(at(THU, 7, 10), actions)
        self.assertEqual([(a["tv"], a["step"]) for a in self.alerts()], [("boys-room", "off"), ("girls-room", "off")])

    def test_restarting_tv_is_retried_for_4_minutes_before_the_alert(self):
        tries = []

        def on(name):
            tries.append(name)
            if name == "girls-room":
                raise kt.TvUnreachable("girls-room: request failed: URLError(ConnectionRefusedError): [Errno 61] refused")

        actions = {"on": on, "cast": lambda n: None, "off": lambda n: None}
        for minute in range(0, 4):
            self.tick(at(THU, 6, minute), actions)
        self.assertEqual(self.alerts(), [])
        self.assertEqual(tries.count("girls-room"), 4)
        self.assertTrue(all("maybe restarting" in m for m in self.logs if "girls-room: on failed" in m))
        self.tick(at(THU, 6, 4), actions)
        self.assertEqual([(a["tv"], a["step"]) for a in self.alerts()], [("girls-room", "on")])
        self.assertIn("try 1 of 4", [m for m in self.logs if "girls-room: on failed" in m][-1])
        for minute in range(5, 12):
            self.tick(at(THU, 6, minute), actions)
        self.assertEqual(len(self.alerts()), 1)
        self.assertEqual(tries.count("girls-room"), 4 + kt.MAX_ATTEMPTS)

    def test_restart_that_comes_back_in_time_sends_no_alert(self):
        state = {"n": 0}

        def on(name):
            if name == "boys-room":
                state["n"] += 1
                if state["n"] < 3:
                    raise kt.TvUnreachable("boys-room: request failed: URLError(OSError): [Errno 65] No route to host")

        cast = []
        actions = {"on": on, "cast": cast.append, "off": lambda n: None}
        for minute in range(0, 4):
            self.tick(at(THU, 6, minute), actions)
        self.assertEqual(self.alerts(), [])
        self.assertIn("boys-room", cast)


class PlanZoneNitTests(unittest.TestCase):
    def test_plan_reads_times_in_the_schedule_zone_like_tick(self):
        seen = []
        real = kt.plan_for

        def spy(schedule, day, now=None):
            seen.append(now)
            return real(schedule, day, now)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(kt, "plan_for", spy), \
                mock.patch("builtins.print"):
            sched = Path(tmp, "schedule.json")
            sched.write_text(json.dumps(SCHEDULE))
            kt.main(["--schedule", str(sched), "--log", tmp + "/l.log", "plan", "--date", "2026-10-29"])
        self.assertEqual(str(seen[0].tzinfo), "America/Los_Angeles")

    def test_naive_generated_at_is_schedule_zone_time(self):
        now = dt.datetime(2026, 10, 29, 6, 0, tzinfo=LA)
        stamp = kt.parse_generated_at("2026-10-28T20:15:00", now)
        self.assertEqual(stamp, dt.datetime(2026, 10, 28, 20, 15, tzinfo=LA))


# ------------------------------------------------------------------- Sonos

BOYS_UID = "RINCON_BOYS000000000001400"
GIRLS_UID = "RINCON_GIRLS00000000001400"
OTHER_UID = "RINCON_OTHER00000000001400"
QUEUE = "file:///jffs/settings/savedqueues.rsq#0"
ALBUM = "x-rincon-cpcontainer:album-made-up"
DIDL = '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"><item><dc:title>x &amp; y</dc:title></item></DIDL-Lite>'


def backup_doc():
    def alarm(aid, room, uid, uri, volume, linked):
        return {"alarm_id": aid, "room": room, "zone_uid": uid, "start_time": "06:00:00", "recurrence": "ON_145",
                "enabled": False, "volume": volume, "program_uri": uri, "program_metadata_title": "t",
                "program_metadata": DIDL, "duration": "01:00:00", "include_linked_zones": linked,
                "play_mode": "SHUFFLE", "note": "made up"}
    return {"zones": [], "alarms": [alarm("18", "Room One", BOYS_UID, QUEUE, 13, False),
                                    alarm("5", "Room Two", GIRLS_UID, ALBUM, 10, True)]}


def listed(aid, uid, uri, enabled="0", start="06:00:00", rec="ON_145", volume="10", linked="1"):
    return {"ID": aid, "StartTime": start, "Duration": "01:00:00", "Recurrence": rec, "Enabled": enabled,
            "RoomUUID": uid, "ProgramURI": uri, "ProgramMetaData": DIDL, "PlayMode": "SHUFFLE",
            "Volume": volume, "IncludeLinkedZones": linked}


class FakeSonos:
    """Answers SOAP calls like a Sonos household, from a list of alarms. No network."""

    def __init__(self, alarms, transport="PLAYING", play_works=True, ignore_writes=False):
        self.alarms = alarms
        self.transport = dict.fromkeys(("10.0.0.21", "10.0.0.22"), transport)
        self.play_works = play_works
        self.ignore_writes = ignore_writes
        self.calls = []
        self.next_id = 40
        self.start_key = "StartTime"  # current firmware lists StartLocalTime

    def answer(self, action, inner):
        return Resp(('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                     '<u:%sResponse xmlns:u="urn:x">%s</u:%sResponse></s:Body></s:Envelope>'
                     % (action, inner, action)).encode())

    def urlopen(self, req, timeout=None):
        action = req.get_header("Soapaction").strip('"').split("#")[1]
        body = ET.fromstring(req.data)
        args = {}
        for el in body.iter():
            if el.tag.endswith("}" + action):
                args = {c.tag: c.text or "" for c in el}
        host = req.host.split(":")[0]
        self.calls.append((action, args))
        if action == "ListAlarms":
            xml = "<Alarms>%s</Alarms>" % "".join(
                "<Alarm %s/>" % " ".join("%s=%s" % (k, quoteattr(v)) for k, v in a.items()) for a in self.alarms)
            return self.answer(action, "<CurrentAlarmList>%s</CurrentAlarmList><CurrentAlarmListVersion>v</CurrentAlarmListVersion>" % escape(xml))
        if action in ("CreateAlarm", "UpdateAlarm"):
            if not self.ignore_writes:
                fields = {(self.start_key if k == "StartLocalTime" else k): v for k, v in args.items()}
                if action == "CreateAlarm":
                    fields["ID"] = str(self.next_id)
                    self.alarms.append(fields)
                else:
                    for a in self.alarms:
                        if a["ID"] == args["ID"]:
                            a.update(fields)
            return self.answer(action, "<AssignedID>%d</AssignedID>" % self.next_id if action == "CreateAlarm" else "")
        if action == "GetTransportInfo":
            return self.answer(action, "<CurrentTransportState>%s</CurrentTransportState>" % self.transport[host])
        if action == "Play":
            if self.play_works:
                self.transport[host] = "PLAYING"
            return self.answer(action, "")
        raise AssertionError(action)

    def writes(self):
        return [(a, args) for a, args in self.calls if a in ("CreateAlarm", "UpdateAlarm", "Play")]


class SonosTests(unittest.TestCase):
    SUN = dt.date(2026, 11, 1)
    MON = dt.date(2026, 11, 2)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        backup = Path(self.tmp.name, "alarms-backup.json")
        backup.write_text(json.dumps(backup_doc()))
        kt.apply_sonos_config({"backup": str(backup), "rooms": {
            "boys-room": {"room": "Room One", "ip": "10.0.0.21"}, "girls-room": {"room": "Room Two", "ip": "10.0.0.22"}}})
        self.addCleanup(kt.apply_sonos_config, None)
        self.state = Path(self.tmp.name, "kids-sonos-state.json")
        self.alert = Path(self.tmp.name, "alert.json")
        self.logs = []
        self.other = listed("7", OTHER_UID, QUEUE, enabled="1", start="07:00:00", rec="DAILY")
        self.fake = FakeSonos([listed("5", GIRLS_UID, ALBUM), dict(self.other)])
        sleep = mock.patch.object(kt.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def tick(self, now, dry=False):
        with mock.patch("urllib.request.urlopen", self.fake.urlopen):
            return kt.sonos_tick(SCHEDULE, now, self.state, self.alert, self.logs.append, dry_run=dry)

    def alarm(self, uid):
        return [a for a in self.fake.alarms if a["RoomUUID"] == uid]

    def alerts(self):
        return json.loads(self.alert.read_text())["alerts"] if self.alert.exists() else []

    def owned(self):
        path = Path(self.tmp.name, "kids-sonos-alarms.json")
        return json.loads(path.read_text()) if path.exists() else {}

    def test_evening_sets_tomorrows_run_day_alarm_and_creates_the_missing_one(self):
        self.assertEqual(self.tick(dt.datetime(2026, 11, 1, 20, 29, tzinfo=LA)), 0)
        self.assertEqual(self.fake.calls, [])  # before 20:30 nothing
        self.assertEqual(self.tick(dt.datetime(2026, 11, 1, 20, 30, tzinfo=LA)), 0)
        girls = self.alarm(GIRLS_UID)
        self.assertEqual(len(girls), 1)
        self.assertEqual((girls[0]["ID"], girls[0]["Enabled"], girls[0]["StartTime"], girls[0]["Recurrence"]),
                         ("5", "1", "06:00:00", "ON_1"))  # Monday only
        self.assertEqual((girls[0]["ProgramURI"], girls[0]["Volume"], girls[0]["IncludeLinkedZones"]), (ALBUM, "10", "1"))
        boys = self.alarm(BOYS_UID)
        self.assertEqual(len(boys), 1)
        self.assertEqual({k: boys[0][k] for k in ("Enabled", "StartTime", "Recurrence", "ProgramURI", "Volume",
                                                  "Duration", "PlayMode", "IncludeLinkedZones", "ProgramMetaData")},
                         {"Enabled": "1", "StartTime": "06:00:00", "Recurrence": "ON_1", "ProgramURI": QUEUE,
                          "Volume": "13", "Duration": "01:00:00", "PlayMode": "SHUFFLE", "IncludeLinkedZones": "0",
                          "ProgramMetaData": DIDL})
        self.assertEqual(self.alarm(OTHER_UID), [self.other])  # another room's alarm is never changed
        updates = [args for a, args in self.fake.calls if a == "UpdateAlarm"]
        self.assertEqual([u["ID"] for u in updates], ["5"])
        # every write is followed by a re-read
        actions = [a for a, _ in self.fake.calls]
        for i, name in enumerate(actions):
            if name in ("CreateAlarm", "UpdateAlarm"):
                self.assertEqual(actions[i + 1], "ListAlarms")
        self.assertIn("boys-room: sonos alarm for 2026-11-02: created from the backup (ID 40), re-read: on 06:00 ON_1",
                      self.logs)
        self.assertIn("girls-room: sonos alarm for 2026-11-02: updated (ID 5), re-read: on 06:00 ON_1", self.logs)
        owned = self.owned()
        self.assertEqual((owned["boys-room"]["id"], owned["boys-room"]["from"]), ("40", "created"))
        self.assertEqual((owned["girls-room"]["id"], owned["girls-room"]["from"]), ("5", "backup"))

    def fresh_evening(self, when=dt.datetime(2026, 11, 1, 20, 30, tzinfo=LA)):
        if self.state.exists():
            self.state.unlink()
        self.fake.calls.clear()
        return self.tick(when)

    def test_a_created_alarm_that_is_later_deleted_alerts_and_is_never_recreated(self):
        self.fresh_evening()
        self.fake.alarms = [a for a in self.fake.alarms if a["ID"] != "40"]
        self.fresh_evening()
        self.assertNotIn("CreateAlarm", [a for a, _ in self.fake.calls])
        self.assertEqual(self.alarm(BOYS_UID), [])
        boys = [a for a in self.alerts() if a["tv"] == "boys-room" and a["step"] == "sonos-sync"]
        self.assertEqual(len(boys), 1)
        self.assertIn("alarm 40 that this job owns is gone from the room; not creating another", boys[0]["error"])

    def test_only_the_owned_alarm_id_is_touched(self):
        # Another alarm in the girls' room with the same source, and one in the boys' room: never written.
        twin = listed("9", GIRLS_UID, ALBUM, enabled="0")
        stray = listed("12", BOYS_UID, QUEUE, enabled="0")
        self.fake.alarms += [dict(twin), dict(stray)]
        self.fresh_evening()
        self.assertEqual([a for a in self.fake.alarms if a["ID"] == "9"], [twin])
        self.assertEqual([a for a in self.fake.alarms if a["ID"] == "12"], [stray])
        self.assertEqual(sorted(args["ID"] for a, args in self.fake.calls if a == "UpdateAlarm"), ["5"])

    def test_the_backup_alarm_gone_after_it_was_adopted_alerts(self):
        self.fresh_evening()
        self.fake.alarms = [a for a in self.fake.alarms if a["ID"] != "5"]
        self.fresh_evening()
        self.assertEqual(self.alarm(GIRLS_UID), [])
        self.assertIn("alarm 5 that this job owns is gone",
                      [a for a in self.alerts() if a["tv"] == "girls-room"][0]["error"])

    def test_configured_alarm_id_wins_and_is_never_replaced(self):
        kt.sonos_config["rooms"]["girls-room"]["alarm_id"] = "9"
        self.fake.alarms.append(listed("9", GIRLS_UID, ALBUM, enabled="0"))
        self.fresh_evening()
        self.assertEqual([args["ID"] for a, args in self.fake.calls if a == "UpdateAlarm"], ["9"])
        self.assertEqual(self.alarm(GIRLS_UID)[0]["Enabled"], "0")  # alarm 5 left alone
        self.fake.alarms = [a for a in self.fake.alarms if a["ID"] != "9"]
        self.fresh_evening()
        self.assertEqual([a for a, _ in self.fake.calls if a in ("UpdateAlarm", "CreateAlarm")],
                         [])  # boys' alarm already right; girls' configured alarm gone: alert only
        self.assertIn("alarm 9 that this job owns is gone",
                      [a for a in self.alerts() if a["tv"] == "girls-room"][0]["error"])

    def test_an_unconfirmed_create_is_never_repeated(self):
        self.fake.next_id = 0
        orig = self.fake.answer
        self.fake.answer = lambda action, inner: orig(action, "<AssignedID></AssignedID>" if action == "CreateAlarm" else inner)
        self.fresh_evening()
        self.assertEqual(self.owned()["boys-room"].keys(), {"creating"})
        self.fresh_evening()
        self.assertEqual([a for a, _ in self.fake.calls].count("CreateAlarm"), 0)
        self.assertTrue(any("never confirmed" in m for m in self.logs))

    def test_non_run_day_with_no_owned_alarm_creates_nothing(self):
        self.fake.alarms = [dict(self.other)]
        self.fresh_evening(dt.datetime(2026, 11, 2, 20, 30, tzinfo=LA))  # Tuesday is no board day
        self.assertEqual(self.fake.writes(), [])
        self.assertEqual(self.owned(), {})

    def test_evening_sync_is_once_a_day_and_idempotent(self):
        self.tick(dt.datetime(2026, 11, 1, 20, 30, tzinfo=LA))
        self.fake.calls.clear()
        self.tick(dt.datetime(2026, 11, 1, 20, 31, tzinfo=LA))
        self.assertEqual(self.fake.calls, [])
        self.state.unlink()  # a fresh state: it reads, finds both right, and writes nothing
        self.tick(dt.datetime(2026, 11, 1, 21, 0, tzinfo=LA))
        self.assertEqual([a for a, _ in self.fake.calls], ["ListAlarms", "ListAlarms"])
        self.assertTrue(any("already on 06:00 ON_1" in m for m in self.logs))

    def test_current_firmware_spelling_of_the_start_time(self):
        self.fake.start_key = "StartLocalTime"
        for alarm in self.fake.alarms:
            alarm["StartLocalTime"] = alarm.pop("StartTime")
        self.tick(dt.datetime(2026, 11, 1, 20, 30, tzinfo=LA))
        girls = self.alarm(GIRLS_UID)[0]
        self.assertEqual((girls["StartLocalTime"], girls["Recurrence"], girls["Enabled"]), ("06:00:00", "ON_1", "1"))
        self.state.unlink()
        self.fake.calls.clear()
        self.tick(dt.datetime(2026, 11, 1, 21, 0, tzinfo=LA))
        self.assertEqual([a for a, _ in self.fake.calls], ["ListAlarms", "ListAlarms"])

    def test_friday_uses_its_own_time_and_weekday(self):
        self.tick(dt.datetime(2026, 10, 29, 20, 30, tzinfo=LA))
        girls = self.alarm(GIRLS_UID)[0]
        self.assertEqual((girls["StartTime"], girls["Recurrence"], girls["Enabled"]), ("06:30:00", "ON_5", "1"))

    def test_non_run_day_turns_it_off_and_a_missing_alarm_stays_missing(self):
        self.fake.alarms[0].update(Enabled="1", Recurrence="ON_1")
        self.tick(dt.datetime(2026, 11, 2, 20, 30, tzinfo=LA))  # Monday evening: Tuesday is no board day
        girls = self.alarm(GIRLS_UID)[0]
        self.assertEqual((girls["Enabled"], girls["Recurrence"], girls["StartTime"]), ("0", "ON_1", "06:00:00"))
        self.assertEqual(self.alarm(BOYS_UID), [])
        self.assertNotIn("CreateAlarm", [a for a, _ in self.fake.calls])
        self.assertEqual(self.alarm(OTHER_UID), [self.other])

    def test_run_days_false_and_skip_list_turn_it_off(self):
        self.fake.alarms[0].update(Enabled="1", Recurrence="ON_1")
        doc = Path(self.tmp.name, "rd.json")
        doc.write_text(json.dumps(run_days_doc(CLOCK.isoformat(), dict(every_day_runs(), **{"2026-11-02": False}))))
        with mock.patch.dict(os.environ, {"KIDS_TV_RUN_DAYS": str(doc)}):
            self.tick(dt.datetime(2026, 11, 1, 20, 30, tzinfo=LA))
        self.assertEqual(self.alarm(GIRLS_UID)[0]["Enabled"], "0")

    def test_undecidable_tomorrow_is_retried_until_23_then_alerts_once(self):
        doc = Path(self.tmp.name, "rd.json")
        doc.write_text(json.dumps(run_days_doc("2026-10-01T20:15:00-07:00", every_day_runs())))
        with mock.patch.dict(os.environ, {"KIDS_TV_RUN_DAYS": str(doc)}):
            for hour, minute in ((20, 30), (20, 31), (21, 45), (22, 59)):
                self.assertEqual(self.tick(dt.datetime(2026, 11, 1, hour, minute, tzinfo=LA)), 0)
            self.assertEqual(self.alerts(), [])
            self.assertEqual(len([m for m in self.logs if "cannot tell yet" in m]), 2)  # once per room
            failed = self.tick(dt.datetime(2026, 11, 1, 23, 0, tzinfo=LA))
            self.tick(dt.datetime(2026, 11, 1, 23, 1, tzinfo=LA))
        self.assertEqual(failed, 2)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(sorted((a["tv"], a["step"]) for a in self.alerts()),
                         [("boys-room", "sonos-sync"), ("girls-room", "sonos-sync")])
        self.assertIn("stale", self.alerts()[0]["error"])
        self.assertIn("by 23:00", self.alerts()[0]["error"])

    def test_undecidable_tomorrow_that_becomes_clear_before_23_is_synced(self):
        doc = Path(self.tmp.name, "rd.json")
        doc.write_text(json.dumps(run_days_doc("2026-10-01T20:15:00-07:00", every_day_runs())))
        with mock.patch.dict(os.environ, {"KIDS_TV_RUN_DAYS": str(doc)}):
            self.tick(dt.datetime(2026, 11, 1, 20, 30, tzinfo=LA))
            doc.write_text(json.dumps(run_days_doc("2026-11-01T21:00:00-08:00", every_day_runs())))
            self.tick(dt.datetime(2026, 11, 1, 21, 5, tzinfo=LA))
        self.assertEqual(self.alerts(), [])
        self.assertEqual(self.alarm(GIRLS_UID)[0]["Enabled"], "1")

    def test_reread_that_does_not_match_fails_retries_and_alerts_once(self):
        self.fake.ignore_writes = True
        for minute in range(30, 40):
            self.tick(dt.datetime(2026, 11, 1, 20, minute, tzinfo=LA))
        updates = [a for a, _ in self.fake.calls if a == "UpdateAlarm"]
        self.assertEqual(len(updates), kt.MAX_ATTEMPTS)
        self.assertTrue(any("re-read does not match" in m for m in self.logs))
        self.assertEqual([a["tv"] for a in self.alerts() if a["step"] == "sonos-sync"], ["boys-room", "girls-room"])

    def run_morning(self, readings, minutes=range(0, 6)):
        """readings: room address -> list of states, one per read; the last repeats."""
        seq = {ip: list(v) for ip, v in readings.items()}
        orig = self.fake.urlopen

        def urlopen(req, timeout=None):
            host = req.host.split(":")[0]
            if req.get_header("Soapaction").endswith('#GetTransportInfo"') and host in seq:
                states = seq[host]
                self.fake.transport[host] = states.pop(0) if len(states) > 1 else states[0]
            return orig(req, timeout)

        self.fake.urlopen = urlopen
        for minute in minutes:
            self.tick(dt.datetime(2026, 11, 2, 6, minute, tzinfo=LA))

    def calls_to(self, action):
        return [a for a, _ in self.fake.calls if a == action]

    def test_morning_room_that_never_played_gets_one_play_and_an_alert(self):
        self.run_morning({"10.0.0.22": ["STOPPED"], "10.0.0.21": ["PLAYING"]})
        self.assertEqual(len(self.calls_to("Play")), 1)
        self.assertEqual([(a["tv"], a["step"]) for a in self.alerts()], [("girls-room", "sonos")])
        self.assertIn("did not play in the 2 min after alarm", self.alerts()[0]["error"])
        # girls: 06:00, 06:01, 06:02 reads, then one more after Play; boys: one read at 06:00
        self.assertEqual(len(self.calls_to("GetTransportInfo")), 5)

    def test_morning_room_that_played_then_stopped_is_left_alone(self):
        self.run_morning({"10.0.0.22": ["PAUSED_PLAYBACK", "PLAYING", "STOPPED"], "10.0.0.21": ["PLAYING"]})
        self.assertEqual(self.calls_to("Play"), [])
        self.assertEqual(self.alerts(), [])
        self.assertEqual(len(self.calls_to("GetTransportInfo")), 3)  # read until seen playing, then never again
        state = json.loads(self.state.read_text())
        self.assertEqual(set(state["check"]["girls-room"]), {"ok"})

    def test_morning_room_still_playing_is_read_once(self):
        self.run_morning({"10.0.0.22": ["PLAYING"], "10.0.0.21": ["PLAYING"]})
        self.assertEqual(self.calls_to("Play"), [])
        self.assertEqual(self.alerts(), [])
        self.assertEqual(len(self.calls_to("GetTransportInfo")), 2)
        self.assertIn("girls-room: sonos playing after alarm", self.logs)

    def test_morning_failed_reads_count_as_not_playing(self):
        self.run_morning({"10.0.0.22": ["STOPPED"]}, minutes=[1])
        self.assertEqual(self.calls_to("Play"), [])  # still inside the window
        self.run_morning({"10.0.0.22": ["STOPPED"]}, minutes=[2])
        self.assertEqual(len(self.calls_to("Play")), 1)

    def test_morning_check_only_on_run_days(self):
        self.fake.transport["10.0.0.22"] = "STOPPED"
        self.tick(dt.datetime(2026, 10, 27, 6, 2, tzinfo=LA))  # Tuesday: no board, no check
        self.assertEqual(self.fake.calls, [])
        self.tick(dt.datetime(2026, 10, 30, 6, 32, tzinfo=LA))  # Friday: alarm 06:30, first tick after the window
        self.assertEqual([a for a, _ in self.fake.calls], ["GetTransportInfo", "GetTransportInfo", "Play",
                                                           "GetTransportInfo"])

    def test_http_client_errors_are_sonos_errors_and_never_break_the_tick(self):
        import http.client
        with mock.patch("urllib.request.urlopen", side_effect=http.client.IncompleteRead(b"")):
            with self.assertRaises(kt.SonosError) as ctx:
                kt.Speaker("girls-room", "10.0.0.22").list_alarms()
        self.assertIn("IncompleteRead", str(ctx.exception))
        reports = []
        with mock.patch("urllib.request.urlopen", self.fake.urlopen), \
                mock.patch.object(kt, "room_is_playing", side_effect=http.client.HTTPException("odd")):
            failed = kt.sonos_tick(SCHEDULE, dt.datetime(2026, 11, 2, 6, 2, tzinfo=LA), self.state, self.alert,
                                   self.logs.append, report=reports.append)
        self.assertEqual(failed, 1)
        self.assertEqual(reports, ["sonos: HTTPException: odd"])

    def test_unreachable_speaker_alerts_without_its_address(self):
        def down(req, timeout=None):
            raise urllib.error.URLError(OSError(64, "Host is down"))

        with mock.patch("urllib.request.urlopen", down):
            failed = kt.sonos_tick(SCHEDULE, dt.datetime(2026, 11, 2, 6, 2, tzinfo=LA), self.state, self.alert,
                                   self.logs.append)
        self.assertEqual(failed, 2)
        errors = [a["error"] for a in self.alerts()]
        self.assertIn("[Errno 64] Host is down", errors[0])
        self.assertIn("did not play", errors[0])
        self.assertNotIn("10.0.0.2", " ".join(errors + self.logs))

    def test_upnp_fault_is_named(self):
        def fault(req, timeout=None):
            raise urllib.error.HTTPError("u", 500, "Internal Server Error", {},
                                         io.BytesIO(b"<s:Fault><detail><UPnPError><errorCode>402</errorCode></UPnPError></detail></s:Fault>"))

        with mock.patch("urllib.request.urlopen", fault):
            with self.assertRaises(kt.SonosError) as ctx:
                kt.Speaker("girls-room", "10.0.0.22").list_alarms()
        self.assertEqual(str(ctx.exception), "girls-room: ListAlarms failed: HTTP 500 UPnP error 402")

    def test_dry_run_prints_and_sends_nothing(self):
        with mock.patch("builtins.print") as shown:
            self.tick(dt.datetime(2026, 11, 1, 20, 30, tzinfo=LA), dry=True)
            self.tick(dt.datetime(2026, 11, 2, 6, 2, tzinfo=LA), dry=True)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(self.state.exists())
        shown.assert_any_call("girls-room: would set the Sonos alarm for 2026-11-02: on 06:00")
        shown.assert_any_call("boys-room: would check the Sonos room is playing")

    def test_without_sonos_config_nothing_runs(self):
        kt.apply_sonos_config(None)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            self.assertEqual(kt.sonos_tick(SCHEDULE, dt.datetime(2026, 11, 1, 20, 30, tzinfo=LA), self.state,
                                           self.alert, self.logs.append), 0)

    def test_damaged_state_does_nothing_today(self):
        self.state.write_text("{garbage")
        errors = []
        with mock.patch("urllib.request.urlopen", self.fake.urlopen):
            kt.sonos_tick(SCHEDULE, dt.datetime(2026, 11, 1, 20, 30, tzinfo=LA), self.state, self.alert,
                          self.logs.append, report=errors.append)
            kt.sonos_tick(SCHEDULE, dt.datetime(2026, 11, 1, 20, 31, tzinfo=LA), self.state, self.alert,
                          self.logs.append, report=errors.append)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(len(errors), 1)

    def test_config_validation(self):
        for bad in ({"rooms": {}}, {"backup": "b", "rooms": {"kitchen": {"room": "K", "ip": "x"}}},
                    {"backup": "b", "rooms": {"boys-room": {"room": "", "ip": "x"}}}, "yes"):
            with self.assertRaises(kt.ScheduleError, msg=bad):
                kt.apply_sonos_config(bad)

    def test_sonos_day_digits(self):
        self.assertEqual([kt.sonos_day(self.SUN + dt.timedelta(days=i)) for i in range(7)],
                         ["0", "1", "2", "3", "4", "5", "6"])

    def test_tick_runs_sonos_even_when_the_tv_part_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            sched = Path(tmp, "schedule.json")
            sched.write_text(json.dumps(SCHEDULE))
            with mock.patch.object(kt, "tick", side_effect=kt.ScheduleError("boom")), \
                    mock.patch.object(kt, "sonos_tick", return_value=0) as sonos, \
                    mock.patch.object(kt, "apply_local_config"), mock.patch("sys.stderr"):
                rc = kt.main(["--schedule", str(sched), "--log", tmp + "/l.log", "--state", tmp + "/s.json",
                              "--alert", tmp + "/a.json", "tick", "--now", "2026-11-01T20:30:00"])
        self.assertEqual(rc, 1)
        sonos.assert_called_once()
        self.assertEqual(sonos.call_args[0][2], Path(tmp, "kids-sonos-state.json"))
        self.assertEqual(sonos.call_args[0][3], tmp + "/a.json")


class TestIsolationTests(unittest.TestCase):
    def test_default_paths_are_not_the_real_home(self):
        args = kt.build_parser().parse_args(["status"])
        for path in (args.state, args.log, args.config_dir):
            self.assertTrue(str(path).startswith(_NO_SKIP_DIR.name), path)


class PlistTests(unittest.TestCase):
    def test_plist_runs_tick_each_minute_and_is_parseable(self):
        path = ROOT / "launchd" / "com.davidcasas.morningboard.kids-tv.plist"
        data = plistlib.loads(path.read_bytes())
        self.assertEqual(data["Label"], "com.davidcasas.morningboard.kids-tv")
        self.assertEqual(data["ProgramArguments"][1], "__REPO__/scripts/kids-tv.py")
        self.assertEqual(data["ProgramArguments"][-1], "tick")
        self.assertEqual(data["StartInterval"], 60)
        self.assertTrue(data["RunAtLoad"])


if __name__ == "__main__":
    unittest.main()
