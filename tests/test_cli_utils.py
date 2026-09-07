"""Tests for dnsctl_lib/cli_utils.py: slugify, parse_index_arg, and the two
interactive prompt helpers."""
from __future__ import annotations

import argparse

import pytest


def test_slugify_lowercases_and_hyphenates(mod):
    assert mod.slugify("Add CAA records!!") == "add-caa-records"


def test_slugify_strips_leading_and_trailing_punctuation(mod):
    assert mod.slugify("--already-slug--") == "already-slug"


def test_slugify_blank_input_falls_back_to_change(mod):
    assert mod.slugify("   ") == "change"
    assert mod.slugify("!!!") == "change"


def test_slugify_truncates_to_max_len_without_trailing_hyphen(mod):
    text = "word " * 20  # long enough to need truncation well before a boundary
    slug = mod.slugify(text, max_len=10)
    assert len(slug) <= 10
    assert not slug.endswith("-")


def test_parse_index_arg_single_int(mod):
    assert mod.parse_index_arg("3") == [3]


def test_parse_index_arg_range(mod):
    assert mod.parse_index_arg("1-6") == [1, 2, 3, 4, 5, 6]


def test_parse_index_arg_reversed_range_is_normalized(mod):
    assert mod.parse_index_arg("6-4") == [4, 5, 6]


def test_parse_index_arg_comma_separated_combo(mod):
    assert mod.parse_index_arg("1,2,4-6") == [1, 2, 4, 5, 6]


def test_parse_index_arg_tolerates_whitespace_and_empty_parts(mod):
    assert mod.parse_index_arg(" 1 , , 2 ") == [1, 2]


def test_parse_index_arg_rejects_non_numeric_token(mod):
    with pytest.raises(argparse.ArgumentTypeError):
        mod.parse_index_arg("abc")


def test_prompt_returns_default_on_empty_input(mod, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_a: "")
    assert mod.prompt("Commit message", default="fallback") == "fallback"


def test_prompt_returns_typed_value(mod, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_a: "typed value")
    assert mod.prompt("Commit message", default="fallback") == "typed value"


@pytest.mark.parametrize(
    "default_yes,answer,expected",
    [
        (True, "", True),
        (True, "n", False),
        (True, "y", True),
        (False, "", False),
        (False, "y", True),
    ],
)
def test_prompt_yes_no(mod, monkeypatch, default_yes, answer, expected):
    monkeypatch.setattr("builtins.input", lambda *_a: answer)
    assert mod.prompt_yes_no("Continue?", default_yes=default_yes) is expected


# --- confirm_or_abort (TOOL-12) --------------------------------------------


def test_confirm_or_abort_exact_match_is_true(mod, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_a: "APPLY")
    assert mod.confirm_or_abort('Type "APPLY" to continue: ', "APPLY") is True


def test_confirm_or_abort_exact_mismatch_is_false(mod, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_a: "apply")
    assert mod.confirm_or_abort('Type "APPLY" to continue: ', "APPLY") is False


def test_confirm_or_abort_no_normalize_is_whitespace_and_case_sensitive(mod, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_a: " APPLY ")
    assert mod.confirm_or_abort('Type "APPLY" to continue: ', "APPLY") is False


def test_confirm_or_abort_normalize_strips_and_lowercases(mod, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_a: " Y ")
    assert mod.confirm_or_abort("Look correct? [y/N]: ", "y", normalize=True) is True


def test_confirm_or_abort_normalize_rejects_other_answers(mod, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_a: "n")
    assert mod.confirm_or_abort("Look correct? [y/N]: ", "y", normalize=True) is False


# --- resolve_zone_arg (TOOL-12) ---------------------------------------------


def test_resolve_zone_arg_none_is_ok(mod):
    assert mod.resolve_zone_arg(None, ["example.com", "example.org"]) is True


def test_resolve_zone_arg_valid_zone_is_ok(mod):
    assert mod.resolve_zone_arg("example.com", ["example.com", "example.org"]) is True


def test_resolve_zone_arg_invalid_zone_prints_error_and_returns_false(mod, capsys):
    assert mod.resolve_zone_arg("unmanaged-zone.test", ["example.com", "example.org"]) is False
    err = capsys.readouterr().err
    assert "not a zone this project manages (example.com, example.org)" in err


# --- split_pr_checks (TOOL-12) ----------------------------------------------


def test_split_pr_checks_separates_preview_from_other_failed(mod):
    checks = [
        {"name": "DNS Preview", "conclusion": "SUCCESS"},
        {"name": "lint", "conclusion": "FAILURE"},
        {"name": "unit-tests", "conclusion": "SUCCESS"},
    ]
    preview, other_failed = mod.split_pr_checks(checks)
    assert preview == [checks[0]]
    assert other_failed == [checks[1]]


def test_split_pr_checks_empty_list(mod):
    assert mod.split_pr_checks([]) == ([], [])


def test_split_pr_checks_treats_none_state_as_not_failed(mod):
    checks = [{"name": "ci", "conclusion": None, "state": None}]
    _preview, other_failed = mod.split_pr_checks(checks)
    assert other_failed == []


def test_split_pr_checks_treats_pending_state_as_failed(mod):
    checks = [{"name": "ci", "conclusion": None, "state": "PENDING"}]
    _preview, other_failed = mod.split_pr_checks(checks)
    assert other_failed == checks


def test_split_pr_checks_matches_context_field_too(mod):
    checks = [{"context": "dns-preview", "state": "SUCCESS"}]
    preview, _other_failed = mod.split_pr_checks(checks)
    assert preview == checks


# --- find_non_author_approval (CI-17) ---------------------------------------


def test_find_non_author_approval_finds_approving_reviewer(mod):
    reviews = [
        {"author": {"login": "alice"}, "state": "APPROVED", "submittedAt": "2026-01-01T00:00:00Z"},
    ]
    assert mod.find_non_author_approval(reviews, "bob") == "alice"


def test_find_non_author_approval_ignores_authors_own_approval(mod):
    reviews = [
        {"author": {"login": "bob"}, "state": "APPROVED", "submittedAt": "2026-01-01T00:00:00Z"},
    ]
    assert mod.find_non_author_approval(reviews, "bob") is None


def test_find_non_author_approval_uses_each_reviewers_latest_review(mod):
    # alice first requests changes, then later approves - only the latest
    # (APPROVED) should count.
    reviews = [
        {"author": {"login": "alice"}, "state": "CHANGES_REQUESTED", "submittedAt": "2026-01-01T00:00:00Z"},
        {"author": {"login": "alice"}, "state": "APPROVED", "submittedAt": "2026-01-02T00:00:00Z"},
    ]
    assert mod.find_non_author_approval(reviews, "bob") == "alice"


def test_find_non_author_approval_stale_approval_superseded_by_changes_requested(mod):
    reviews = [
        {"author": {"login": "alice"}, "state": "APPROVED", "submittedAt": "2026-01-01T00:00:00Z"},
        {"author": {"login": "alice"}, "state": "CHANGES_REQUESTED", "submittedAt": "2026-01-02T00:00:00Z"},
    ]
    assert mod.find_non_author_approval(reviews, "bob") is None


def test_find_non_author_approval_empty_reviews(mod):
    assert mod.find_non_author_approval([], "bob") is None


# --- ensure_trailing_dot (TOOL-14) ------------------------------------------


def test_ensure_trailing_dot_already_has_dot_is_unchanged(mod):
    assert mod.ensure_trailing_dot("example.com.", interactive=True) == "example.com."


def test_ensure_trailing_dot_empty_value_is_unchanged(mod):
    assert mod.ensure_trailing_dot("", interactive=True) == ""


def test_ensure_trailing_dot_non_interactive_appends_and_notes(mod, capsys):
    result = mod.ensure_trailing_dot("example.com", interactive=False)
    assert result == "example.com."
    assert "note: appended trailing dot -> example.com." in capsys.readouterr().out


def test_ensure_trailing_dot_interactive_yes_appends(mod, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_a: "")  # default_yes=True
    assert mod.ensure_trailing_dot("example.com", interactive=True) == "example.com."


def test_ensure_trailing_dot_interactive_no_leaves_value_alone(mod, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_a: "n")
    assert mod.ensure_trailing_dot("example.com", interactive=True) == "example.com"


# --- RECORD_VALUE_LABELS (TOOL-14) ------------------------------------------


def test_record_value_labels_covers_every_wizard_record_type(mod):
    assert set(mod.RECORD_VALUE_LABELS) == {"A", "CNAME", "MX", "TXT", "CAA"}
