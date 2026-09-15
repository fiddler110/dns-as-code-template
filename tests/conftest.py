"""Shared pytest fixtures for the dnsctl test suite.

Imports scripts/dnsctl.py as a module (it's a standalone script, not a
package) and gives tests a way to point it at a fixture file instead of
the real dnsconfig.js - no test here should ever read or write the real
config, or touch the network / Cloudflare API.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_dnsctl():
    spec = importlib.util.spec_from_file_location(
        "dnsctl", REPO_ROOT / "scripts" / "dnsctl.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["dnsctl"] = module
    spec.loader.exec_module(module)
    return module


dnsctl = _load_dnsctl()


@pytest.fixture
def mod():
    """The dnsctl module, imported once for the whole test session."""
    return dnsctl


class FakeForge:
    """Test double for dnsctl_lib.forge.Forge - dnsctl.py's command handlers
    only ever call methods on whatever active_forge() returns, so tests fake
    the PR lifecycle at that seam instead of the old gh-subprocess one.

    Configure a method's return value via the matching kwarg, e.g.
    FakeForge(pr_view=mod.forge_lib.ForgeResult(0, data={...})); pass a
    callable instead of a ForgeResult to compute the result from the call's
    arguments. Every call is recorded in `.calls` as (method_name, args) so
    tests can assert what was (and wasn't) reached."""

    def __init__(self, *, available=True, name="github", display_name="GitHub", **results):
        self._available = available
        self.name = name
        self.display_name = display_name
        self._results = results
        self.calls = []

    def available(self):
        return self._available

    def unavailable_message(self):
        return f"error: fake '{self.name}' forge is not available."

    def require(self):
        if self.available():
            return True
        import sys
        print(self.unavailable_message(), file=sys.stderr)
        return False

    def _call(self, method, *args):
        self.calls.append((method, args))
        result = self._results.get(method)
        if callable(result):
            return result(*args)
        if result is None:
            raise AssertionError(f"FakeForge.{method} called but no result was configured for it")
        return result

    def pr_create(self, *a):
        return self._call("pr_create", *a)

    def pr_list_open(self, *a):
        return self._call("pr_list_open", *a)

    def pr_diff(self, *a):
        return self._call("pr_diff", *a)

    def pr_comments(self, *a):
        return self._call("pr_comments", *a)

    def pr_view(self, *a):
        return self._call("pr_view", *a)

    def pr_approve(self, *a):
        return self._call("pr_approve", *a)

    def pr_merge(self, *a):
        return self._call("pr_merge", *a)


@pytest.fixture
def fake_forge(mod, monkeypatch):
    """Patches mod.active_forge to return a FakeForge, and returns the
    factory so a test can build one with whichever methods it needs
    (`fake_forge(pr_view=...)`) - `mod.active_forge()` returns whichever
    instance was built by the most recent call."""

    holder = {}

    def _make(**kwargs) -> FakeForge:
        instance = FakeForge(**kwargs)
        holder["instance"] = instance
        return instance

    monkeypatch.setattr(mod, "active_forge", lambda: holder["instance"])
    return _make


@pytest.fixture
def config_from(tmp_path, monkeypatch, mod):
    """Returns a function that copies a fixture (by filename, under
    tests/fixtures/) into tmp_path and points mod.DNSCONFIG_FILE at it."""

    def _use(fixture_name: str) -> Path:
        src = FIXTURES_DIR / fixture_name
        dest = tmp_path / "dnsconfig.js"
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.setattr(mod, "DNSCONFIG_FILE", dest)
        return dest

    return _use
