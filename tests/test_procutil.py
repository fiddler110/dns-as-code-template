"""Tests for the git-repo-facing helpers added to dnsctl_lib/procutil.py for
TOOL-12: require_clean_worktree and sync_branch. Both wrap real git plumbing
(status/checkout/fetch/pull), so - following the same pattern as
tests/test_commit_helpers.py's `repo` fixture - these run against a real
throwaway git repo (a "work" clone of a bare "origin") rather than mocking
subprocess, with dnsctl_lib.procutil.REPO_ROOT pointed at the clone.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch, mod):
    """A bare "origin", a "seed" checkout used to push new commits to origin
    from outside the code under test, and a "work" clone - the one
    dnsctl_lib.procutil.REPO_ROOT is pointed at, i.e. the one the functions
    under test actually operate on."""
    origin_dir = tmp_path / "origin.git"
    origin_dir.mkdir()
    _git(origin_dir, "init", "-q", "--bare")

    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    _git(seed_dir, "init", "-q")
    _git(seed_dir, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(seed_dir, "config", "user.email", "test@example.com")
    _git(seed_dir, "config", "user.name", "Test User")
    _git(seed_dir, "config", "commit.gpgsign", "false")
    (seed_dir / "f.txt").write_text("v1\n", encoding="utf-8")
    _git(seed_dir, "add", "f.txt")
    _git(seed_dir, "commit", "-q", "-m", "init")
    _git(seed_dir, "remote", "add", "origin", str(origin_dir))
    _git(seed_dir, "push", "-q", "origin", "main")
    # A bare repo's HEAD defaults to whatever `init.defaultBranch` happens to
    # be configured on the machine that ran `git init --bare` (often "master"
    # on CI runners, but "main" wherever this was authored) - independent of
    # the "main" branch name this fixture actually uses. If that default
    # doesn't match, HEAD points at a ref that was never pushed, so `git
    # clone` below warns "remote HEAD refers to nonexistent ref, unable to
    # checkout" and leaves `work_dir` with a broken/unborn HEAD - as opposed
    # to just picking a different-but-consistent branch name - which made
    # `git rev-parse --abbrev-ref HEAD` fail and (since git_output() doesn't
    # check the exit code) silently return the literal string "HEAD" instead
    # of a real branch name. Fix origin's HEAD explicitly so this fixture's
    # behavior doesn't depend on any git config default.
    _git(origin_dir, "symbolic-ref", "HEAD", "refs/heads/main")

    work_dir = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin_dir), str(work_dir))
    _git(work_dir, "config", "user.email", "test@example.com")
    _git(work_dir, "config", "user.name", "Test User")
    _git(work_dir, "config", "commit.gpgsign", "false")

    monkeypatch.setattr(mod.procutil, "REPO_ROOT", work_dir)
    return {"origin": origin_dir, "seed": seed_dir, "work": work_dir}


# --- require_clean_worktree --------------------------------------------------


def test_require_clean_worktree_true_when_clean(mod, repo):
    assert mod.procutil.require_clean_worktree("doing X") is True


def test_require_clean_worktree_false_and_prints_when_dirty(mod, repo, capsys):
    (repo["work"] / "f.txt").write_text("uncommitted change\n", encoding="utf-8")

    result = mod.procutil.require_clean_worktree("doing X")

    assert result is False
    err = capsys.readouterr().err
    assert "uncommitted changes" in err
    assert "before doing X:" in err


# --- sync_branch --------------------------------------------------------------


def test_sync_branch_already_on_branch_fast_forwards(mod, repo):
    (repo["seed"] / "f.txt").write_text("v2\n", encoding="utf-8")
    _git(repo["seed"], "add", "f.txt")
    _git(repo["seed"], "commit", "-q", "-m", "v2")
    _git(repo["seed"], "push", "-q", "origin", "main")

    ok = mod.procutil.sync_branch("main")

    assert ok is True
    assert (repo["work"] / "f.txt").read_text(encoding="utf-8") == "v2\n"


def test_sync_branch_switches_branch_when_needed(mod, repo):
    _git(repo["work"], "checkout", "-q", "-b", "other")

    ok = mod.procutil.sync_branch("main")

    assert ok is True
    assert _git(repo["work"], "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_sync_branch_prints_switch_and_fetch_messages_when_given(mod, repo, capsys):
    _git(repo["work"], "checkout", "-q", "-b", "other")

    ok = mod.procutil.sync_branch(
        "main",
        switch_message="Switching from '{current_branch}' to '{branch}'...",
        fetch_message="Fetching latest '{branch}' from origin...",
    )

    assert ok is True
    out = capsys.readouterr().out
    assert "Switching from 'other' to 'main'..." in out
    assert "Fetching latest 'main' from origin..." in out


def test_sync_branch_silent_by_default(mod, repo, capsys):
    _git(repo["work"], "checkout", "-q", "-b", "other")

    ok = mod.procutil.sync_branch("main")

    assert ok is True
    out = capsys.readouterr().out
    assert out == ""


def _diverge(repo):
    (repo["work"] / "f.txt").write_text("local-only\n", encoding="utf-8")
    _git(repo["work"], "add", "f.txt")
    _git(repo["work"], "commit", "-q", "-m", "local only commit")

    (repo["seed"] / "f.txt").write_text("upstream-only\n", encoding="utf-8")
    _git(repo["seed"], "add", "f.txt")
    _git(repo["seed"], "commit", "-q", "-m", "upstream only commit")
    _git(repo["seed"], "push", "-q", "origin", "main")


def test_sync_branch_diverged_returns_false_with_default_message(mod, repo, capsys):
    _diverge(repo)

    ok = mod.procutil.sync_branch("main")

    assert ok is False
    err = capsys.readouterr().err
    assert "error: 'main' could not be fast-forwarded to origin/main." in err


def test_sync_branch_diverged_uses_custom_diverged_message(mod, repo, capsys):
    _diverge(repo)

    ok = mod.procutil.sync_branch("main", diverged_message="custom diverged message")

    assert ok is False
    assert "custom diverged message" in capsys.readouterr().err
