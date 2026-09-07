"""Happy-path tests for TOOL-16: the `record add`/`edit`/`remove`/`update-ip`
CLI command handlers (cmd_record_add etc.) in scripts/dnsctl.py. Previously
only the pure helpers these handlers call (build_record_line,
parse_record_line_full, find_zone_block, insert_record_line, ...) were
unit-tested (tests/test_zone_block_mutation.py) - never the handlers
themselves.

Every test runs with --yes (non-interactive) so no `input()` prompt is ever
hit, and uses the `config_from` fixture (tests/conftest.py) to point
mod.DNSCONFIG_FILE at a throwaway copy of tests/fixtures/multi_zone.js -
never the real dnsconfig.js. `offer_preview_and_submit`'s non-interactive
branch only prints next-step hints (see scripts/dnsctl.py), so it's safe to
leave un-mocked here.

Following TOOL-13 (dnsconfig.js is read once per command, not re-read by
every helper), assertions here call `mod.read_dnsconfig_lines()` fresh after
each handler call to see what it wrote to disk, then thread that `lines` list
into `find_record_lines` - the same pattern tests/test_zone_block_mutation.py
uses.
"""
from __future__ import annotations

import argparse


def test_cmd_record_add_happy_path_a_record(mod, config_from):
    config_from("multi_zone.js")
    args = argparse.Namespace(
        target="newhost.example.com", zone=None, type="A", value="5.6.7.8",
        priority=None, proxy=None, ttl=None, tag=None, yes=True,
    )

    assert mod.cmd_record_add(args) == 0

    lines = mod.read_dnsconfig_lines()
    matches = mod.find_record_lines(lines, "newhost", "example.com", "A")
    assert len(matches) == 1
    assert "5.6.7.8" in matches[0][1]


def test_cmd_record_add_happy_path_mx_record(mod, config_from):
    config_from("multi_zone.js")
    args = argparse.Namespace(
        target="@", zone="example.org", type="MX", value="mail.example.org.",
        priority=20, proxy=None, ttl=None, tag=None, yes=True,
    )

    assert mod.cmd_record_add(args) == 0

    lines = mod.read_dnsconfig_lines()
    matches = mod.find_record_lines(lines, "@", "example.org", "MX")
    assert len(matches) == 1
    assert "mail.example.org." in matches[0][1]
    assert "20" in matches[0][1]


def test_cmd_record_add_rejects_duplicate_record(mod, config_from):
    config_from("multi_zone.js")
    # This exact A record already exists in multi_zone.js's example.com block.
    args = argparse.Namespace(
        target="@", zone="example.com", type="A", value="203.0.113.5",
        priority=None, proxy=True, ttl=None, tag=None, yes=True,
    )

    assert mod.cmd_record_add(args) == 1

    lines = mod.read_dnsconfig_lines()
    assert len(mod.find_record_lines(lines, "@", "example.com", "A")) == 1


def test_cmd_record_edit_happy_path_changes_cname_target(mod, config_from):
    config_from("multi_zone.js")
    args = argparse.Namespace(
        target="www.example.com", zone=None, type=None, index=None,
        value="changed.example.com.", priority=None, proxy=None, ttl=None,
        tag=None, yes=True,
    )

    assert mod.cmd_record_edit(args) == 0

    lines = mod.read_dnsconfig_lines()
    matches = mod.find_record_lines(lines, "www", "example.com", "CNAME")
    assert len(matches) == 1
    assert "changed.example.com." in matches[0][1]
    # The other zone's "www" CNAME must be untouched.
    assert any(
        "example.org." in line for _i, line in mod.find_record_lines(lines, "www", "example.org")
    )


def test_cmd_record_remove_happy_path(mod, config_from):
    config_from("multi_zone.js")
    args = argparse.Namespace(
        target="www.example.org", zone=None, type=None, index=None, yes=True,
    )

    assert mod.cmd_record_remove(args) == 0

    lines = mod.read_dnsconfig_lines()
    assert mod.find_record_lines(lines, "www", "example.org") == []
    # example.com's "www" CNAME must be unaffected.
    assert len(mod.find_record_lines(lines, "www", "example.com")) == 1


def test_cmd_record_update_ip_happy_path(mod, config_from):
    config_from("multi_zone.js")
    args = argparse.Namespace(
        old_ip="203.0.113.5", new_ip="1.2.3.4", zone=None, yes=True,
    )

    assert mod.cmd_record_update_ip(args) == 0

    lines = mod.read_dnsconfig_lines()
    matches = mod.find_record_lines(lines, "@", "example.com", "A")
    assert len(matches) == 1
    assert "1.2.3.4" in matches[0][1]
    assert "203.0.113.5" not in matches[0][1]
    # example.org's A record (different IP) must be untouched.
    other_zone_matches = mod.find_record_lines(lines, "@", "example.org", "A")
    assert len(other_zone_matches) == 1
    assert "203.0.113.10" in other_zone_matches[0][1]


def test_cmd_record_update_ip_no_matches_returns_error(mod, config_from):
    config_from("multi_zone.js")
    args = argparse.Namespace(
        old_ip="10.10.10.10", new_ip="1.2.3.4", zone=None, yes=True,
    )

    assert mod.cmd_record_update_ip(args) == 1
