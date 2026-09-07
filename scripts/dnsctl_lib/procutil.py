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

# Timeouts (seconds) for subprocess calls that can hang on a network operation
# or another external process, so a stuck git/gh/dnscontrol call blocks the
# CLI for a bounded time instead of forever. Most git/gh subcommands used
# here are effectively instant (local status/config reads, or quick API
# calls), but fetch/pull/push and gh's API calls do touch the network, so
# every call gets the same generous ceiling rather than trying to classify
# each one.
GIT_TIMEOUT = 60
GH_TIMEOUT = 60
# dnscontrol preview/push/import all talk to the Cloudflare API for every
# configured zone, which can take longer than a single git/gh call.
DNSCONTROL_TIMEOUT = 180


def _run_with_timeout(
    cmd: list[str],
    *,
    capture: bool,
    check: bool,
    timeout: int,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Shared subprocess.run wrapper: on subprocess.TimeoutExpired, print a
    clear error (never a raw traceback) and return a CompletedProcess with a
    non-zero returncode (124, the conventional "command timed out" exit code)
    so every existing call site's `if result.returncode != 0:` handling keeps
    working unchanged."""
    try:
        return subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            env=env,
            capture_output=capture,
            text=capture,
            check=check,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        eprint(f"error: '{' '.join(cmd)}' timed out after {timeout}s.")
        return subprocess.CompletedProcess(
            cmd,
            returncode=124,
            stdout="" if capture else None,
            stderr="" if capture else None,
        )


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


def run_dnscontrol(args: list[str], env_overrides: dict, timeout: int = DNSCONTROL_TIMEOUT) -> int:
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

    proc = _run_with_timeout(
        [dnscontrol, *args], capture=False, check=False, timeout=timeout, env=env
    )
    return proc.returncode


def have_gh() -> bool:
    return shutil.which("gh") is not None


def require_gh() -> bool:
    if have_gh():
        return True
    eprint("error: GitHub CLI ('gh') not found on PATH. Install it: https://cli.github.com/")
    return False


def git(
    args: list[str],
    capture: bool = False,
    check: bool = False,
    timeout: int = GIT_TIMEOUT,
) -> subprocess.CompletedProcess:
    return _run_with_timeout(["git", *args], capture=capture, check=check, timeout=timeout)


def git_output(args: list[str]) -> str:
    return git(args, capture=True).stdout.strip()


def gh(
    args: list[str], capture: bool = True, timeout: int = GH_TIMEOUT
) -> subprocess.CompletedProcess:
    return _run_with_timeout(["gh", *args], capture=capture, check=False, timeout=timeout)


def require_clean_worktree(context: str) -> bool:
    """Guard shared by `begin`, `rollback`, and `validate` (via sync_branch's
    callers): refuse to continue if the working tree has uncommitted changes,
    rather than silently stashing or discarding them. `context` fills in the
    "...before <context>:" part of the error, since the wording differs by
    caller."""
    status = git_output(["status", "--porcelain"])
    if status:
        eprint(
            "error: you have uncommitted changes - commit, stash, or discard them "
            f"before {context}:"
        )
        eprint(status)
        return False
    return True


def sync_branch(
    branch: str,
    *,
    switch_message: str | None = None,
    fetch_message: str | None = None,
    diverged_message: str | None = None,
) -> bool:
    """Ensure `branch` is checked out locally and fast-forwarded to
    origin/`branch`: checkout (if not already on it) -> fetch -> pull
    --ff-only. Shared by `begin`, `rollback`, and `validate` (the old
    sync_local_main), which only differ in how chatty to be and in the exact
    wording of the diverged-branch error - `switch_message`/`fetch_message`
    are printed (with `{current_branch}`/`{branch}` placeholders) only if
    given, and `diverged_message` defaults to a generic message if omitted."""
    current_branch = git_output(["rev-parse", "--abbrev-ref", "HEAD"])
    if current_branch != branch:
        if switch_message:
            print(switch_message.format(current_branch=current_branch, branch=branch))
        if git(["checkout", branch]).returncode != 0:
            return False

    if fetch_message:
        print(fetch_message.format(current_branch=current_branch, branch=branch))
    if git(["fetch", "origin", branch]).returncode != 0:
        return False
    if git(["pull", "--ff-only", "origin", branch]).returncode != 0:
        eprint(diverged_message or f"error: '{branch}' could not be fast-forwarded to origin/{branch}.")
        return False
    return True
