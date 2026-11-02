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
                if name == "castcheck":  # test windows only: it runs when kickstarted, never at load or login
                    self.assertNotIn("RunAtLoad", data)
                else:
                    self.assertIs(data["RunAtLoad"], True)
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



def readme_block(after: str) -> str:
    """The first ``` block in README.md after the line containing `after`."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    rest = text[text.index(after):]
    start = rest.index("```\n") + 4
    return rest[start:rest.index("```", start)]


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("plutil"), "needs macOS plutil")
class ReadmeInstallTests(unittest.TestCase):
    """The README's install and rollback blocks, run with HOME in a temp dir and a launchctl that only logs."""

    LIVE = "This is the step that makes it live."
    ROLLBACK = "Roll back to plain python3, as the mini account."
    P = "com.davidcasas.morningboard"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / "code").mkdir()
        (self.home / "code" / "morning-board").symlink_to(ROOT)
        self.agents = self.home / "Library" / "LaunchAgents"
        self.agents.mkdir(parents=True)
        bin_dir = self.home / "bin"
        bin_dir.mkdir()
        self.calls = self.home / "launchctl.log"
        fake = bin_dir / "launchctl"
        loaded = self.home / "launchctl.loaded"
        loaded.write_text("")
        # Like launchd: bootstrap of a loaded label fails (5), bootout of one not loaded fails (3).
        fake.write_text("""#!/bin/sh
echo "$*" >> "%s"
case "$1" in
  bootstrap) label=$(basename "$3" .plist)
             grep -qx "$label" "%s" && exit 5
             echo "$label" >> "%s" ;;
  bootout)   label=${2##*/}
             grep -qx "$label" "%s" || exit 3
             grep -vx "$label" "%s" > "%s.new"; mv "%s.new" "%s" ;;
esac
exit 0
""" % ((self.calls,) + (loaded,) * 7))
        self.loaded = loaded
        fake.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home), PATH="%s:/usr/bin:/bin" % bin_dir)

    def run_block(self, after):
        done = subprocess.run(["/bin/bash", "-e", "-c", readme_block(after)], env=self.env,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done

    def agent(self, name):
        return plistlib.loads((self.agents / ("%s.%s.plist" % (self.P, name))).read_bytes())

    def test_install_loads_job_and_helper_netcheck_and_removes_the_plain_netcheck(self):
        (self.agents / ("%s.kids-tv-netcheck.plist" % self.P)).write_text("old")
        self.loaded.write_text("%s.kids-tv-netcheck\n" % self.P)  # the old plain netcheck, loaded
        for _ in range(2):  # running it again changes nothing
            self.run_block(self.LIVE)
            self.assertEqual(sorted(f.name for f in self.agents.iterdir()),
                             ["%s.kids-tv-helper-netcheck.plist" % self.P, "%s.kids-tv.plist" % self.P])
        exe = str(self.home / "Applications/MorningBoardTVHelper.app/Contents/MacOS/kids-tv-helper")
        self.assertEqual(self.agent("kids-tv")["ProgramArguments"],
                         [exe, str(self.home / "code/morning-board/scripts/kids-tv.py"), "tick"])
        self.assertEqual(self.agent("kids-tv-helper-netcheck")["ProgramArguments"][0], exe)
        calls = self.calls.read_text().splitlines()
        self.assertIn("bootout gui/%d/%s.kids-tv-netcheck" % (os.getuid(), self.P), calls)
        self.assertEqual(sum(c.startswith("bootstrap ") for c in calls), 4)
        self.assertEqual(sorted(self.loaded.read_text().split()), ["%s.kids-tv" % self.P, "%s.kids-tv-helper-netcheck" % self.P])

    def test_rollback_loads_the_plain_python_job_and_netcheck(self):
        self.run_block(self.LIVE)
        self.run_block(self.ROLLBACK)
        self.assertEqual(sorted(f.name for f in self.agents.iterdir()),
                         ["%s.kids-tv-netcheck.plist" % self.P, "%s.kids-tv.plist" % self.P])
        job = self.agent("kids-tv")
        self.assertEqual(job["ProgramArguments"],
                         ["/usr/bin/python3", str(self.home / "code/morning-board/scripts/kids-tv.py"), "tick"])
        live = plistlib.loads(substitute(TEMPLATES["tick"].read_text(encoding="utf-8"),
                                         str(self.home / "code/morning-board"), str(self.home)).encode())
        self.assertEqual({k: v for k, v in job.items() if k != "ProgramArguments"},
                         {k: v for k, v in live.items() if k != "ProgramArguments"})
        self.assertEqual(self.agent("kids-tv-netcheck")["ProgramArguments"][0], "/usr/bin/python3")
        self.assertEqual(sorted(self.loaded.read_text().split()), ["%s.kids-tv" % self.P, "%s.kids-tv-netcheck" % self.P])

@unittest.skipUnless(CAN_BUILD, "needs macOS with clang, codesign and plutil")
class BuiltHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.script = Path(cls.tmp.name, "scripts", "kids-tv.py")
        cls.script.parent.mkdir()
        cls.script.write_text("", encoding="utf-8")
        cls.app = Path(cls.tmp.name, "a", "MorningBoardTVHelper.app")
        cls.cdhash = cls.build(Path(cls.tmp.name, "a"))
        cls.exe = cls.app / "Contents" / "MacOS" / "kids-tv-helper"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def build(cls, out: Path) -> str:
        done = subprocess.run([str(BUILD), str(cls.script), str(out)], capture_output=True, text=True, timeout=120)
        if done.returncode != 0:
            raise AssertionError(done.stdout + done.stderr)
        return [l.split()[1] for l in done.stdout.splitlines() if l.startswith("cdhash ")][0]

    def fake_script(self, body: str) -> str:
        self.script.write_text(body, encoding="utf-8")
        return str(self.script)

    def run_helper(self, *args, timeout=20, env=None):
        return subprocess.run([str(self.exe)] + list(args), capture_output=True, text=True, timeout=timeout, env=env)

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

    def test_refuses_anything_but_the_kids_tv_py_it_was_built_for(self):
        other = Path(self.tmp.name, "elsewhere", "kids-tv.py")
        other.parent.mkdir(exist_ok=True)
        other.write_text("print('ran the wrong script')\n", encoding="utf-8")
        self.fake_script("print('ran')\n")
        for args in ([], ["/bin/ls"], ["scripts/kids-tv.py"], [str(other)], [str(self.script) + ".bak"],
                     [str(self.script.parent) + "/./kids-tv.py"]):
            with self.subTest(args):
                done = self.run_helper(*args)
                self.assertEqual(done.returncode, 64)
                self.assertEqual(done.stdout, "")
        self.assertEqual(self.run_helper(str(self.script)).stdout, "ran\n")

    def test_build_needs_an_absolute_kids_tv_py(self):
        out = Path(self.tmp.name, "refused")
        for script in ("", "scripts/kids-tv.py", "/bin/ls", str(self.script) + '"', str(Path(self.tmp.name, "none", "kids-tv.py"))):
            with self.subTest(script):
                done = subprocess.run([str(BUILD), script, str(out)], capture_output=True, text=True, timeout=60)
                self.assertNotEqual(done.returncode, 0)
                self.assertFalse(out.exists())

    def test_python_environment_variables_are_removed(self):
        script = self.fake_script("import os\nprint(sorted(k for k in os.environ if k.startswith('PYTHON')))\n"
                                  "print(os.environ.get('KEEP_ME'))\n")
        env = dict(os.environ, PYTHONPATH=self.tmp.name, PYTHONSTARTUP="/dev/null", PYTHONHOME="/nowhere",
                   PYTHONNOUSERSITE="1", KEEP_ME="kept")
        done = self.run_helper(script, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(), ["[]", "kept"])

    def test_child_starts_with_no_blocked_signals_and_default_actions(self):
        script = self.fake_script("import signal\nprint(sorted(int(s) for s in signal.pthread_sigmask(signal.SIG_BLOCK, [])))\n"
                                  "print([signal.getsignal(s) == signal.SIG_DFL for s in (signal.SIGTERM, signal.SIGHUP)])\n")
        forwarded = {signal.SIGTERM, signal.SIGINT, signal.SIGHUP}

        def blocked_and_ignored():  # as a parent that blocks or ignores them would leave the helper
            signal.pthread_sigmask(signal.SIG_BLOCK, forwarded)
            signal.signal(signal.SIGHUP, signal.SIG_IGN)

        done = subprocess.run([str(self.exe), script], capture_output=True, text=True, timeout=20,
                              preexec_fn=blocked_and_ignored)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(), ["[]", "[True, True]"])

    def test_a_signal_while_the_child_is_spawned_is_passed_on(self):
        # A test build of the same source raises SIGTERM after blocking it, just before posix_spawn: the
        # signal must stay pending and reach the child once its pid is known, not be dropped.
        exe = Path(self.tmp.name, "spawn-window-helper")
        subprocess.run(["clang", "-Os", "-Wall", "-Wextra", "-Werror", '-DKIDS_TV_SCRIPT="%s"' % self.script,
                        "-DKIDS_TV_TEST_SIGNAL_IN_SPAWN_WINDOW=SIGTERM", "-o", str(exe),
                        str(ROOT / "helper" / "kids-tv-helper.c")], check=True, capture_output=True, timeout=120)
        script = self.fake_script("import time\ntime.sleep(5)\nprint('not stopped')\n")
        done = subprocess.run([str(exe), script], capture_output=True, text=True, timeout=20)
        self.assertEqual(done.returncode, 128 + signal.SIGTERM, done.stdout + done.stderr)
        self.assertEqual(done.stdout, "")


if __name__ == "__main__":
    unittest.main()
