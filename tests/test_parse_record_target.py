import pytest


def test_apex(mod):
    assert mod.parse_record_target("example.com") == ("@", "example.com")


def test_subdomain(mod):
    assert mod.parse_record_target("plex.example.com") == ("plex", "example.com")


def test_wildcard(mod):
    assert mod.parse_record_target("*.example.com") == ("*", "example.com")


def test_compound_name(mod):
    assert mod.parse_record_target("www.plex.example.com") == (
        "www.plex",
        "example.com",
    )


def test_trailing_dot(mod):
    assert mod.parse_record_target("plex.example.com.") == (
        "plex",
        "example.com",
    )


def test_case_insensitive(mod):
    assert mod.parse_record_target("PLEX.Example.Com") == (
        "plex",
        "example.com",
    )


def test_bare_relative_with_zone(mod):
    assert mod.parse_record_target("plex", zone="example.com") == (
        "plex",
        "example.com",
    )


def test_compound_relative_with_zone(mod):
    assert mod.parse_record_target("www.plex", zone="example.com") == (
        "www.plex",
        "example.com",
    )


def test_bare_relative_without_zone_raises(mod):
    with pytest.raises(ValueError, match="bare relative name"):
        mod.parse_record_target("plex")


def test_name_in_no_managed_zone_raises(mod):
    with pytest.raises(ValueError, match="doesn't match any zone"):
        mod.parse_record_target("plex.unmanaged-zone.test")


def test_explicit_zone_not_in_zones_raises(mod):
    with pytest.raises(ValueError, match="not a zone this project manages"):
        mod.parse_record_target("plex", zone="unmanaged-zone.test")
