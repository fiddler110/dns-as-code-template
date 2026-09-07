"""Regression tests for the "keep these in sync" invariants documented across
this repo (see CLAUDE.md). Each test parses the real, checked-in files
directly - not fixtures - since the whole point is to catch drift in the
actual repo state.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_zones_matches_dnsconfig_js(mod):
    dnsconfig = (REPO_ROOT / "dnsconfig.js").read_text(encoding="utf-8")
    zones = set(re.findall(r'^D\("([^"]+)"', dnsconfig, re.MULTILINE))
    assert zones == set(mod.ZONES)


# The pinned dnscontrol version used to be hardcoded independently in several
# places (this used to be a "do these literals agree" test). Now there's a
# true single source - .dnscontrol-version - so what actually needs
# protecting is narrower: (1) dnsctl.py reads that file rather than a literal,
# and (2) every workflow that installs/compares dnscontrol reads that same
# file rather than a hardcoded version string. A revert to a hardcoded
# literal in any of these locations should fail this test even if that
# literal happens to still match the current pinned version.
VERSION_LITERAL_RE = re.compile(r'(?:DNSCONTROL_VERSION|PINNED_VERSION):\s*"[\d.]+"')


def test_dnscontrol_version_file_is_the_single_source(mod):
    version_file = REPO_ROOT / ".dnscontrol-version"
    assert version_file.is_file(), ".dnscontrol-version does not exist"
    assert mod.DNSCONTROL_VERSION == version_file.read_text(encoding="utf-8").strip()


def test_workflows_read_dnscontrol_version_from_shared_file():
    workflows_dir = REPO_ROOT / ".github" / "workflows"
    for name in ("preview.yml", "apply.yml", "drift.yml", "dnscontrol-version-check.yml"):
        text = (workflows_dir / name).read_text(encoding="utf-8")
        assert ".dnscontrol-version" in text, (
            f"{name} does not appear to read the shared .dnscontrol-version file"
        )
        assert not VERSION_LITERAL_RE.search(text), (
            f"{name} has a hardcoded DNSCONTROL_VERSION/PINNED_VERSION literal again "
            "instead of reading .dnscontrol-version"
        )


def _uncomment(text: str) -> str:
    """This template ships every workflow's `on:` trigger block commented out
    (see apply.yml) since there are no real Cloudflare credentials configured
    yet - strip a single leading '# ' (or bare '#') from each line so the
    invariant below still parses the commented-out block instead of finding
    nothing. A live project that has uncommented its triggers parses
    unchanged, since there's nothing left to strip."""
    return "\n".join(re.sub(r"^(\s*)#\s?", r"\1", line) for line in text.splitlines())


def _paths_filter_from(text: str) -> set[str]:
    text = _uncomment(text)
    match = re.search(r"push:\s*\n(?:.*\n)*?\s*paths:\s*\n((?:\s*-\s*\"[^\"]+\"\s*\n)+)", text)
    assert match, "could not find a push: paths: filter"
    return set(re.findall(r'-\s*"([^"]+)"', match.group(1)))


def test_apply_trigger_paths_matches_apply_yml(mod):
    apply_yml = (REPO_ROOT / ".github" / "workflows" / "apply.yml").read_text(encoding="utf-8")
    assert _paths_filter_from(apply_yml) == mod.APPLY_TRIGGER_PATHS


def test_pre_push_paths_matches_apply_yml_paths():
    pre_push = (REPO_ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8")
    match = re.search(r"dns_relevant_paths=\(([^)]*)\)", pre_push)
    assert match, "could not find dns_relevant_paths in .githooks/pre-push"
    hook_paths = set(match.group(1).split())

    apply_yml = (REPO_ROOT / ".github" / "workflows" / "apply.yml").read_text(encoding="utf-8")
    assert hook_paths == _paths_filter_from(apply_yml)
