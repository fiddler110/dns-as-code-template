"""Round-trip tests for build_record_line() against parse_record_line_full().

The TOOL-2 cases below assert the fixed round-trip behavior: CF_PROXY_OFF,
a trailing `//` comment, and an unrecognized modifier token are all now
preserved through parse -> build -> parse instead of being dropped or
mashed into the record's value. See ROADMAP.md TOOL-2.
"""


def test_a_record_round_trip(mod):
    line = mod.build_record_line("A", "@", "1.2.3.4", proxy=True, ttl=300)
    parsed = mod.parse_record_line_full(line)
    assert parsed["type"] == "A"
    assert parsed["name"] == "@"
    assert parsed["value"] == "1.2.3.4"
    assert parsed["proxied"] == "yes"
    assert parsed["ttl"] == "300"


def test_cname_round_trip_no_proxy_no_ttl(mod):
    line = mod.build_record_line("CNAME", "www", "example.com.")
    parsed = mod.parse_record_line_full(line)
    assert parsed["type"] == "CNAME"
    assert parsed["value"] == "example.com."
    assert parsed["proxied"] == ""
    assert parsed["ttl"] == ""


def test_mx_round_trip(mod):
    line = mod.build_record_line("MX", "@", "mail.example.com.", priority=10)
    parsed = mod.parse_record_line_full(line)
    assert parsed["type"] == "MX"
    assert parsed["priority"] == "10"
    assert parsed["value"] == "mail.example.com."


def test_txt_round_trip_with_ttl(mod):
    line = mod.build_record_line("TXT", "_acme-challenge", "token", ttl=120)
    parsed = mod.parse_record_line_full(line)
    assert parsed["type"] == "TXT"
    assert parsed["value"] == "token"
    assert parsed["ttl"] == "120"


def test_tool2_cf_proxy_off_is_round_tripped(mod):
    """A record explicitly parsed with proxied == "no" (CF_PROXY_OFF) comes
    back as "no" again after being rewritten, instead of silently becoming
    an omitted modifier."""
    original = mod.parse_record_line_full('\tA("host", "1.2.3.4", CF_PROXY_OFF),')
    assert original["proxied"] == "no"

    rebuilt_line = mod.build_record_line(
        "A", original["name"], original["value"], proxy=False, proxy_off=True
    )
    assert "CF_PROXY_OFF" in rebuilt_line
    reparsed = mod.parse_record_line_full(rebuilt_line)
    assert reparsed["proxied"] == "no"


def test_tool2_trailing_comment_is_preserved(mod):
    """A trailing `//` comment no longer breaks parsing of the whole line -
    it's captured separately and re-emitted by build_record_line()."""
    line_with_comment = '\tCNAME("jf", "example.com."), // no proxy: streaming'
    parsed = mod.parse_record_line_full(line_with_comment)
    assert parsed is not None
    assert parsed["type"] == "CNAME"
    assert parsed["value"] == "example.com."
    assert parsed["comment"] == "// no proxy: streaming"

    rebuilt_line = mod.build_record_line(
        "CNAME", parsed["name"], parsed["value"], comment=parsed["comment"]
    )
    assert rebuilt_line.rstrip().endswith("// no proxy: streaming")
    reparsed = mod.parse_record_line_full(rebuilt_line)
    assert reparsed["comment"] == "// no proxy: streaming"


def test_tool2_unrecognized_modifier_is_preserved_as_extra(mod):
    """An unrecognized modifier token (anything that isn't CF_PROXY_ON/OFF
    or TTL(n)) is kept separately in `extras` instead of being mashed into
    the record's value, and build_record_line() re-emits it verbatim."""
    parsed = mod.parse_record_line_full('\tA("host", "1.2.3.4", IGNORE_NAME),')
    assert parsed is not None
    assert parsed["value"] == "1.2.3.4"
    assert parsed["extras"] == ["IGNORE_NAME"]

    rebuilt_line = mod.build_record_line(
        "A", parsed["name"], parsed["value"], extras=parsed["extras"]
    )
    reparsed = mod.parse_record_line_full(rebuilt_line)
    assert reparsed["value"] == "1.2.3.4"
    assert reparsed["extras"] == ["IGNORE_NAME"]
