#!/usr/bin/env python3
"""Fast-forward the morning board checkout.

Runs git pull --ff-only and then stops. A failed pull leaves the files
already on disk. Nothing is reset, cleaned, or deleted.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git_bin() -> str:
    if Path("/usr/bin/git").is_file():
        return "/usr/bin/git"
    return "git"


def pull_ff_only(repo, run=None) -> int:
    """Return git's exit code. On failure the checkout is left as it was."""
    repo = Path(repo).resolve()
    cmd = [
        git_bin(),
        "-C",
        str(repo),
        "-c",
        "safe.directory=" + str(repo),
        "pull",
        "--ff-only",
    ]
    if run is not None:
        completed = run(cmd)
        return int(getattr(completed, "returncode", completed))
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        completed = subprocess.run(cmd, check=False, stdin=subprocess.DEVNULL, env=env)
    except OSError:
        return 1
    return completed.returncode


def main(argv):
    repo = ROOT
    if "--repo" in argv:
        repo = Path(argv[argv.index("--repo") + 1]).expanduser()
    code = pull_ff_only(repo)
    if code == 0:
        print("pull ok", flush=True)
    else:
        print("pull failed; checkout left in place", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
