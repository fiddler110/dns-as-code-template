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
