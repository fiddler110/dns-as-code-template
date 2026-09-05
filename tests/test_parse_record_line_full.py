def test_a_record_plain(mod):
    parsed = mod.parse_record_line_full('\tA("@", "203.0.113.5"),')
    assert parsed == {
        "type": "A",
        "name": "@",
        "value": "203.0.113.5",
        "priority": "",
        "ttl": "",
        "proxied": "",
        "extras": [],
        "comment": "",
        "raw": 'A("@", "203.0.113.5"),',
    }


def test_a_record_with_proxy_on(mod):
    parsed = mod.parse_record_line_full('\tA("@", "203.0.113.5", CF_PROXY_ON),')
    assert parsed["proxied"] == "yes"
    assert parsed["value"] == "203.0.113.5"


def test_cname_with_ttl(mod):
    parsed = mod.parse_record_line_full('\tCNAME("www", "example.com.", TTL(300)),')
    assert parsed["type"] == "CNAME"
    assert parsed["ttl"] == "300"
    assert parsed["proxied"] == ""


def test_cname_without_proxy(mod):
    parsed = mod.parse_record_line_full('\tCNAME("jf", "example.com."),')
    assert parsed["proxied"] == ""
    assert parsed["value"] == "example.com."


def test_mx_with_priority(mod):
    parsed = mod.parse_record_line_full('\tMX("@", 50, "amir.mx.cloudflare.net."),')
    assert parsed["type"] == "MX"
    assert parsed["priority"] == "50"
    assert parsed["value"] == "amir.mx.cloudflare.net."


def test_txt_plain(mod):
    parsed = mod.parse_record_line_full('\tTXT("_acme-challenge", "sometoken"),')
    assert parsed["type"] == "TXT"
    assert parsed["value"] == "sometoken"


def test_txt_with_ttl(mod):
    parsed = mod.parse_record_line_full('\tTXT("_acme-challenge", "sometoken", TTL(120)),')
    assert parsed["ttl"] == "120"


def test_txt_value_with_comma(mod):
    parsed = mod.parse_record_line_full(
        '\tTXT("_dmarc", "v=DMARC1; p=reject, sp=reject"),'
    )
    assert parsed["value"] == "v=DMARC1; p=reject, sp=reject"


def test_txt_value_with_escaped_quote(mod):
    parsed = mod.parse_record_line_full(
        '\tTXT("note", "she said \\"hello\\""),'
    )
    assert parsed["value"] == 'she said "hello"'


def test_no_trailing_comma_still_parses(mod):
    """Regression guard for TOOL-1: a syntactically valid record line with no
    trailing comma must parse the same as one with a comma."""
    parsed = mod.parse_record_line_full('\tA("noComma", "1.2.3.4")')
    assert parsed is not None
    assert parsed["name"] == "noComma"
    assert parsed["value"] == "1.2.3.4"


def test_split_top_level_args_ignores_commas_in_parens():
    import dnsctl

    args = dnsctl.split_top_level_args('"www", "example.com.", TTL(300)')
    assert args == ['"www"', '"example.com."', "TTL(300)"]
