"""MorningBoardTVHelper.app: Info.plist, the launchd templates, and the built launcher."""
from __future__ import annotations

import importlib.util
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INFO = ROOT / "helper" / "Info.plist"
BUILD = ROOT / "scripts" / "build-kids-tv-helper.sh"
BUNDLE_ID = "com.davidcasas.morningboard.kidstv-helper"
APP_EXE = "__HOME__/Applications/MorningBoardTVHelper.app/Contents/MacOS/kids-tv-helper"
TEMPLATES = {
    "tick": ROOT / "launchd" / "com.davidcasas.morningboard.kids-tv.plist",
    "netcheck": ROOT / "launchd" / "com.davidcasas.morningboard.kids-tv-helper-netcheck.plist",
    "castcheck": ROOT / "launchd" / "com.davidcasas.morningboard.kids-tv-helper-castcheck.plist",
}

spec = importlib.util.spec_from_file_location("kids_tv_for_helper", ROOT / "scripts" / "kids-tv.py")
kt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kt)

CAN_BUILD = sys.platform == "darwin" and all(shutil.which(t) for t in ("clang", "codesign", "plutil"))


def info():
    return plistlib.loads(INFO.read_bytes())


def substitute(text: str, repo: str, home: str) -> str:
    """The README's install step: sed s|__REPO__|...| and s|__HOME__|...|."""
    return text.replace("__REPO__", repo).replace("__HOME__", home)


class InfoPlistTests(unittest.TestCase):
    def test_identity_keys(self):
        data = info()
        self.assertEqual(data["CFBundleIdentifier"], BUNDLE_ID)
        self.assertEqual(data["CFBundleExecutable"], "kids-tv-helper")
        self.assertEqual(data["CFBundlePackageType"], "APPL")
        self.assertIs(data["LSUIElement"], True)

    def test_local_network_description_is_set(self):
        self.assertGreater(len(info()["NSLocalNetworkUsageDescription"].strip()), 20)

    def test_bonjour_services_are_the_ones_the_script_resolves(self):
        # The script resolves a TV by dns-sd -L on MDNS_SERVICE; it never browses _googlecast.
        self.assertEqual(info()["NSBonjourServices"], [kt.MDNS_SERVICE])


class TemplateTests(unittest.TestCase):
    def test_all_run_the_app_executable_on_kids_tv_py(self):
        for name, path in TEMPLATES.items():
            with self.subTest(name):
                data = plistlib.loads(path.read_bytes())
                self.assertEqual(data["ProgramArguments"][:2], [APP_EXE, "__REPO__/scripts/kids-tv.py"])
                self.assertTrue(APP_EXE.endswith("/Contents/MacOS/" + info()["CFBundleExecutable"]))
                if name != "tick":  # the live job keeps exactly the keys it had; see the test below
                    self.assertEqual(data["AssociatedBundleIdentifiers"], [BUNDLE_ID])
                self.assertTrue(data["RunAtLoad"])
                self.assertTrue(data["StandardOutPath"].startswith("__HOME__/Library/Logs/"))
                self.assertTrue(data["StandardErrorPath"].startswith("__HOME__/Library/Logs/"))

    def test_live_job_runs_through_the_helper_and_keeps_every_other_key(self):
        data = plistlib.loads(TEMPLATES["tick"].read_bytes())
        self.assertEqual(data, {
            "Label": "com.davidcasas.morningboard.kids-tv",
            "ProgramArguments": [APP_EXE, "__REPO__/scripts/kids-tv.py", "tick"],
            "RunAtLoad": True,
            "StartInterval": 60,
            "StandardOutPath": "__HOME__/Library/Logs/kids-tv-morning-board.out",
            "StandardErrorPath": "__HOME__/Library/Logs/kids-tv-morning-board.err",
        })

    def test_netcheck_template_runs_once_with_a_settle(self):
        data = plistlib.loads(TEMPLATES["netcheck"].read_bytes())
        self.assertEqual(data["ProgramArguments"][2:],
                         ["--log", "__HOME__/Library/Logs/kids-tv-helper-netcheck.log", "netcheck", "--settle", "180"])
        self.assertNotIn("StartInterval", data)
        self.assertNotIn("KeepAlive", data)

    def test_castcheck_template_runs_the_castcheck_once_away_from_the_live_files(self):
        plain = plistlib.loads((ROOT / "launchd" / "com.davidcasas.morningboard.kids-tv-castcheck.plist").read_bytes())
        data = plistlib.loads(TEMPLATES["castcheck"].read_bytes())
        self.assertEqual(data["ProgramArguments"][2:],
                         ["--log", "__HOME__/Library/Logs/kids-tv-helper-castcheck.log"] + plain["ProgramArguments"][2:])
        self.assertEqual(data["EnvironmentVariables"],
                         {"KIDS_TV_ALERT": "__HOME__/Library/Logs/kids-tv-helper-castcheck-alert.json"})
        self.assertNotIn("StartInterval", data)
        self.assertNotIn("KeepAlive", data)

    def test_substitution_leaves_no_placeholder_and_still_parses(self):
        for name, path in TEMPLATES.items():
            with self.subTest(name):
                out = substitute(path.read_text(encoding="utf-8"), "/Users/u/code/morning-board", "/Users/u")
                self.assertNotIn("__REPO__", out)
                self.assertNotIn("__HOME__", out)
                data = plistlib.loads(out.encode("utf-8"))
                self.assertEqual(data["ProgramArguments"][0],
                                 "/Users/u/Applications/MorningBoardTVHelper.app/Contents/MacOS/kids-tv-helper")
                self.assertEqual(data["ProgramArguments"][1], "/Users/u/code/morning-board/scripts/kids-tv.py")


@unittest.skipUnless(CAN_BUILD, "needs macOS with clang, codesign and plutil")
class BuiltHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = Path(cls.tmp.name, "a", "MorningBoardTVHelper.app")
        cls.cdhash = cls.build(Path(cls.tmp.name, "a"))
        cls.exe = cls.app / "Contents" / "MacOS" / "kids-tv-helper"
        cls.script = Path(cls.tmp.name, "scripts", "kids-tv.py")
        cls.script.parent.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def build(out: Path) -> str:
        done = subprocess.run([str(BUILD), str(out)], capture_output=True, text=True, timeout=120)
        if done.returncode != 0:
            raise AssertionError(done.stdout + done.stderr)
        return [l.split()[1] for l in done.stdout.splitlines() if l.startswith("cdhash ")][0]

    def fake_script(self, body: str) -> str:
        self.script.write_text(body, encoding="utf-8")
        return str(self.script)

    def run_helper(self, *args, timeout=20):
        return subprocess.run([str(self.exe)] + list(args), capture_output=True, text=True, timeout=timeout)

    def test_signed_ad_hoc_with_the_bundle_identifier(self):
        done = subprocess.run(["codesign", "-dv", "--verbose=4", str(self.app)], capture_output=True, text=True)
        self.assertIn("Identifier=" + BUNDLE_ID, done.stderr)
        self.assertIn("Signature=adhoc", done.stderr)
        self.assertIn("flags=0x10002(adhoc,runtime)", done.stderr)
        verify = subprocess.run(["codesign", "--verify", "--strict", str(self.app)], capture_output=True, text=True)
        self.assertEqual(verify.returncode, 0, verify.stderr)

    def test_rebuild_elsewhere_gives_the_same_cdhash(self):
        self.assertEqual(self.build(Path(self.tmp.name, "b")), self.cdhash)

    def test_runs_python_on_the_script_and_returns_its_status(self):
        script = self.fake_script("import sys\nprint(sys.executable)\nprint(sys.argv[1:])\nsys.exit(3)\n")
        done = self.run_helper(script, "alerts", "--json")
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertIn("['alerts', '--json']", done.stdout)

    def test_python_is_a_child_not_an_exec(self):
        script = self.fake_script("import os\nprint(os.getppid())\n")
        proc = subprocess.Popen([str(self.exe), script], stdout=subprocess.PIPE, text=True)
        out, _ = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(int(out.strip()), proc.pid)

    def test_refuses_anything_but_an_absolute_kids_tv_py(self):
        self.assertEqual(self.run_helper().returncode, 64)
        self.assertEqual(self.run_helper("/bin/ls").returncode, 64)
        self.assertEqual(self.run_helper("scripts/kids-tv.py").returncode, 64)
        self.assertEqual(self.run_helper(str(Path(self.tmp.name, "kids-tv.py.bak"))).returncode, 64)

    def test_sigterm_reaches_the_child(self):
        script = self.fake_script("import sys, time\nprint('up', flush=True)\ntime.sleep(30)\n")
        proc = subprocess.Popen([str(self.exe), script], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "up")
            started = time.monotonic()
            proc.send_signal(signal.SIGTERM)
            self.assertEqual(proc.wait(timeout=10), 128 + signal.SIGTERM)
            self.assertLess(time.monotonic() - started, 10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()


if __name__ == "__main__":
    unittest.main()
