#!/usr/bin/env python3
"""Checks for the kids' TV job. No TV, no network, no real tokens."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
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


_NO_SKIP_DIR = tempfile.TemporaryDirectory()


def setUpModule():
    # Other tests must not read the repo's real skip file or the mini's real local config.
    os.environ["KIDS_TV_SKIP"] = str(Path(_NO_SKIP_DIR.name, "none.json"))
    os.environ["KIDS_TV_CONFIG"] = str(Path(_NO_SKIP_DIR.name, "no-config.json"))


def tearDownModule():
    os.environ.pop("KIDS_TV_SKIP", None)
    os.environ.pop("KIDS_TV_CONFIG", None)
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
        self.assertEqual(len([m for m in self.logs if m.startswith("error:")]), 1)

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
        self.assertEqual(len([m for m in self.logs if m.startswith("error:")]), 1)
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
