"""Wrappers around the external tools dnsctl shells out to: dnscontrol,
git, and gh. REPO_ROOT here is this package's own repo checkout - identical
to the REPO_ROOT computed in dnsctl.py, just derived independently to avoid
a circular import."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from .cli_utils import eprint

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def exe_name(name: str) -> str:
    return f"{name}.exe" if platform.system() == "Windows" else name


def find_dnscontrol() -> str | None:
    found = shutil.which("dnscontrol")
    if found:
        return found
    # go install puts binaries in $GOBIN or $GOPATH/bin, which may not be on
    # PATH yet in this shell session - check the common locations directly.
    candidates = []
    gopath = os.environ.get("GOPATH")
    home = Path.home()
    if gopath:
        candidates.append(Path(gopath) / "bin" / exe_name("dnscontrol"))
    candidates.append(home / "go" / "bin" / exe_name("dnscontrol"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def run_dnscontrol(args: list[str], env_overrides: dict) -> int:
    dnscontrol = find_dnscontrol()
    if not dnscontrol:
        eprint(
            "error: dnscontrol not found on PATH.\n"
            "  Run: python scripts/dnsctl.py install-dnscontrol\n"
            "  or:  go install github.com/DNSControl/dnscontrol/v4@latest"
        )
        return 1

    env = os.environ.copy()
    env.update(env_overrides)

    proc = subprocess.run([dnscontrol, *args], cwd=REPO_ROOT, env=env)
    return proc.returncode


def have_gh() -> bool:
    return shutil.which("gh") is not None


def require_gh() -> bool:
    if have_gh():
        return True
    eprint("error: GitHub CLI ('gh') not found on PATH. Install it: https://cli.github.com/")
    return False


def git(args: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=capture, text=capture
    )


def git_output(args: list[str]) -> str:
    return git(args, capture=True).stdout.strip()


def gh(args: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args], cwd=REPO_ROOT, capture_output=capture, text=capture
    )
