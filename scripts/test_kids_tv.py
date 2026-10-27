#!/usr/bin/env python3
"""Checks for the kids' TV job. No TV, no network, no real tokens."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import plistlib
import tempfile
import unittest
import urllib.error
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
        self.assertIn("daniel: on 06:30, off 07:40", text)
        self.assertIn("girls: on 06:30, off 07:40", text)


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
        self.assertEqual(rec.calls, [("on", "daniel"), ("cast", "daniel"), ("on", "girls"), ("cast", "girls")])

    def test_no_repeat_and_no_recast_between_on_and_off(self):
        rec = Recorder()
        for minute in range(0, 70):
            self.run_tick(at(THU, 6, 0) + dt.timedelta(minutes=minute), rec)
        self.assertEqual(len(rec.calls), 4)

    def test_one_power_off_per_tv_at_on_the_road_time(self):
        rec = Recorder()
        for minute in range(0, 90):
            self.run_tick(at(THU, 6, 0) + dt.timedelta(minutes=minute), rec)
        self.assertEqual([c for c in rec.calls if c[0] == "off"], [("off", "daniel"), ("off", "girls")])
        self.assertEqual(rec.calls[-2:], [("off", "daniel"), ("off", "girls")])

    def test_friday_waits_for_its_own_times(self):
        rec = Recorder()
        self.run_tick(at(FRI, 6, 0), rec)
        self.assertEqual(rec.calls, [])
        self.run_tick(at(FRI, 6, 30), rec)
        self.assertEqual(rec.calls[0], ("on", "daniel"))
        rec.calls.clear()
        self.run_tick(at(FRI, 7, 10), rec)
        self.assertEqual(rec.calls, [])
        self.run_tick(at(FRI, 7, 40), rec)
        self.assertEqual(rec.calls, [("off", "daniel"), ("off", "girls")])

    def test_survives_a_reboot_state_is_on_disk(self):
        rec = Recorder()
        self.run_tick(at(THU, 6, 0), rec)
        rec2 = Recorder()
        self.run_tick(at(THU, 6, 5), rec2)
        self.assertEqual(rec2.calls, [])

    def test_late_boot_inside_the_window_still_turns_on(self):
        rec = Recorder()
        self.run_tick(at(THU, 6, 40), rec)
        self.assertEqual(rec.calls[0], ("on", "daniel"))

    def test_boot_after_off_time_does_nothing(self):
        rec = Recorder()
        self.run_tick(at(THU, 7, 11), rec)
        self.assertEqual(rec.calls, [])

    def test_no_power_off_if_it_never_came_on(self):
        rec = Recorder(fail={("on", "daniel")} | {("on", "girls")})
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
        rec = Recorder(fail={("on", "daniel")})
        failures = 0
        for minute in range(0, 20):
            failures += self.run_tick(at(THU, 6, 0) + dt.timedelta(minutes=minute), rec)
        self.assertEqual(len([c for c in rec.calls if c == ("on", "daniel")]), kt.MAX_ATTEMPTS)
        self.assertEqual(failures, kt.MAX_ATTEMPTS)
        self.assertEqual(len([c for c in rec.calls if c == ("on", "girls")]), 1)
        self.assertTrue(any("daniel: on failed" in m for m in self.logs))

    def test_failed_cast_retries_the_cast_only(self):
        rec = Recorder(fail={("cast", "girls")})
        self.run_tick(at(THU, 6, 0), rec)
        self.run_tick(at(THU, 6, 1), rec)
        self.assertEqual(len([c for c in rec.calls if c == ("on", "girls")]), 1)
        self.assertEqual(len([c for c in rec.calls if c == ("cast", "girls")]), 2)

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
        shown.assert_any_call("daniel: would on")


class SecretTests(unittest.TestCase):
    TOKEN = "SECRETTOKEN123"
    IP = "10.9.8.7"

    def make_tv(self, tmp):
        Path(tmp, "daniel.env").write_text(
            "VIZIO_DANIEL_IP=%s\nVIZIO_DANIEL_TOKEN='%s'\n" % (self.IP, self.TOKEN))
        return kt.Tv("daniel", tmp)

    def test_env_parse_and_missing_key_names_only_the_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_tv(tmp)
            Path(tmp, "girls.env").write_text("VIZIO_GIRLS_IP=%s\n" % self.IP)
            with self.assertRaises(kt.TvError) as ctx:
                kt.Tv("girls", tmp)
            self.assertIn("VIZIO_GIRLS_TOKEN", str(ctx.exception))
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
