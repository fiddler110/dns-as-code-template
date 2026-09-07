import argparse


def run_lint(mod, zone="example.com"):
    # These fixtures only define the example.com block - restrict lint to
    # it so it doesn't also complain that example.org's block is missing.
    return mod.cmd_lint(argparse.Namespace(zone=zone))


def test_lint_clean_passes(mod, config_from, capsys):
    config_from("lint_clean.js")
    rc = run_lint(mod)
    out = capsys.readouterr().out
    assert rc == 0
    assert "lint passed" in out


def test_lint_flags_duplicate_line(mod, config_from, capsys):
    config_from("lint_duplicate.js")
    rc = run_lint(mod)
    out = capsys.readouterr().out
    assert rc == 1
    assert "duplicate line" in out


def test_lint_flags_cname_coexistence(mod, config_from, capsys):
    config_from("lint_cname_coexist.js")
    rc = run_lint(mod)
    out = capsys.readouterr().out
    assert rc == 1
    assert "can't coexist" in out


def test_lint_flags_two_cnames_same_name(mod, config_from, capsys):
    config_from("lint_two_cnames.js")
    rc = run_lint(mod)
    out = capsys.readouterr().out
    assert rc == 1
    assert "more than one CNAME" in out


def test_lint_flags_missing_trailing_dot(mod, config_from, capsys):
    config_from("lint_missing_dot.js")
    rc = run_lint(mod)
    out = capsys.readouterr().out
    assert rc == 1
    assert "missing trailing dot" in out


def test_lint_flags_malformed_line_as_warning_not_silent_skip(mod, config_from, capsys):
    """TOOL-1 regression test: a line that is neither a directive nor a
    parseable record must show up as a [warn], not vanish silently."""
    config_from("lint_malformed.js")
    rc = run_lint(mod)
    out = capsys.readouterr().out
    assert rc == 0  # it's a warning, not an error - lint still "passes"
    assert "[warn]" in out
    assert "SRV_LIKE_BUT_UNSUPPORTED" in out


def test_lint_flags_bad_ipv4(mod, config_from, capsys):
    """TOOL-6: an A record whose value isn't a well-formed IPv4 address is
    caught by lint instead of only surfacing at `preview` time."""
    config_from("lint_bad_ipv4.js")
    rc = run_lint(mod)
    out = capsys.readouterr().out
    assert rc == 1
    assert "not a valid IPv4 address" in out


def test_lint_flags_missing_trailing_dot_on_ns(mod, config_from, capsys):
    """TOOL-6: the trailing-dot check now covers NS, not just CNAME/MX."""
    config_from("lint_missing_dot_ns.js")
    rc = run_lint(mod)
    out = capsys.readouterr().out
    assert rc == 1
    assert "missing trailing dot" in out


def test_lint_flags_missing_trailing_dot_on_srv(mod, config_from, capsys):
    """TOOL-6: the trailing-dot check now covers SRV, not just CNAME/MX."""
    config_from("lint_missing_dot_srv.js")
    rc = run_lint(mod)
    out = capsys.readouterr().out
    assert rc == 1
    assert "missing trailing dot" in out


def test_lint_flags_apex_cname_unconditionally(mod, config_from, capsys):
    """TOOL-6: an apex CNAME is invalid per DNS spec even with no sibling
    record at the same name (unlike the coexistence check, which only fires
    when something else already shares the name)."""
    config_from("lint_apex_cname.js")
    rc = run_lint(mod)
    out = capsys.readouterr().out
    assert rc == 1
    assert "zone apex" in out


def test_lint_flags_ttl_zero(mod, config_from, capsys):
    """TOOL-6: TTL(0) is rejected - non-positive TTLs are meaningless."""
    config_from("lint_ttl_zero.js")
    rc = run_lint(mod)
    out = capsys.readouterr().out
    assert rc == 1
    assert "not positive" in out


def test_lint_flags_ttl_too_large(mod, config_from, capsys):
    """TOOL-6: an absurdly large explicit TTL is rejected as a sanity check."""
    config_from("lint_ttl_too_large.js")
    rc = run_lint(mod)
    out = capsys.readouterr().out
    assert rc == 1
    assert "exceeds the sanity limit" in out


def test_lint_no_comma_record_is_included_not_dropped(mod, config_from, capsys):
    """TOOL-1 regression test: before the fix, FULL_RECORD_LINE_PATTERN
    required a trailing comma, so a valid record line without one (like
    dnscontrol itself accepts) was silently skipped by lint/show - the
    duplicate check below never even saw it. After the fix it must be
    treated exactly like any other record."""
    config_from("lint_no_comma.js")
    rc = run_lint(mod)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[warn]" not in out
    assert "not checked" not in out

    rows, skipped = mod.collect_show_rows("example.com")
    assert skipped == []
    names = [row[2] for row in rows]
    assert "noComma" in names
