"""Tests for `dnsctl fmt` (TOOL-9): re-sorting each zone block in
dnsconfig.js into canonical record-type order while preserving comments
attached to a record and never changing what's actually in the file
(only where each line sits)."""

import argparse


def run_fmt(mod, zone="example.com", check=False):
    # These fixtures only define the example.com block - restrict fmt to
    # it so it doesn't also complain that example.org's block is missing
    # (same reasoning as test_lint.py's run_lint()).
    return mod.cmd_fmt(argparse.Namespace(zone=zone, check=check))


def block_lines(mod, zone="example.com"):
    lines = mod.read_dnsconfig_lines()
    start, end = mod.find_zone_block(lines, zone)
    return lines[start + 1 : end]


def test_fmt_sorts_scrambled_block_into_canonical_type_order(mod, config_from):
    config_from("fmt_scrambled.js")
    rc = run_fmt(mod)
    assert rc == 0

    lines = block_lines(mod)
    expected = [
        "\tDnsProvider(CF),",
        "\tDefaultTTL(1),",
        '\tA("@", "203.0.113.5", CF_PROXY_ON),',
        '\tCNAME("www", "example.com.", CF_PROXY_ON),',
        (
            "\t// Unproxied: Jellyfin streaming through the Cloudflare proxy would hit "
            "CDN response-size/streaming limits."
        ),
        '\tCNAME("jf", "example.com."),',
        '\tMX("@", 50, "amir.mx.cloudflare.net."),',
        '\tTXT("_dmarc", "v=DMARC1; p=reject"),',
        '\tTXT("@", "v=spf1 include:_spf.mx.cloudflare.net -all"),',
        '\tCAA("@", "issue", "letsencrypt.org"),',
    ]
    assert lines == expected


def test_fmt_preserves_the_set_of_records_exactly(mod, config_from):
    """Reordering must never change what's actually configured - only the
    line order. Compare the full set of parsed record tuples before/after."""
    config_from("fmt_scrambled.js")
    before_rows, before_skipped = mod.collect_show_rows("example.com")
    run_fmt(mod)
    after_rows, after_skipped = mod.collect_show_rows("example.com")

    assert before_skipped == after_skipped == []
    assert {tuple(row) for row in before_rows} == {tuple(row) for row in after_rows}
    assert len(before_rows) == len(after_rows)


def test_fmt_comment_stays_glued_to_its_record(mod, config_from):
    config_from("fmt_scrambled.js")
    run_fmt(mod)
    lines = block_lines(mod)
    jf_index = next(i for i, line in enumerate(lines) if '"jf"' in line)
    assert "Unproxied" in lines[jf_index - 1]


def test_fmt_is_idempotent(mod, config_from):
    config_from("fmt_scrambled.js")
    run_fmt(mod)
    first_pass = mod.DNSCONFIG_FILE.read_text(encoding="utf-8")

    rc = run_fmt(mod)
    second_pass = mod.DNSCONFIG_FILE.read_text(encoding="utf-8")

    assert rc == 0
    assert first_pass == second_pass


def test_fmt_check_mode_does_not_modify_and_reports_needed(mod, config_from, capsys):
    dest = config_from("fmt_scrambled.js")
    before = dest.read_text(encoding="utf-8")

    rc = run_fmt(mod, check=True)
    out = capsys.readouterr().out

    assert rc == 1
    assert dest.read_text(encoding="utf-8") == before
    assert "would reformat" in out


def test_fmt_check_mode_passes_on_already_sorted_input(mod, config_from, capsys):
    config_from("lint_clean.js")
    rc = run_fmt(mod, check=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "already sorted" in out


def test_fmt_refuses_to_reorder_an_unparseable_line(mod, config_from):
    """fmt would rather refuse than guess wrong about a line it can't fully
    understand - reuses the same fixture lint's TOOL-1 regression test uses."""
    dest = config_from("lint_malformed.js")
    before = dest.read_text(encoding="utf-8")

    rc = run_fmt(mod)

    assert rc == 1
    assert dest.read_text(encoding="utf-8") == before


def test_fmt_real_dnsconfig_js_is_already_canonical(mod, tmp_path, monkeypatch):
    """The real dnsconfig.js is hand-kept in canonical order already, so fmt
    against a copy of it should be a strict no-op - this also guards against
    RECORD_TYPE_ORDER silently drifting from how the real file is arranged."""
    real_path = mod.REPO_ROOT / "dnsconfig.js"
    dest = tmp_path / "dnsconfig.js"
    dest.write_text(real_path.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(mod, "DNSCONFIG_FILE", dest)

    before = dest.read_text(encoding="utf-8")
    rc = run_fmt(mod, zone=None)  # both zones - this fixture is the real file
    after = dest.read_text(encoding="utf-8")

    assert rc == 0
    assert before == after
