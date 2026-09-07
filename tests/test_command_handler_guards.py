"""Tests for TOOL-16: the CLI command handlers in scripts/dnsctl.py that wrap
`submit`, `begin`, `merge`, `rollback`, `preview`/`push`, and `import` - none
of these had any test coverage before, only the pure helpers they call
(confirm_or_abort, resolve_zone_arg, require_cloudflare_token,
require_clean_worktree) were tested in isolation (tests/test_cli_utils.py,
tests/test_env.py, tests/test_procutil.py).

These tests mock every git/gh/dnscontrol-touching call (git, git_output, gh,
require_gh, require_clean_worktree, procutil.sync_branch, run_dnscontrol,
load_cloudflare_env, require_cloudflare_token) at the dnsctl module level -
following the `_fake_gh_pr_view`-style faking already used in
tests/test_commit_helpers.py - so no test here ever touches a real git repo,
GitHub, or the network. The goal is to prove the *handlers* actually call
these guards and stop when they fail/refuse, not just that the guards work in
isolation.
"""
from __future__ import annotations

import argparse
import json


def _cp(returncode=0, stdout="", stderr=""):
    import subprocess

    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# --- confirm/abort paths, exercised through the command handlers -----------


def test_cmd_submit_aborts_when_diff_confirmation_declined(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "require_gh", lambda: True)
    monkeypatch.setattr(mod, "git_output", lambda args: "M dnsconfig.js")
    monkeypatch.setattr(mod, "cmd_preview", lambda _args: 0)

    git_calls = []
    monkeypatch.setattr(mod, "git", lambda args, **kw: git_calls.append(args) or _cp())
    monkeypatch.setattr(mod, "confirm_or_abort", lambda *a, **k: False)

    args = argparse.Namespace(
        message="msg", files=None, branch=None, base="main", body=None,
        skip_preview=False, yes=False,
    )

    assert mod.cmd_submit(args) == 1
    assert "Aborted." in capsys.readouterr().out
    # Aborting before the confirmation must mean no branch/commit work happened.
    assert git_calls == []


class _FakePath:
    """Stands in for mod.ENV_FILE so tests never touch a real .env on disk."""

    def is_file(self):
        return True


def test_cmd_submit_blocks_when_ticket_reference_required_and_missing(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "require_gh", lambda: True)
    monkeypatch.setattr(mod, "git_output", lambda args: "M dnsconfig.js")
    monkeypatch.setattr(mod, "ENV_FILE", _FakePath())
    monkeypatch.setattr(
        mod, "load_env_file", lambda _path: {"DNSCTL_REQUIRE_TICKET_REFERENCE": "true"}
    )

    git_calls = []
    monkeypatch.setattr(mod, "git", lambda args, **kw: git_calls.append(args) or _cp())

    args = argparse.Namespace(
        message="Add CNAME for myapp", files=None, branch=None, base="main", body=None,
        skip_preview=False, yes=False,
    )

    assert mod.cmd_submit(args) == 1
    assert "no ticket reference" in capsys.readouterr().err
    # Must fail before touching git at all, not just before pushing/PR-ing.
    assert git_calls == []


def test_cmd_submit_allows_ticket_reference_in_message(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "require_gh", lambda: True)
    monkeypatch.setattr(mod, "git_output", lambda args: "M dnsconfig.js")
    monkeypatch.setattr(mod, "ENV_FILE", _FakePath())
    monkeypatch.setattr(
        mod, "load_env_file", lambda _path: {"DNSCTL_REQUIRE_TICKET_REFERENCE": "true"}
    )
    monkeypatch.setattr(mod, "cmd_preview", lambda _args: 0)
    monkeypatch.setattr(mod, "confirm_or_abort", lambda *a, **k: False)

    args = argparse.Namespace(
        message="[INC0012345] Add CNAME for myapp", files=None, branch=None, base="main",
        body=None, skip_preview=False, yes=False,
    )

    # Ticket reference present, so it should get past the local check and
    # fail later, at the (unrelated) diff-confirmation prompt instead.
    assert mod.cmd_submit(args) == 1
    out_err = capsys.readouterr()
    assert "no ticket reference" not in out_err.err
    assert "Aborted." in out_err.out


def test_cmd_push_blocks_on_dirty_worktree(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "load_cloudflare_env", dict)
    monkeypatch.setattr(mod, "require_cloudflare_token", lambda *a, **k: True)
    monkeypatch.setattr(mod, "require_clean_worktree", lambda _ctx: False)

    run_calls = []
    monkeypatch.setattr(mod, "run_dnscontrol", lambda args, env: run_calls.append(args) or 0)

    args = argparse.Namespace(yes=False)

    assert mod.cmd_push(args) == 1
    assert run_calls == []


def test_cmd_push_aborts_when_apply_confirmation_declined(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "load_cloudflare_env", dict)
    monkeypatch.setattr(mod, "require_cloudflare_token", lambda *a, **k: True)
    monkeypatch.setattr(mod, "require_clean_worktree", lambda _ctx: True)
    monkeypatch.setattr(mod, "confirm_or_abort", lambda *a, **k: False)

    run_calls = []
    monkeypatch.setattr(mod, "run_dnscontrol", lambda args, env: run_calls.append(args) or 0)

    args = argparse.Namespace(yes=False)

    assert mod.cmd_push(args) == 1
    assert "Aborted." in capsys.readouterr().out
    assert run_calls == []


def test_cmd_merge_aborts_when_merge_confirmation_declined(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "require_gh", lambda: True)

    gh_calls = []

    def fake_gh(args, **kw):
        gh_calls.append(args)
        if args[:2] == ["pr", "view"]:
            return _cp(stdout=json.dumps({
                "state": "OPEN",
                "title": "Some change",
                "statusCheckRollup": [{"name": "DNS Preview", "conclusion": "SUCCESS"}],
            }))
        raise AssertionError(f"unexpected gh call after abort: {args}")

    monkeypatch.setattr(mod, "gh", fake_gh)
    monkeypatch.setattr(mod, "confirm_or_abort", lambda *a, **k: False)

    args = argparse.Namespace(pr=42, force=False, yes=False, wait=False, timeout=300)

    assert mod.cmd_merge(args) == 1
    assert "Aborted." in capsys.readouterr().out
    # Only the initial `pr view` call should have happened - no `pr merge`.
    assert gh_calls == [["pr", "view", "42", "--json", "state,statusCheckRollup,title,author,reviews"]]


def test_cmd_merge_blocks_when_no_preview_check_found(mod, monkeypatch, capsys):
    """A PR with no DNS Preview check at all (not yet run, or misconfigured)
    must require --force just like a failed one - it must not fall through
    to the merge confirmation on its own."""
    monkeypatch.setattr(mod, "require_gh", lambda: True)

    def fake_gh(args, **kw):
        if args[:2] == ["pr", "view"]:
            return _cp(stdout=json.dumps({
                "state": "OPEN",
                "title": "Some change",
                "statusCheckRollup": [{"name": "lint", "conclusion": "SUCCESS"}],
            }))
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(mod, "gh", fake_gh)
    monkeypatch.setattr(mod, "confirm_or_abort", lambda *a, **k: True)

    args = argparse.Namespace(pr=42, force=False, yes=False, wait=False, timeout=300)

    assert mod.cmd_merge(args) == 1
    assert "no 'DNS Preview' check found" in capsys.readouterr().err


class _FakePath:
    """Stands in for mod.ENV_FILE so tests never touch a real .env on disk."""

    def is_file(self):
        return True


def test_cmd_merge_blocks_when_non_author_review_required_and_missing(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "require_gh", lambda: True)
    monkeypatch.setattr(mod, "ENV_FILE", _FakePath())
    monkeypatch.setattr(
        mod, "load_env_file", lambda _path: {"DNSCTL_REQUIRE_NON_AUTHOR_REVIEW": "true"}
    )

    merge_called = []

    def fake_gh(args, **kw):
        if args[:2] == ["pr", "view"]:
            return _cp(stdout=json.dumps({
                "state": "OPEN",
                "title": "Some change",
                "author": {"login": "bob"},
                "reviews": [
                    {"author": {"login": "bob"}, "state": "APPROVED", "submittedAt": "2026-01-01T00:00:00Z"},
                ],
                "statusCheckRollup": [{"name": "DNS Preview", "conclusion": "SUCCESS"}],
            }))
        merge_called.append(args)
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(mod, "gh", fake_gh)

    args = argparse.Namespace(pr=42, force=False, yes=False, wait=False, timeout=300)

    assert mod.cmd_merge(args) == 1
    assert "no APPROVED review" in capsys.readouterr().err
    assert merge_called == []


def test_cmd_merge_allows_when_non_author_review_present(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "require_gh", lambda: True)
    monkeypatch.setattr(mod, "ENV_FILE", _FakePath())
    monkeypatch.setattr(
        mod, "load_env_file", lambda _path: {"DNSCTL_REQUIRE_NON_AUTHOR_REVIEW": "true"}
    )
    monkeypatch.setattr(mod, "confirm_or_abort", lambda *a, **k: False)

    def fake_gh(args, **kw):
        assert args[:2] == ["pr", "view"]
        return _cp(stdout=json.dumps({
            "state": "OPEN",
            "title": "Some change",
            "author": {"login": "bob"},
            "reviews": [
                {"author": {"login": "alice"}, "state": "APPROVED", "submittedAt": "2026-01-01T00:00:00Z"},
            ],
            "statusCheckRollup": [{"name": "DNS Preview", "conclusion": "SUCCESS"}],
        }))

    monkeypatch.setattr(mod, "gh", fake_gh)

    args = argparse.Namespace(pr=42, force=False, yes=False, wait=False, timeout=300)

    # Approval present, so it should get past the local check and fail later,
    # at the (unrelated) merge-confirmation prompt instead.
    assert mod.cmd_merge(args) == 1
    out_err = capsys.readouterr()
    assert "no APPROVED review" not in out_err.err
    assert "Aborted." in out_err.out


def test_cmd_rollback_aborts_when_diff_confirmation_declined(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "require_gh", lambda: True)
    monkeypatch.setattr(mod, "require_clean_worktree", lambda _ctx: True)
    monkeypatch.setattr(mod, "resolve_revert_target", lambda target: ("abc12345", False))
    monkeypatch.setattr(mod.procutil, "sync_branch", lambda *a, **k: True)
    monkeypatch.setattr(mod, "git_output", lambda args: "Some commit subject")

    git_calls = []

    def fake_git(args, **kw):
        git_calls.append(args)
        return _cp()

    monkeypatch.setattr(mod, "git", fake_git)
    monkeypatch.setattr(mod, "cmd_preview", lambda _args: 0)
    monkeypatch.setattr(mod, "confirm_or_abort", lambda *a, **k: False)

    args = argparse.Namespace(target="42", yes=False)

    assert mod.cmd_rollback(args) == 1
    out = capsys.readouterr().out
    assert "Aborted" in out
    assert "left in place for manual inspection" in out
    # checkout -b and revert happen before the confirmation; push must not.
    assert ["checkout", "-b", "dns/revert-abc12345"] in git_calls
    assert not any(call and call[0] == "push" for call in git_calls)


# --- clean-worktree guard, exercised through begin/rollback -----------------


def test_cmd_begin_stops_when_worktree_dirty(mod, monkeypatch):
    monkeypatch.setattr(mod, "require_gh", lambda: True)
    monkeypatch.setattr(mod, "require_clean_worktree", lambda _ctx: False)

    sync_calls = []
    monkeypatch.setattr(mod.procutil, "sync_branch", lambda *a, **k: sync_calls.append(a) or True)

    args = argparse.Namespace(base="main", branch=None, yes=False, description=None)

    assert mod.cmd_begin(args) == 1
    assert sync_calls == []


def test_cmd_rollback_stops_when_worktree_dirty(mod, monkeypatch):
    monkeypatch.setattr(mod, "require_gh", lambda: True)
    monkeypatch.setattr(mod, "require_clean_worktree", lambda _ctx: False)

    resolve_calls = []
    monkeypatch.setattr(
        mod, "resolve_revert_target", lambda target: resolve_calls.append(target) or ("x", False)
    )

    args = argparse.Namespace(target="42", yes=True)

    assert mod.cmd_rollback(args) == 1
    assert resolve_calls == []


# --- zone/token validation guards, exercised through the handlers ----------


def test_cmd_import_rejects_unknown_zone(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "load_cloudflare_env", dict)
    monkeypatch.setattr(mod, "require_cloudflare_token", lambda *a, **k: True)

    run_calls = []
    monkeypatch.setattr(mod, "run_dnscontrol", lambda args, env: run_calls.append(args) or 0)

    args = argparse.Namespace(zone="not-a-zone", out="zone_import.js")

    assert mod.cmd_import(args) == 1
    err = capsys.readouterr().err
    assert "not a zone this project manages" in err
    assert run_calls == []


def test_cmd_import_requires_cloudflare_token(mod, monkeypatch):
    monkeypatch.setattr(mod, "load_cloudflare_env", dict)
    monkeypatch.setattr(mod, "require_cloudflare_token", lambda *a, **k: False)

    run_calls = []
    monkeypatch.setattr(mod, "run_dnscontrol", lambda args, env: run_calls.append(args) or 0)

    args = argparse.Namespace(zone=None, out="zone_import.js")

    assert mod.cmd_import(args) == 1
    assert run_calls == []


def test_cmd_preview_requires_cloudflare_token(mod, monkeypatch):
    monkeypatch.setattr(mod, "load_cloudflare_env", dict)
    monkeypatch.setattr(mod, "require_cloudflare_token", lambda *a, **k: False)

    run_calls = []
    monkeypatch.setattr(mod, "run_dnscontrol", lambda args, env: run_calls.append(args) or 0)

    assert mod.cmd_preview(argparse.Namespace()) == 1
    assert run_calls == []


def test_cmd_record_update_ip_rejects_unknown_zone(mod, config_from):
    config_from("multi_zone.js")
    args = argparse.Namespace(old_ip="1.2.3.4", new_ip="5.6.7.8", zone="not-a-zone", yes=True)
    assert mod.cmd_record_update_ip(args) == 1
