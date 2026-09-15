"""Tests for the git/forge-facing helpers in dnsctl.py that back `validate`
and `rollback`: commit_triggers_apply and resolve_revert_target. Also carries
a fetch_live_acme_snapshot regression test (a shipped NameError lived in
exactly this kind of untested path).

commit_triggers_apply/resolve_revert_target's commit-ish resolution is
exercised against a real throwaway git repo (not mocked) by pointing
dnsctl_lib.procutil.REPO_ROOT at a tmp_path repo, since procutil.git()/
git_output() always run against that module-level constant. The PR-number
path (no real forge to talk to here) is faked via the `fake_forge` fixture
(conftest.py's FakeForge).
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
    """A real, throwaway git repo with a small commit history:

        A (root) -- dnsconfig.js created
         \\
          B -- dnsconfig.js edited (triggers apply)
           \\
            C -- README.md added (does not trigger apply)
             \\
              M -- merge of feature branch D into C (two parents)

    feature branch:
        B -- D -- dnsconfig.js edited again (triggers apply)
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _git(repo_dir, "init", "-q")
    _git(repo_dir, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo_dir, "config", "user.email", "test@example.com")
    _git(repo_dir, "config", "user.name", "Test User")
    _git(repo_dir, "config", "commit.gpgsign", "false")

    monkeypatch.setattr(mod.procutil, "REPO_ROOT", repo_dir)

    (repo_dir / "dnsconfig.js").write_text("// v1\n", encoding="utf-8")
    _git(repo_dir, "add", "dnsconfig.js")
    _git(repo_dir, "commit", "-q", "-m", "A: add dnsconfig.js")
    sha_a = _git(repo_dir, "rev-parse", "HEAD")

    (repo_dir / "dnsconfig.js").write_text("// v2\n", encoding="utf-8")
    _git(repo_dir, "add", "dnsconfig.js")
    _git(repo_dir, "commit", "-q", "-m", "B: edit dnsconfig.js")
    sha_b = _git(repo_dir, "rev-parse", "HEAD")

    (repo_dir / "README.md").write_text("docs\n", encoding="utf-8")
    _git(repo_dir, "add", "README.md")
    _git(repo_dir, "commit", "-q", "-m", "C: add README")
    sha_c = _git(repo_dir, "rev-parse", "HEAD")

    _git(repo_dir, "checkout", "-q", "-b", "feature", sha_b)
    (repo_dir / "dnsconfig.js").write_text("// v3 on feature\n", encoding="utf-8")
    _git(repo_dir, "add", "dnsconfig.js")
    _git(repo_dir, "commit", "-q", "-m", "D: edit dnsconfig.js on feature")
    sha_d = _git(repo_dir, "rev-parse", "HEAD")

    _git(repo_dir, "checkout", "-q", "main")
    _git(repo_dir, "merge", "--no-ff", "-q", "-m", "M: merge feature", "feature")
    sha_m = _git(repo_dir, "rev-parse", "HEAD")

    return {
        "dir": repo_dir,
        "a": sha_a,
        "b": sha_b,
        "c": sha_c,
        "d": sha_d,
        "m": sha_m,
    }


# --- commit_triggers_apply ------------------------------------------------


def test_commit_triggers_apply_true_when_apply_trigger_path_changed(mod, repo):
    assert mod.commit_triggers_apply(repo["b"]) is True


def test_commit_triggers_apply_false_when_only_unrelated_path_changed(mod, repo):
    assert mod.commit_triggers_apply(repo["c"]) is False


def test_commit_triggers_apply_true_for_merge_commit_touching_trigger_path(mod, repo):
    assert mod.commit_triggers_apply(repo["m"]) is True


def test_commit_triggers_apply_none_when_commit_has_no_parent(mod, repo):
    assert mod.commit_triggers_apply(repo["a"]) is None


# --- resolve_revert_target: commit-ish path (real git, no forge involved) --


def test_resolve_revert_target_single_parent_commit(mod, repo):
    assert mod.resolve_revert_target(repo["b"]) == (repo["b"], False)


def test_resolve_revert_target_merge_commit_is_flagged(mod, repo):
    assert mod.resolve_revert_target(repo["m"]) == (repo["m"], True)


def test_resolve_revert_target_resolves_a_ref_not_just_a_sha(mod, repo):
    assert mod.resolve_revert_target("main") == (repo["m"], True)


def test_resolve_revert_target_invalid_commit_returns_none(mod, repo):
    assert mod.resolve_revert_target("not-a-real-ref") is None


# --- resolve_revert_target: PR-number path (forge faked) -------------------


def test_resolve_revert_target_pr_number_resolves_merge_commit(mod, fake_forge, repo):
    fake_forge(pr_view=mod.forge_lib.ForgeResult(
        0, data={"state": "MERGED", "mergeCommit": {"oid": repo["b"]}}
    ))
    assert mod.resolve_revert_target("42") == (repo["b"], False)


def test_resolve_revert_target_pr_number_not_merged_returns_none(mod, fake_forge, repo):
    fake_forge(pr_view=mod.forge_lib.ForgeResult(
        0, data={"state": "OPEN", "mergeCommit": None}
    ))
    assert mod.resolve_revert_target("42") is None


def test_resolve_revert_target_pr_number_forge_failure_returns_none(mod, fake_forge, repo):
    fake_forge(pr_view=mod.forge_lib.ForgeResult(1, error="not found"))
    assert mod.resolve_revert_target("42") is None


def test_resolve_revert_target_pr_number_missing_merge_commit_returns_none(mod, fake_forge, repo):
    fake_forge(pr_view=mod.forge_lib.ForgeResult(
        0, data={"state": "MERGED", "mergeCommit": None}
    ))
    assert mod.resolve_revert_target("42") is None


def test_resolve_revert_target_pr_number_without_forge_available_returns_none(mod, fake_forge, repo):
    forge = fake_forge(available=False)

    assert mod.resolve_revert_target("42") is None
    assert forge.calls == []


# --- fetch_live_acme_snapshot regression test ------------------------------

_LIVE_SNAPSHOT_JS = """var CF = NewDnsProvider("cloudflare");
var REG = NewRegistrar("none");

D("example.com", REG,
\tDnsProvider(CF),
\tDefaultTTL(1),
\tA("@", "203.0.113.5", CF_PROXY_ON),
\tTXT("_acme-challenge", "token-a", TTL(120)),
\tTXT("_acme-challenge", "token-b", TTL(120)),
\tTXT("_acme-challenge.jellyfin", "token-c", TTL(120)),
);
"""


def test_fetch_live_acme_snapshot_parses_get_zones_output_without_error(mod, monkeypatch):
    """Regression test: fetch_live_acme_snapshot used to call the
    unqualified name find_zone_block_in_lines(...) instead of
    records.find_zone_block_in_lines(...), raising NameError before it ever
    got to parse anything."""

    def fake_run_dnscontrol(args, env_overrides):
        out_arg = next(a for a in args if a.startswith("--out="))
        Path(out_arg.split("=", 1)[1]).write_text(_LIVE_SNAPSHOT_JS, encoding="utf-8")
        return 0

    monkeypatch.setattr(mod, "run_dnscontrol", fake_run_dnscontrol)

    result = mod.fetch_live_acme_snapshot("example.com", {})

    assert result is not None
    assert sorted(r["name"] for r in result) == [
        "_acme-challenge",
        "_acme-challenge",
        "_acme-challenge.jellyfin",
    ]
    assert all(r["type"] == "TXT" for r in result)
    assert {r["value"] for r in result} == {"token-a", "token-b", "token-c"}


def test_fetch_live_acme_snapshot_returns_none_when_snapshot_fails(mod, monkeypatch):
    monkeypatch.setattr(mod, "run_dnscontrol", lambda args, env_overrides: 1)
    assert mod.fetch_live_acme_snapshot("example.com", {}) is None
