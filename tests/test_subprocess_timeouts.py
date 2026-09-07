"""Tests for TOOL-15: every subprocess/procutil call that can hang on a
network operation or another external process gets a `timeout=`, and a
subprocess.TimeoutExpired is caught and turned into a clean, non-zero-exit
error - never a raw traceback. Also covers cmd_merge's `gh pr merge` call
being routed through the same require_gh() guard used by every other
gh-touching command, instead of raising an uncaught FileNotFoundError when
`gh` isn't installed.
"""
from __future__ import annotations

import argparse
import subprocess

import pytest


def _raise_timeout(cmd, **kwargs):
    raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))


# --- procutil.git()/gh()/run_dnscontrol(): TimeoutExpired handling ----------


def test_git_timeout_prints_clean_error_and_returns_nonzero(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod.procutil.subprocess, "run", _raise_timeout)

    result = mod.procutil.git(["fetch", "origin", "main"], timeout=5)

    assert result.returncode != 0
    err = capsys.readouterr().err
    assert "timed out after 5s" in err
    assert "Traceback" not in err


def test_gh_timeout_prints_clean_error_and_returns_nonzero(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod.procutil.subprocess, "run", _raise_timeout)

    result = mod.procutil.gh(["pr", "view", "1"], timeout=5)

    assert result.returncode != 0
    err = capsys.readouterr().err
    assert "timed out after 5s" in err
    assert "Traceback" not in err


def test_gh_timeout_does_not_raise(mod, monkeypatch):
    """The bug being fixed: previously nothing caught TimeoutExpired, so a
    hung `gh` process would propagate the exception instead of a clean
    error - confirm it no longer escapes at all."""
    monkeypatch.setattr(mod.procutil.subprocess, "run", _raise_timeout)

    try:
        mod.procutil.gh(["run", "watch", "123", "--exit-status"], capture=False, timeout=5)
    except subprocess.TimeoutExpired:
        pytest.fail("subprocess.TimeoutExpired was not caught")


def test_git_output_timeout_returns_empty_string_not_none(mod, monkeypatch):
    """git_output() calls .stdout.strip() unconditionally - confirm the
    timeout fallback still has a real (empty) string, not None, so that
    doesn't crash."""
    monkeypatch.setattr(mod.procutil.subprocess, "run", _raise_timeout)

    assert mod.procutil.git_output(["status", "--porcelain"]) == ""


def test_run_dnscontrol_timeout_returns_nonzero_not_raise(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod.procutil, "find_dnscontrol", lambda: "/usr/bin/dnscontrol")
    monkeypatch.setattr(mod.procutil.subprocess, "run", _raise_timeout)

    rc = mod.procutil.run_dnscontrol(["preview"], {}, timeout=5)

    assert rc != 0
    err = capsys.readouterr().err
    assert "timed out after 5s" in err


# --- cmd_doctor's direct `dnscontrol version` subprocess.run call -----------


def test_doctor_dnscontrol_version_timeout_does_not_crash(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "find_dnscontrol", lambda: "/usr/bin/dnscontrol")
    monkeypatch.setattr(mod.subprocess, "run", _raise_timeout)
    # Keep cmd_doctor from touching anything else real/network-y after the
    # dnscontrol check we're targeting.
    monkeypatch.setattr(mod, "load_env_file", lambda path: {"CLOUDFLARE_API_TOKEN": "x"})
    monkeypatch.setattr(mod.procutil, "have_gh", lambda: True)
    monkeypatch.setattr(mod, "git_output", lambda args: ".githooks")
    monkeypatch.setattr(mod.env_lib, "load_env_file", lambda path: {"CLOUDFLARE_API_TOKEN": "x"})

    rc = mod.cmd_doctor(argparse.Namespace())

    assert isinstance(rc, int)
    err = capsys.readouterr().err
    assert "timed out after 15s" in err
    assert "Traceback" not in err


# --- env.py's `az keyvault secret show` subprocess.run call -----------------


def test_fetch_keyvault_secret_timeout_returns_none_not_raise(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod.env_lib.shutil, "which", lambda name: "/usr/bin/az")
    monkeypatch.setattr(mod.env_lib.subprocess, "run", _raise_timeout)

    result = mod.env_lib.fetch_keyvault_secret("some-vault", "some-secret")

    assert result is None
    err = capsys.readouterr().err
    assert "timed out after 60s" in err
    assert "Traceback" not in err


# --- cmd_merge routed through require_gh() ----------------------------------


def test_cmd_merge_when_gh_missing_prints_clean_error_not_traceback(mod, monkeypatch, capsys):
    """Regression test for the bug TOOL-15 fixes: cmd_merge's `gh pr merge`
    call used to bypass the require_gh() guard every other gh-touching
    command uses, so a missing `gh` binary raised an uncaught
    FileNotFoundError instead of the standard clean error message."""
    monkeypatch.setattr(mod.procutil, "have_gh", lambda: False)

    def fail_if_called(*args, **kwargs):
        raise FileNotFoundError("gh not found - this should never be reached")

    monkeypatch.setattr(mod, "gh", fail_if_called)

    rc = mod.cmd_merge(argparse.Namespace(pr=1, force=False, yes=True, wait=False, timeout=300))

    assert rc == 1
    err = capsys.readouterr().err
    assert "GitHub CLI ('gh') not found on PATH" in err


def test_cmd_merge_gh_missing_matches_other_commands_guard_message(mod, monkeypatch, capsys):
    """cmd_merge's error message on a missing `gh` should be identical to
    every other require_gh()-guarded command's (e.g. cmd_status), not a
    bespoke one - confirming it really does go through the same guard."""
    monkeypatch.setattr(mod.procutil, "have_gh", lambda: False)
    monkeypatch.setattr(mod, "gh", lambda *a, **k: (_ for _ in ()).throw(AssertionError("unreachable")))

    mod.cmd_merge(argparse.Namespace(pr=1, force=False, yes=True, wait=False, timeout=300))
    merge_err = capsys.readouterr().err

    mod.cmd_status(argparse.Namespace())
    status_err = capsys.readouterr().err

    assert merge_err == status_err
