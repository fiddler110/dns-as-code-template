"""Tests for dnsctl_lib/forge.py's pure logic: remote-URL parsing, forge-kind
auto-detection, and DNSCTL_FORGE override handling. The platform-specific
Forge implementations (GithubForge/AzureDevOpsForge/ForgejoForge) themselves
talk to a real CLI or a real HTTP API and aren't exercised here - dnsctl.py's
own command-handler tests (test_command_handler_guards.py,
test_commit_helpers.py) cover those through the Forge interface via
conftest.py's FakeForge instead.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def forge_lib(mod):
    return mod.forge_lib


# --- detect_forge_kind -------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:owner/repo.git",
        "https://github.com/owner/repo.git",
        "ssh://git@github.com/owner/repo.git",
        "git@github.mycorp.com:owner/repo.git",  # GitHub Enterprise
        "https://github.mycorp.com/owner/repo.git",
    ],
)
def test_detect_forge_kind_github(forge_lib, url):
    assert forge_lib.detect_forge_kind(url) == "github"


@pytest.mark.parametrize(
    "url",
    [
        "git@ssh.dev.azure.com:v3/org/project/repo",
        "https://dev.azure.com/org/project/_git/repo",
        "https://org@dev.azure.com/org/project/_git/repo",
        "https://org.visualstudio.com/project/_git/repo",
    ],
)
def test_detect_forge_kind_azure_devops(forge_lib, url):
    assert forge_lib.detect_forge_kind(url) == "azuredevops"


@pytest.mark.parametrize(
    "url",
    [
        "git@forge.example.com:owner/repo.git",
        "https://forge.example.com/owner/repo.git",
        "https://git.internal.corp/owner/repo.git",
    ],
)
def test_detect_forge_kind_forgejo_fallback(forge_lib, url):
    assert forge_lib.detect_forge_kind(url) == "forgejo"


def test_detect_forge_kind_no_remote_defaults_to_github(forge_lib):
    assert forge_lib.detect_forge_kind("") == "github"


# --- get_forge: DNSCTL_FORGE override vs. auto-detection --------------------


def test_get_forge_auto_detects_github(forge_lib, monkeypatch):
    monkeypatch.setattr(forge_lib, "get_origin_url", lambda: "git@github.com:owner/repo.git")
    assert isinstance(forge_lib.get_forge({}), forge_lib.GithubForge)


def test_get_forge_auto_detects_azure_devops(forge_lib, monkeypatch):
    monkeypatch.setattr(forge_lib, "get_origin_url", lambda: "https://dev.azure.com/org/project/_git/repo")
    assert isinstance(forge_lib.get_forge({}), forge_lib.AzureDevOpsForge)


def test_get_forge_auto_detects_forgejo(forge_lib, monkeypatch):
    monkeypatch.setattr(forge_lib, "get_origin_url", lambda: "https://forge.example.com/owner/repo.git")
    assert isinstance(forge_lib.get_forge({}), forge_lib.ForgejoForge)


@pytest.mark.parametrize(
    "override,expected",
    [
        ("github", "GithubForge"),
        ("gh", "GithubForge"),
        ("azuredevops", "AzureDevOpsForge"),
        ("azure-devops", "AzureDevOpsForge"),
        ("az", "AzureDevOpsForge"),
        ("forgejo", "ForgejoForge"),
        ("gitea", "ForgejoForge"),
        ("FORGEJO", "ForgejoForge"),  # case-insensitive
    ],
)
def test_get_forge_dnsctl_forge_override_wins_over_auto_detect(forge_lib, monkeypatch, override, expected):
    # Origin points at GitHub - if the override didn't win, we'd get GithubForge.
    monkeypatch.setattr(forge_lib, "get_origin_url", lambda: "git@github.com:owner/repo.git")
    forge = forge_lib.get_forge({"DNSCTL_FORGE": override})
    assert type(forge).__name__ == expected


def test_get_forge_reads_override_from_process_environment_too(forge_lib, monkeypatch):
    monkeypatch.setattr(forge_lib, "get_origin_url", lambda: "git@github.com:owner/repo.git")
    monkeypatch.setenv("DNSCTL_FORGE", "forgejo")
    assert isinstance(forge_lib.get_forge({}), forge_lib.ForgejoForge)


def test_get_forge_unrecognized_override_falls_back_to_auto_detect(forge_lib, monkeypatch, capsys):
    monkeypatch.setattr(forge_lib, "get_origin_url", lambda: "https://dev.azure.com/org/project/_git/repo")
    forge = forge_lib.get_forge({"DNSCTL_FORGE": "bitbucket"})
    assert isinstance(forge, forge_lib.AzureDevOpsForge)
    assert "unrecognized DNSCTL_FORGE" in capsys.readouterr().err


# --- parse_azure_devops_remote ----------------------------------------------


def test_parse_azure_devops_remote_ssh(forge_lib):
    ref = forge_lib.parse_azure_devops_remote("git@ssh.dev.azure.com:v3/myorg/myproject/myrepo")
    assert ref == forge_lib.AzureDevOpsRepoRef("https://dev.azure.com/myorg", "myproject", "myrepo")


def test_parse_azure_devops_remote_https(forge_lib):
    ref = forge_lib.parse_azure_devops_remote("https://dev.azure.com/myorg/myproject/_git/myrepo")
    assert ref == forge_lib.AzureDevOpsRepoRef("https://dev.azure.com/myorg", "myproject", "myrepo")


def test_parse_azure_devops_remote_https_with_user_prefix(forge_lib):
    ref = forge_lib.parse_azure_devops_remote("https://myorg@dev.azure.com/myorg/myproject/_git/myrepo")
    assert ref == forge_lib.AzureDevOpsRepoRef("https://dev.azure.com/myorg", "myproject", "myrepo")


def test_parse_azure_devops_remote_legacy_visualstudio_com(forge_lib):
    ref = forge_lib.parse_azure_devops_remote("https://myorg.visualstudio.com/myproject/_git/myrepo")
    assert ref == forge_lib.AzureDevOpsRepoRef("https://dev.azure.com/myorg", "myproject", "myrepo")


def test_parse_azure_devops_remote_strips_git_suffix(forge_lib):
    ref = forge_lib.parse_azure_devops_remote("https://dev.azure.com/myorg/myproject/_git/myrepo.git")
    assert ref.repository == "myrepo"


def test_parse_azure_devops_remote_non_azure_url_returns_none(forge_lib):
    assert forge_lib.parse_azure_devops_remote("git@github.com:owner/repo.git") is None


# --- parse_forgejo_remote ----------------------------------------------------


def test_parse_forgejo_remote_ssh(forge_lib):
    ref = forge_lib.parse_forgejo_remote("git@forge.example.com:owner/repo.git")
    assert ref == forge_lib.ForgejoRepoRef("https://forge.example.com", "owner", "repo")


def test_parse_forgejo_remote_https(forge_lib):
    ref = forge_lib.parse_forgejo_remote("https://forge.example.com/owner/repo.git")
    assert ref == forge_lib.ForgejoRepoRef("https://forge.example.com", "owner", "repo")


def test_parse_forgejo_remote_https_without_git_suffix(forge_lib):
    ref = forge_lib.parse_forgejo_remote("https://forge.example.com/owner/repo")
    assert ref == forge_lib.ForgejoRepoRef("https://forge.example.com", "owner", "repo")


def test_parse_forgejo_remote_malformed_url_returns_none(forge_lib):
    assert forge_lib.parse_forgejo_remote("not-a-url") is None
