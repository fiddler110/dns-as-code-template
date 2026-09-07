"""Small standalone helpers shared by dnsctl's command handlers: stderr
printing, interactive prompts, and a couple of string/argument parsers."""

from __future__ import annotations

import argparse
import os
import re
import sys


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


# Exact (case-insensitive) name/context values that identify the DNS Preview
# check. "DNS Preview" is the workflow name in .github/workflows/preview.yml
# (Checks API `name`); "dns-preview" is the equivalent legacy Status API
# `context` form. split_pr_checks matches these exactly - not a "preview"
# substring - so an unrelated future check merely named e.g. "Feature Preview
# Deploy" can never be mistaken for the DNS Preview merge gate.
DNS_PREVIEW_CHECK_NAMES = {"dns preview", "dns-preview"}

# Prompt label for each record type's "value" field, shared by `record add`
# and `record edit` (previously duplicated verbatim between the two).
RECORD_VALUE_LABELS = {
    "A": "IPv4 address",
    "CNAME": "target hostname",
    "MX": "mail server hostname",
    "TXT": "text value",
    "CAA": "CAA value (e.g. letsencrypt.org, or mailto:... for iodef)",
}


def ensure_trailing_dot(value: str, interactive: bool) -> str:
    """CNAME/MX targets in dnsconfig.js are conventionally written with a
    trailing dot. If `value` is missing one, either ask (interactive) or
    append it and note the change (non-interactive). Shared by `record add`
    and `record edit`."""
    if not value or value.endswith("."):
        return value
    if interactive:
        if prompt_yes_no(f"'{value}' has no trailing dot - add one?", default_yes=True):
            return value + "."
        return value
    value += "."
    print(f"note: appended trailing dot -> {value}")
    return value


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug[:max_len].rstrip("-")) or "change"


def parse_index_arg(text: str) -> list[int]:
    """Parse one --remove token: a plain int ("3"), a range ("1-6"), or
    comma-separated combos of either ("1,2,4-6"). Returns the expanded ints."""
    result = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)-(\d+)", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            result.extend(range(lo, hi + 1))
            continue
        if not re.fullmatch(r"\d+", part):
            raise argparse.ArgumentTypeError(
                f"invalid index or range: {part!r} (expected an integer like '3' or a range like '1-6')"
            )
        result.append(int(part))
    return result


def prompt(message: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{message}{suffix}: ").strip()
    return value or (default or "")


def prompt_yes_no(message: str, default_yes: bool = True) -> bool:
    suffix = " [Y/n]" if default_yes else " [y/N]"
    answer = input(f"{message}{suffix}: ").strip().lower()
    if not answer:
        return default_yes
    return answer == "y"


def confirm_or_abort(prompt_text: str, expected: str, *, normalize: bool = False) -> bool:
    """Ask the user to type `expected` to continue (via `input(prompt_text)`),
    returning True only on an exact match. With normalize=True the typed
    answer is `.strip().lower()`-ed first, for the "...? [y/N]: " style
    confirmations (expected="y") as opposed to the "Type "X" to continue"
    style (expected e.g. "APPLY", compared verbatim). Callers are responsible
    for printing "Aborted." and returning their own exit code on False, since
    that varies by call site."""
    answer = input(prompt_text)
    if normalize:
        answer = answer.strip().lower()
    return answer == expected


def get_dnsctl_setting(local_env: dict, name: str) -> str:
    """Look up a DNSCTL_* team-mode setting, checking `local_env` (.env's
    parsed contents) first and falling back to the process environment - the
    same precedence has_cloudflare_token/CLOUDFLARE_KEYVAULT_NAME already use
    elsewhere, so setting these via a shared CI/runner environment instead of
    a literal .env file actually takes effect instead of silently no-op'ing."""
    return local_env.get(name) or os.environ.get(name, "")


def resolve_zone_arg(zone: str | None, zones: list[str]) -> bool:
    """Validate an optional --zone argument against the project's known
    `zones`. Returns True if `zone` is falsy (no filter requested) or a
    recognized zone; otherwise prints the standard "not a zone this project
    manages" error and returns False, for the caller to `return 1` on."""
    if zone and zone not in zones:
        eprint(f"error: '{zone}' is not a zone this project manages ({', '.join(zones)}).")
        return False
    return True


def split_pr_checks(checks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split a PR's `statusCheckRollup` into (preview_checks, other_failed):
    the DNS Preview check(s) (matched by exact name/context against
    DNS_PREVIEW_CHECK_NAMES, case-insensitively), and any other check that
    isn't in a passing/neutral state. Shared by `status` (reporting) and
    `merge` (the merge gate)."""
    preview_checks = [
        c for c in checks
        if (c.get("name") or c.get("context") or "").lower() in DNS_PREVIEW_CHECK_NAMES
    ]
    other_failed = [
        c for c in checks
        if c not in preview_checks
        and (c.get("conclusion") or c.get("state")) not in (None, "SUCCESS", "SKIPPED", "NEUTRAL")
    ]
    return preview_checks, other_failed


def find_non_author_approval(reviews: list[dict], author_login: str) -> str | None:
    """Given a PR's `reviews` (gh's `--json reviews` shape: each item has
    `author: {login}`, `state`, `submittedAt`) and the PR author's login,
    return the login of a reviewer other than the author whose latest review
    is APPROVED, or None if there isn't one.

    Mirrors the jq pipeline in apply.yml's "Require an approving review from
    someone other than the PR author" step exactly - sort by submission time,
    keep only each reviewer's latest review (a later CHANGES_REQUESTED/COMMENTED
    supersedes an earlier APPROVED, and vice versa), then look for an approval
    from someone who isn't the author. `dnsctl merge`'s local mirror reuses
    this so the two can't drift apart from reimplementing the same logic
    twice in bash and Python."""
    latest_by_login: dict[str, dict] = {}
    for review in sorted(reviews, key=lambda r: r.get("submittedAt") or ""):
        login = (review.get("author") or {}).get("login")
        if login:
            latest_by_login[login] = review
    for login, review in latest_by_login.items():
        if login != author_login and review.get("state") == "APPROVED":
            return login
    return None
