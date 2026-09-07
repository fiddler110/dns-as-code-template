"""Tests for dnsctl_lib/install.py: platform_asset_name's OS/arch mapping,
used by `dnsctl.py install-dnscontrol`."""
from __future__ import annotations

import pytest


def _fake_platform(monkeypatch, mod, system, machine):
    monkeypatch.setattr(mod.install.platform, "system", lambda: system)
    monkeypatch.setattr(mod.install.platform, "machine", lambda: machine)


@pytest.mark.parametrize(
    "system,machine,expected",
    [
        ("Linux", "x86_64", "dnscontrol_4.46.0_linux_amd64.tar.gz"),
        ("Linux", "amd64", "dnscontrol_4.46.0_linux_amd64.tar.gz"),
        ("Linux", "aarch64", "dnscontrol_4.46.0_linux_arm64.tar.gz"),
        ("Linux", "arm64", "dnscontrol_4.46.0_linux_arm64.tar.gz"),
        ("Darwin", "x86_64", "dnscontrol_4.46.0_darwin_all.tar.gz"),
        ("Darwin", "arm64", "dnscontrol_4.46.0_darwin_all.tar.gz"),
        ("Windows", "amd64", "dnscontrol_4.46.0_windows_amd64.zip"),
        ("Windows", "arm64", "dnscontrol_4.46.0_windows_arm64.zip"),
    ],
)
def test_platform_asset_name_known_combinations(mod, monkeypatch, system, machine, expected):
    _fake_platform(monkeypatch, mod, system, machine)
    assert mod.install.platform_asset_name("4.46.0") == expected


def test_platform_asset_name_unsupported_arch_returns_none(mod, monkeypatch):
    _fake_platform(monkeypatch, mod, "Linux", "riscv64")
    assert mod.install.platform_asset_name("4.46.0") is None


def test_platform_asset_name_unsupported_os_returns_none(mod, monkeypatch):
    _fake_platform(monkeypatch, mod, "FreeBSD", "x86_64")
    assert mod.install.platform_asset_name("4.46.0") is None
