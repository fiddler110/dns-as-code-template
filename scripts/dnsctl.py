#!/usr/bin/env python3
"""
dnsctl - cross-platform helper for this dnscontrol/Cloudflare project.

Wraps the common operations documented in docs/ (setup, preview, push,
zone re-baseline, dnscontrol install) behind one script that works the
same way on Windows, macOS, and Linux. Standard library only - no pip
install required.

Usage:
    python scripts/dnsctl.py <command> [options]

Commands:
    doctor              Check that the local environment is set up correctly.
    setup               One-time local setup: git hook + .env scaffold.
    install-dnscontrol  Download the pinned dnscontrol release for this OS/arch.
    preview             Run `dnscontrol preview` (loads .env automatically).
    push                Run `dnscontrol push` (requires confirmation).
    import              Snapshot the live Cloudflare zone to a JS file for
                         manual merging back into dnsconfig.js.
    submit              Commit your dnsconfig.js change, push a branch, and
                         open a pull request for review.
    status              List open pull requests and their DNS Preview check status.
    review              Show a PR's file diff and its DNS Preview comment.
    approve             Approve a PR (GitHub blocks approving your own PR - see below).
    merge               Merge a PR once its DNS Preview check has passed.
    record add          Interactively add a record to dnsconfig.js, e.g.
                         `record add plex.example.com`.
    record remove       Interactively remove a record from dnsconfig.js.
    record list         List records currently in dnsconfig.js.
    record edit         Change an existing record's value/priority/proxy/TTL in place.
    record update-ip    Bulk-replace an IP across every A record that points at it.
    record prune-acme   List/remove stale _acme-challenge TXT records.
    lint                Fast offline sanity checks on dnsconfig.js.
    show                Table view of all records (terminal, CSV, or Markdown).

The submit/status/review/approve/merge commands require the GitHub CLI
(`gh`, https://cli.github.com/), authenticated against this repo.

Run `python scripts/dnsctl.py <command> --help` for command-specific options.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

# Keep in sync with DNSCONTROL_VERSION in .github/workflows/preview.yml and apply.yml.
DNSCONTROL_VERSION = "4.46.0"
CREDKEY = "cloudflare"
# Every zone managed in dnsconfig.js. Keep this in sync with the D("...", ...)
# blocks there - the record wizard needs it to figure out which zone a given
# name belongs to (and to require --zone when a bare relative name is ambiguous).
ZONES = ["example.com", "example.org"]

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
CREDS_FILE = REPO_ROOT / "creds.json"
DNSCONFIG_FILE = REPO_ROOT / "dnsconfig.js"


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def load_env_file(path: Path) -> dict:
    """Minimal KEY=VALUE .env parser. No external dependency required."""
    env = {}
    if not path.is_file():
        return env
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            env[key] = value
    return env


def find_dnscontrol() -> str | None:
    found = shutil.which("dnscontrol")
    if found:
        return found
    # go install puts binaries in $GOBIN or $GOPATH/bin, which may not be on
    # PATH yet in this shell session - check the common locations directly.
    candidates = []
    gopath = os.environ.get("GOPATH")
    home = Path.home()
    if gopath:
        candidates.append(Path(gopath) / "bin" / exe_name("dnscontrol"))
    candidates.append(home / "go" / "bin" / exe_name("dnscontrol"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def exe_name(name: str) -> str:
    return f"{name}.exe" if platform.system() == "Windows" else name


def run_dnscontrol(args: list[str], env_overrides: dict) -> int:
    dnscontrol = find_dnscontrol()
    if not dnscontrol:
        eprint(
            "error: dnscontrol not found on PATH.\n"
            "  Run: python scripts/dnsctl.py install-dnscontrol\n"
            "  or:  go install github.com/DNSControl/dnscontrol/v4@latest"
        )
        return 1

    env = os.environ.copy()
    env.update(env_overrides)

    proc = subprocess.run([dnscontrol, *args], cwd=REPO_ROOT, env=env)
    return proc.returncode


def have_gh() -> bool:
    return shutil.which("gh") is not None


def require_gh() -> bool:
    if have_gh():
        return True
    eprint("error: GitHub CLI ('gh') not found on PATH. Install it: https://cli.github.com/")
    return False


def git(args: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=capture, text=capture
    )


def git_output(args: list[str]) -> str:
    return git(args, capture=True).stdout.strip()


def gh(args: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args], cwd=REPO_ROOT, capture_output=capture, text=capture
    )


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug[:max_len].rstrip("-")) or "change"


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


# A record name is either "@" (apex), "*" (wildcard), or dot-separated labels
# made of letters/digits/hyphen/underscore (each label non-empty, no leading/
# trailing hyphen requirement enforced - Cloudflare/dnscontrol will reject
# anything actually invalid at preview time regardless).
VALID_NAME_PATTERN = re.compile(r'^(\*|[A-Za-z0-9_](?:[A-Za-z0-9_-]*[A-Za-z0-9_])?)'
                                 r'(\.[A-Za-z0-9_](?:[A-Za-z0-9_-]*[A-Za-z0-9_])?)*$')


def detect_zone(spec: str) -> str | None:
    """Return the zone from ZONES that `spec` is fully-qualified under, if any."""
    lower_spec = spec.strip().rstrip(".").lower()
    for z in ZONES:
        lz = z.lower()
        if lower_spec == lz or lower_spec.endswith("." + lz):
            return z
    return None


def parse_record_target(spec: str, zone: str | None = None) -> tuple[str, str]:
    """
    Parse an input like "plex.example.com", "www.plex.example.com",
    "example.com" (apex), "*.example.com" (wildcard), or an
    already-relative name like "plex" or "www.plex", into (name, zone) -
    name is what dnscontrol expects ("@" for the apex), zone is one of ZONES.
    Case-insensitive; tolerates a trailing dot on a fully-qualified name.

    If `zone` is given, it's used directly (and validated against ZONES) -
    required for a bare relative name when more than one zone is managed,
    since e.g. "plex" alone doesn't say which zone it belongs to.
    """
    spec = spec.strip().rstrip(".")
    if not spec:
        raise ValueError("record name cannot be empty.")
    lower_spec = spec.lower()

    if zone:
        if zone not in ZONES:
            raise ValueError(f"'{zone}' is not a zone this project manages ({', '.join(ZONES)}).")
        target_zone = zone
    else:
        target_zone = detect_zone(spec)
        if target_zone is None:
            if "." not in lower_spec:
                raise ValueError(
                    f"'{spec}' is a bare relative name, and this project manages more than "
                    f"one zone ({', '.join(ZONES)}) - pass --zone to say which one."
                )
            raise ValueError(
                f"'{spec}' doesn't match any zone this project manages ({', '.join(ZONES)})."
            )

    lower_zone = target_zone.lower()
    if lower_spec == lower_zone:
        name = "@"
    else:
        suffix = "." + lower_zone
        if lower_spec.endswith(suffix):
            name = spec[: -len(suffix)].lower()
            name = name or "@"
        else:
            # No zone suffix present - the caller pinned the zone via --zone,
            # so treat the whole spec as an already-relative name (e.g. "plex"
            # or the compound "www.plex").
            name = lower_spec

    if name != "@" and not VALID_NAME_PATTERN.match(name):
        raise ValueError(
            f"'{name}' doesn't look like a valid DNS label/name "
            "(letters, digits, hyphens, underscores, and dots only)."
        )
    return name, target_zone


def fqdn_for(name: str, zone: str) -> str:
    return zone if name == "@" else f"{name}.{zone}"


# Only actual record-type functions - deliberately excludes D(...) (the domain
# declaration itself), which would otherwise match "D(" as a one-letter type.
KNOWN_RECORD_TYPES = ("A", "AAAA", "CNAME", "MX", "TXT", "NS", "CAA", "SRV", "PTR", "ALIAS")
RECORD_LINE_PATTERN = re.compile(
    r'^\s*(' + "|".join(KNOWN_RECORD_TYPES) + r')\(\s*"((?:[^"\\]|\\.)*)"'
)
FULL_RECORD_LINE_PATTERN = re.compile(
    r'^\s*(' + "|".join(KNOWN_RECORD_TYPES) + r')\((.*)\),\s*$'
)


def split_top_level_args(argstr: str) -> list[str]:
    """Split a call's argument text on top-level commas (ignores commas
    inside quoted strings or nested parens like TTL(120))."""
    args = []
    cur = []
    depth = 0
    in_str = False
    escape = False
    for ch in argstr:
        if in_str:
            cur.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            cur.append(ch)
        elif ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        args.append("".join(cur).strip())
    return args


def parse_record_line_full(line: str) -> dict | None:
    """Parse one record line into its constituent fields for reporting.

    Best-effort: covers the modifiers actually used in this project
    (CF_PROXY_ON/OFF, TTL(n)) plus positional name/value(s)/MX priority.
    """
    m = FULL_RECORD_LINE_PATTERN.match(line)
    if not m:
        return None
    record_type, argstr = m.group(1), m.group(2)
    raw_args = split_top_level_args(argstr)
    if not raw_args:
        return None

    def unquote(a: str) -> str:
        if a.startswith('"'):
            try:
                return json.loads(a)
            except (json.JSONDecodeError, ValueError):
                return a.strip('"')
        return a

    proxied = ""
    ttl = ""
    value_parts = []
    positional = []
    for a in raw_args[1:]:
        if a == "CF_PROXY_ON":
            proxied = "yes"
        elif a == "CF_PROXY_OFF":
            proxied = "no"
        elif a.startswith("TTL(") and a.endswith(")"):
            ttl = a[len("TTL("):-1].strip()
        else:
            positional.append(a)

    name = unquote(raw_args[0])
    priority = ""
    if record_type == "MX" and positional:
        priority = positional.pop(0)
    value_parts = [unquote(p) for p in positional]
    value = " ".join(value_parts)

    return {
        "type": record_type,
        "name": name,
        "value": value,
        "priority": priority,
        "ttl": ttl,
        "proxied": proxied,
        "raw": line.strip(),
    }


def find_zone_block(zone: str) -> tuple[int, int]:
    """Return (start, end) line indices of D("zone", ...) ... ); in dnsconfig.js."""
    lines = DNSCONFIG_FILE.read_text(encoding="utf-8").splitlines()
    start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f'D("{zone}"') or stripped.startswith(f"D('{zone}'"):
            start = i
            break
    if start is None:
        raise RuntimeError(f'Could not find D("{zone}", ...) in {DNSCONFIG_FILE.name}.')
    for j in range(start + 1, len(lines)):
        if lines[j].strip() == ");":
            return start, j
    raise RuntimeError(f'Could not find the closing ");" for zone {zone} in {DNSCONFIG_FILE.name}.')


def build_record_line(
    record_type: str,
    name: str,
    value: str,
    priority: int | None = None,
    proxy: bool | None = None,
    ttl: int | None = None,
) -> str:
    quoted_name = json.dumps(name)
    quoted_value = json.dumps(value)

    if record_type in ("A", "CNAME"):
        parts = [quoted_name, quoted_value]
        if proxy:
            parts.append("CF_PROXY_ON")
        if ttl:
            parts.append(f"TTL({ttl})")
        return f'\t{record_type}({", ".join(parts)}),'

    if record_type == "MX":
        if priority is None:
            raise ValueError("MX records require a priority.")
        return f"\tMX({quoted_name}, {priority}, {quoted_value}),"

    if record_type == "TXT":
        parts = [quoted_name, quoted_value]
        if ttl:
            parts.append(f"TTL({ttl})")
        return f'\tTXT({", ".join(parts)}),'

    raise ValueError(
        f"The record wizard doesn't support {record_type} records yet - "
        "edit dnsconfig.js directly for this type. See docs/record-types.md."
    )


def find_record_lines(
    name: str, zone: str, record_type: str | None = None
) -> list[tuple[int, str]]:
    """Find matching record lines within `zone`'s D(...) block only - not the whole file,
    since the same relative name can validly exist in more than one zone's block."""
    start, end = find_zone_block(zone)
    lines = DNSCONFIG_FILE.read_text(encoding="utf-8").splitlines()
    matches = []
    for i in range(start + 1, end):
        m = RECORD_LINE_PATTERN.match(lines[i])
        if not m:
            continue
        line_type, line_name = m.group(1), m.group(2)
        if line_name != name:
            continue
        if record_type and line_type != record_type:
            continue
        matches.append((i, lines[i]))
    return matches


def insert_record_line(line: str, zone: str) -> None:
    _, end = find_zone_block(zone)
    lines = DNSCONFIG_FILE.read_text(encoding="utf-8").splitlines()
    lines.insert(end, line)
    DNSCONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def remove_line_at(index: int) -> None:
    lines = DNSCONFIG_FILE.read_text(encoding="utf-8").splitlines()
    del lines[index]
    DNSCONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def replace_line_at(index: int, new_line: str) -> None:
    """Overwrite one line in place. Safe to call once per index computed from
    the same read - unlike insert/remove, this never shifts other line numbers."""
    lines = DNSCONFIG_FILE.read_text(encoding="utf-8").splitlines()
    lines[index] = new_line
    DNSCONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def offer_preview_and_submit(default_message: str, interactive: bool) -> None:
    """After editing dnsconfig.js, optionally run preview and hand off to submit."""
    if not interactive:
        print(
            "\nNext: python scripts/dnsctl.py preview   (verify the diff)\n"
            '      python scripts/dnsctl.py submit "..." (open a PR)'
        )
        return

    if prompt_yes_no("\nRun `dnscontrol preview` now to verify?", default_yes=True):
        cmd_preview(argparse.Namespace())

    if prompt_yes_no("\nOpen a pull request for this change now?", default_yes=False):
        message = prompt("Commit message / PR title", default=default_message)
        submit_args = argparse.Namespace(
            message=message,
            files=["dnsconfig.js"],
            branch=None,
            base="main",
            body=None,
            skip_preview=True,
            yes=True,
        )
        cmd_submit(submit_args)


def cmd_doctor(_args) -> int:
    ok = True

    print("== dnsctl doctor ==")

    dnscontrol = find_dnscontrol()
    if dnscontrol:
        version_proc = subprocess.run(
            [dnscontrol, "version"], capture_output=True, text=True
        )
        version = version_proc.stdout.strip() or version_proc.stderr.strip()
        print(f"[ok]   dnscontrol found: {dnscontrol} ({version})")
    else:
        print("[FAIL] dnscontrol not found on PATH or in the usual go install location.")
        print("       Fix: python scripts/dnsctl.py install-dnscontrol")
        ok = False

    if ENV_FILE.is_file():
        env = load_env_file(ENV_FILE)
        token = env.get("CLOUDFLARE_API_TOKEN")
        if token:
            print(f"[ok]   .env found with CLOUDFLARE_API_TOKEN set (length {len(token)}).")
        else:
            print("[FAIL] .env exists but CLOUDFLARE_API_TOKEN is missing or empty.")
            ok = False
        if env.get("CLOUDFLARE_API_TOKEN_WRITE"):
            print(
                "[warn] .env contains CLOUDFLARE_API_TOKEN_WRITE. The write token should "
                "normally only exist as a GitHub Actions secret - see docs/security.md."
            )
    else:
        print("[FAIL] .env not found.")
        print(f"       Fix: copy {ENV_EXAMPLE.name} to .env and fill in the read-only token.")
        ok = False

    if CREDS_FILE.is_file():
        contents = CREDS_FILE.read_text(encoding="utf-8")
        if '"apitoken"' in contents:
            print("[ok]   creds.json uses the correct 'apitoken' key.")
        else:
            print(
                "[FAIL] creds.json does not contain the literal key \"apitoken\" - "
                "dnscontrol's Cloudflare provider requires this exact field name. "
                "See docs/operations.md#troubleshooting."
            )
            ok = False
    else:
        print("[FAIL] creds.json not found.")
        ok = False

    hooks_path = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if hooks_path in (".githooks", str(REPO_ROOT / ".githooks")):
        print(f"[ok]   git hooks path is set to '{hooks_path}'.")
    else:
        print("[warn] git core.hooksPath is not set to .githooks - the pre-push safety "
              "check will not run.")
        print("       Fix: git config core.hooksPath .githooks")

    print()
    if ok:
        print("Environment looks good.")
    else:
        print("One or more checks failed - see [FAIL] lines above.")
    return 0 if ok else 1


def cmd_setup(_args) -> int:
    print("Setting up local environment...")

    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=REPO_ROOT, check=True)
    print("[ok] git core.hooksPath set to .githooks")

    if ENV_FILE.is_file():
        print("[ok] .env already exists (leaving it as-is)")
    else:
        if not ENV_EXAMPLE.is_file():
            eprint("error: .env.example not found - cannot scaffold .env")
            return 1
        shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
        print(f"[ok] created .env from {ENV_EXAMPLE.name} - edit it and fill in your "
              "read-only Cloudflare API token")

    print()
    print("Next: install dnscontrol if needed (python scripts/dnsctl.py install-dnscontrol),")
    print("then fill in .env and run: python scripts/dnsctl.py doctor")
    return 0


def platform_asset_name(version: str) -> str | None:
    system = platform.system()
    machine = platform.machine().lower()

    arch_map = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    arch = arch_map.get(machine)
    if not arch:
        return None

    if system == "Linux":
        return f"dnscontrol_{version}_linux_{arch}.tar.gz"
    if system == "Darwin":
        return f"dnscontrol_{version}_darwin_all.tar.gz"
    if system == "Windows":
        return f"dnscontrol_{version}_windows_{arch}.zip"
    return None


def cmd_install_dnscontrol(args) -> int:
    version = args.version
    asset = platform_asset_name(version)
    if not asset:
        eprint(
            f"error: no known dnscontrol release asset for {platform.system()} "
            f"{platform.machine()}. Download manually from "
            "https://github.com/DNSControl/dnscontrol/releases"
        )
        return 1

    dest_dir = Path(args.dest).expanduser().resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / exe_name("dnscontrol")

    url = f"https://github.com/DNSControl/dnscontrol/releases/download/v{version}/{asset}"
    print(f"Downloading {url} ...")

    tmp_archive = dest_dir / asset
    urllib.request.urlretrieve(url, tmp_archive)

    print(f"Extracting dnscontrol binary to {dest_path} ...")
    if asset.endswith(".zip"):
        with zipfile.ZipFile(tmp_archive) as zf:
            with zf.open(exe_name("dnscontrol")) as src, open(dest_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(tmp_archive, "r:gz") as tf:
            member = tf.getmember("dnscontrol")
            with tf.extractfile(member) as src, open(dest_path, "wb") as dst:
                shutil.copyfileobj(src, dst)

    tmp_archive.unlink()

    if platform.system() != "Windows":
        current = os.stat(dest_path).st_mode
        os.chmod(dest_path, current | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    print(f"[ok] installed dnscontrol {version} to {dest_path}")
    if str(dest_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"Note: {dest_dir} is not on your PATH. Add it, or reference the binary "
              "directly, to use the 'dnscontrol' command outside this script.")
    return 0


def cmd_preview(_args) -> int:
    env = load_env_file(ENV_FILE)
    if "CLOUDFLARE_API_TOKEN" not in env and "CLOUDFLARE_API_TOKEN" not in os.environ:
        eprint("error: CLOUDFLARE_API_TOKEN not set in .env or the environment.")
        eprint("       Run: python scripts/dnsctl.py setup")
        return 1
    return run_dnscontrol(["preview"], env)


def cmd_push(args) -> int:
    env = load_env_file(ENV_FILE)
    if "CLOUDFLARE_API_TOKEN" not in env and "CLOUDFLARE_API_TOKEN" not in os.environ:
        eprint("error: CLOUDFLARE_API_TOKEN not set in .env or the environment.")
        return 1

    print(
        "WARNING: this will apply changes directly to the live Cloudflare zones "
        f"managed here ({', '.join(ZONES)}). The normal workflow is: open a PR, review "
        "the DNS Preview comment, and merge to main so the apply.yml GitHub Actions "
        "workflow applies it with the write-scoped token. Running this locally requires "
        "the write token to be set yourself for this one command (e.g. via "
        "CLOUDFLARE_API_TOKEN_WRITE in .env) - see docs/security.md before doing this."
    )
    if not args.yes:
        confirm = input('Type "APPLY" to continue, anything else to abort: ')
        if confirm != "APPLY":
            print("Aborted.")
            return 1

    write_token = env.get("CLOUDFLARE_API_TOKEN_WRITE") or os.environ.get(
        "CLOUDFLARE_API_TOKEN_WRITE"
    )
    overrides = dict(env)
    if write_token:
        overrides["CLOUDFLARE_API_TOKEN"] = write_token

    return run_dnscontrol(["push"], overrides)


def cmd_import(args) -> int:
    env = load_env_file(ENV_FILE)
    if "CLOUDFLARE_API_TOKEN" not in env and "CLOUDFLARE_API_TOKEN" not in os.environ:
        eprint("error: CLOUDFLARE_API_TOKEN not set in .env or the environment.")
        return 1

    zone = args.zone
    if not zone:
        if len(ZONES) == 1:
            zone = ZONES[0]
        else:
            eprint(f"error: --zone is required (this project manages: {', '.join(ZONES)}).")
            return 1
    elif zone not in ZONES:
        eprint(f"error: '{zone}' is not a zone this project manages ({', '.join(ZONES)}).")
        return 1

    out_path = args.out
    print(f"Snapshotting live zone '{zone}' to {out_path} ...")
    rc = run_dnscontrol(
        ["get-zones", "--format=js", f"--out={out_path}", CREDKEY, zone], env
    )
    if rc == 0:
        print(f"[ok] wrote {out_path}")
        print("Now manually merge any new/changed records into dnsconfig.js, delete "
              f"{out_path}, and run `python scripts/dnsctl.py preview` to confirm "
              "0 corrections before committing. See docs/operations.md#re-baselining-the-zone.")
    return rc


def cmd_submit(args) -> int:
    if not require_gh():
        return 1

    status = git_output(["status", "--porcelain"])
    if not status:
        eprint("error: no local changes to commit.")
        return 1

    if not args.skip_preview:
        print("Running dnscontrol preview to sanity-check your change before committing...\n")
        rc = cmd_preview(args)
        print()
        if rc != 0:
            eprint("error: dnscontrol preview failed - fix the error above before submitting.")
            return 1
        if not args.yes:
            confirm = input("Does the diff above look correct? [y/N]: ").strip().lower()
            if confirm != "y":
                print("Aborted.")
                return 1

    current_branch = git_output(["rev-parse", "--abbrev-ref", "HEAD"])
    base_branch = args.base
    created_branch = False

    if current_branch == base_branch:
        branch = args.branch or f"dns/{slugify(args.message)}"
        print(f"Creating branch '{branch}' from '{base_branch}'...")
        if git(["checkout", "-b", branch]).returncode != 0:
            return 1
        created_branch = True
    else:
        branch = current_branch
        print(f"Already on branch '{branch}' - using it.")

    files = args.files or ["dnsconfig.js"]
    git(["add", *files])

    staged = git_output(["diff", "--cached", "--name-only"])
    if not staged:
        eprint(f"error: nothing staged from {files} - check --files matches your edited file(s).")
        if created_branch:
            git(["checkout", base_branch])
            git(["branch", "-D", branch])
        return 1

    print(f"Staged: {', '.join(staged.splitlines())}")

    if git(["commit", "-m", args.message]).returncode != 0:
        return 1

    print(f"Pushing '{branch}'...")
    push_result = subprocess.run(["git", "push", "-u", "origin", branch], cwd=REPO_ROOT)
    if push_result.returncode != 0:
        eprint("error: git push failed (see above).")
        return push_result.returncode

    print("Opening pull request...")
    pr_args = [
        "pr", "create",
        "--title", args.message,
        "--body", args.body or "",
        "--base", base_branch,
        "--head", branch,
    ]
    result = gh(pr_args)
    if result.returncode != 0:
        eprint(result.stderr.strip() or result.stdout.strip())
        return result.returncode
    print(result.stdout.strip())

    print(
        "\nNext: wait for the 'DNS Preview' check (python scripts/dnsctl.py status), "
        "review it (python scripts/dnsctl.py review <PR#>), then "
        "python scripts/dnsctl.py merge <PR#>."
    )
    return 0


def cmd_status(_args) -> int:
    if not require_gh():
        return 1

    result = gh([
        "pr", "list", "--state", "open",
        "--json", "number,title,headRefName,url,isDraft,statusCheckRollup",
    ])
    if result.returncode != 0:
        eprint(result.stderr.strip())
        return result.returncode

    prs = json.loads(result.stdout)
    if not prs:
        print("No open pull requests.")
        return 0

    for pr in prs:
        checks = pr.get("statusCheckRollup") or []
        preview_checks = [
            c for c in checks
            if "preview" in (c.get("name") or c.get("context") or "").lower()
        ]
        if preview_checks:
            states = sorted({
                (c.get("conclusion") or c.get("state") or "unknown") for c in preview_checks
            })
            check_state = ", ".join(states)
        else:
            check_state = "no DNS Preview check found yet"

        draft = " [draft]" if pr.get("isDraft") else ""
        print(f"#{pr['number']}{draft}: {pr['title']}")
        print(f"    branch: {pr['headRefName']}")
        print(f"    DNS Preview: {check_state}")
        print(f"    {pr['url']}")
    return 0


def cmd_review(args) -> int:
    if not require_gh():
        return 1

    number = str(args.pr)

    print(f"=== dnsconfig.js diff for PR #{number} ===")
    diff_result = gh(["pr", "diff", number])
    if diff_result.returncode != 0:
        eprint(diff_result.stderr.strip())
        return diff_result.returncode
    print(diff_result.stdout or "(no diff)")

    print(f"\n=== DNS Preview comment for PR #{number} ===")
    view_result = gh(["pr", "view", number, "--json", "comments"])
    if view_result.returncode != 0:
        eprint(view_result.stderr.strip())
        return view_result.returncode

    data = json.loads(view_result.stdout)
    comments = data.get("comments", [])
    preview_comments = [
        c for c in comments if "dnscontrol preview" in (c.get("body") or "").lower()
    ]
    if preview_comments:
        print(preview_comments[-1]["body"])
    else:
        print("No DNS Preview comment yet - the check may still be running.")
    return 0


def cmd_approve(args) -> int:
    if not require_gh():
        return 1

    number = str(args.pr)
    result = gh(["pr", "review", number, "--approve", "--body", args.body or ""])
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "own pull request" in stderr.lower():
            print(
                "GitHub does not allow approving your own pull request - this will "
                "always happen on a solo-maintained repo like this one.\n"
                "This repo has no required-review branch rule, so approval isn't "
                "needed to merge anyway - once the DNS Preview check has passed, run:\n"
                f"  python scripts/dnsctl.py merge {number}"
            )
            return 1
        eprint(stderr or result.stdout.strip())
        return result.returncode
    print(result.stdout.strip())
    return 0


def cmd_merge(args) -> int:
    if not require_gh():
        return 1

    number = str(args.pr)

    view_result = gh(["pr", "view", number, "--json", "state,statusCheckRollup,title"])
    if view_result.returncode != 0:
        eprint(view_result.stderr.strip())
        return view_result.returncode

    data = json.loads(view_result.stdout)
    if data.get("state") != "OPEN":
        eprint(f"error: PR #{number} is not open (state: {data.get('state')}).")
        return 1

    checks = data.get("statusCheckRollup") or []
    preview_checks = [
        c for c in checks
        if "preview" in (c.get("name") or c.get("context") or "").lower()
    ]
    if not preview_checks:
        print("warning: no 'DNS Preview' check found yet on this PR.")
    else:
        failed = [c for c in preview_checks if (c.get("conclusion") or c.get("state")) != "SUCCESS"]
        if failed:
            eprint(
                f"error: DNS Preview check has not succeeded for PR #{number}. "
                f"Run: python scripts/dnsctl.py review {number}"
            )
            if not args.force:
                return 1
            print("warning: --force set, merging anyway.")

    print(f'PR #{number}: "{data.get("title")}"')
    print(
        "Merging will trigger the 'DNS Apply' GitHub Actions workflow, which applies "
        f"this change to the live Cloudflare zone(s) managed here ({', '.join(ZONES)})."
    )
    if not args.yes:
        confirm = input('Type "MERGE" to continue, anything else to abort: ')
        if confirm != "MERGE":
            print("Aborted.")
            return 1

    result = subprocess.run(
        ["gh", "pr", "merge", number, "--merge", "--delete-branch"], cwd=REPO_ROOT
    )
    if result.returncode == 0:
        print("Merged. Watch the 'DNS Apply' workflow run to confirm it applied cleanly.")
    return result.returncode


def cmd_record_add(args) -> int:
    try:
        name, zone = parse_record_target(args.target, zone=args.zone)
    except ValueError as e:
        eprint(f"error: {e}")
        return 1

    interactive = not args.yes

    record_type = (args.type or "").upper()
    if not record_type:
        if not interactive:
            eprint("error: --yes requires --type to be provided (no prompts allowed).")
            return 1
        record_type = prompt("Record type (A, CNAME, MX, TXT)", default="CNAME").upper()
    if record_type not in ("A", "CNAME", "MX", "TXT"):
        eprint(
            f"error: unsupported record type '{record_type}'. This wizard supports "
            "A, CNAME, MX, TXT - edit dnsconfig.js directly for anything else "
            "(see docs/record-types.md)."
        )
        return 1

    value = args.value
    priority = args.priority
    proxy = args.proxy
    ttl = args.ttl

    if value is None:
        if not interactive:
            eprint("error: --yes requires --value to be provided (no prompts allowed).")
            return 1
        label = {
            "A": "IPv4 address",
            "CNAME": "target hostname",
            "MX": "mail server hostname",
            "TXT": "text value",
        }[record_type]
        value = prompt(f"{label} for {fqdn_for(name, zone)}")

    if record_type in ("CNAME", "MX") and value and not value.endswith("."):
        if interactive:
            if prompt_yes_no(f"'{value}' has no trailing dot - add one?", default_yes=True):
                value += "."
        else:
            value += "."
            print(f"note: appended trailing dot -> {value}")

    if record_type == "MX" and priority is None:
        if not interactive:
            eprint("error: --yes with an MX record requires --priority.")
            return 1
        priority = int(prompt("Priority (lower number = preferred)", default="10"))

    if record_type in ("A", "CNAME") and proxy is None:
        if interactive:
            proxy = prompt_yes_no("Proxy through Cloudflare (orange cloud)?", default_yes=True)
        else:
            proxy = True
            print("note: defaulting to proxy ON (pass --no-proxy to disable)")

    if record_type == "TXT" and ttl is None and interactive:
        ttl_input = prompt("TTL override in seconds (blank = zone default)", default="")
        ttl = int(ttl_input) if ttl_input else None

    try:
        line = build_record_line(
            record_type, name, value, priority=priority, proxy=proxy, ttl=ttl
        )
    except ValueError as e:
        eprint(f"error: {e}")
        return 1

    start, end = find_zone_block(zone)
    zone_lines = DNSCONFIG_FILE.read_text(encoding="utf-8").splitlines()[start + 1 : end]
    if line.strip() in {existing_line.strip() for existing_line in zone_lines}:
        eprint(
            f"error: this exact record already exists in the {zone} block of "
            f"{DNSCONFIG_FILE.name} - nothing to add."
        )
        return 1

    same_name_other_type = [
        (i, l) for i, l in find_record_lines(name, zone)
        if RECORD_LINE_PATTERN.match(l).group(1) != record_type
    ]
    if same_name_other_type:
        existing_types = sorted({RECORD_LINE_PATTERN.match(l).group(1) for _, l in same_name_other_type})
        if record_type == "CNAME" or "CNAME" in existing_types:
            print(
                f"\nWARNING: {fqdn_for(name, zone)} already has {'/'.join(existing_types)} record(s), "
                "and CNAME can't coexist with any other record type on the same name "
                "(DNS-wide rule, not specific to this project). Cloudflare will likely reject this."
            )
            for _, existing_line in same_name_other_type:
                print(f"  {existing_line.strip()}")

    existing_same_type = find_record_lines(name, zone, record_type)
    if existing_same_type:
        print(f"\nNote: {len(existing_same_type)} existing {record_type} record(s) already use this name:")
        for _, existing_line in existing_same_type:
            print(f"  {existing_line.strip()}")

    print(f"\nFull name: {fqdn_for(name, zone)}")
    print(f"About to add this line to the {zone} block of {DNSCONFIG_FILE.name}:")
    print(f"  {line}")
    if interactive and not prompt_yes_no("Add it?", default_yes=True):
        print("Aborted.")
        return 1

    insert_record_line(line, zone)
    print(f"[ok] added to {DNSCONFIG_FILE.name}")
    offer_preview_and_submit(f"Add {record_type} for {fqdn_for(name, zone)}", interactive=interactive)
    return 0


def cmd_record_edit(args) -> int:
    try:
        name, zone = parse_record_target(args.target, zone=args.zone)
    except ValueError as e:
        eprint(f"error: {e}")
        return 1

    interactive = not args.yes

    type_filter = args.type.upper() if args.type else None
    matches = find_record_lines(name, zone, type_filter)
    if not matches:
        eprint(
            f"error: no records found for '{fqdn_for(name, zone)}'"
            + (f" of type {type_filter}" if type_filter else "")
            + " in dnsconfig.js."
        )
        return 1

    if args.index is not None:
        try:
            selected = matches[args.index]
        except IndexError:
            eprint(f"error: --index {args.index} out of range (found {len(matches)} match(es)).")
            return 1
    elif len(matches) == 1:
        selected = matches[0]
    elif not interactive:
        eprint(
            f"error: {len(matches)} matches for '{fqdn_for(name, zone)}' and --yes was given - "
            "pass --index (see `record list`) or --type to disambiguate."
        )
        for i, (_, line) in enumerate(matches):
            eprint(f"  [{i}] {line.strip()}")
        return 1
    else:
        print(f"Found {len(matches)} matching records:")
        for i, (_, line) in enumerate(matches):
            print(f"  [{i}] {line.strip()}")
        choice = prompt("Which one to edit? (index)")
        try:
            selected = matches[int(choice)]
        except (ValueError, IndexError):
            eprint("error: invalid selection.")
            return 1

    idx, old_line = selected
    parsed = parse_record_line_full(old_line)
    if not parsed:
        eprint("error: could not parse the selected line - edit dnsconfig.js directly.")
        return 1

    record_type = parsed["type"]
    if record_type not in ("A", "CNAME", "MX", "TXT"):
        eprint(
            f"error: editing {record_type} records isn't supported by this wizard - "
            "edit dnsconfig.js directly (see docs/record-types.md)."
        )
        return 1

    value = args.value
    if value is None:
        if not interactive:
            eprint("error: --yes requires --value to be provided (no prompts allowed).")
            return 1
        label = {
            "A": "IPv4 address",
            "CNAME": "target hostname",
            "MX": "mail server hostname",
            "TXT": "text value",
        }[record_type]
        value = prompt(f"{label} for {fqdn_for(name, zone)}", default=parsed["value"])

    if record_type in ("CNAME", "MX") and value and not value.endswith("."):
        if interactive:
            if prompt_yes_no(f"'{value}' has no trailing dot - add one?", default_yes=True):
                value += "."
        else:
            value += "."
            print(f"note: appended trailing dot -> {value}")

    priority = args.priority
    if record_type == "MX" and priority is None:
        current_priority = parsed["priority"] or "10"
        if interactive:
            priority = int(prompt("Priority (lower number = preferred)", default=current_priority))
        else:
            priority = int(current_priority)

    proxy = args.proxy
    if record_type in ("A", "CNAME") and proxy is None:
        current_proxy = parsed["proxied"] == "yes"
        if interactive:
            proxy = prompt_yes_no("Proxy through Cloudflare (orange cloud)?", default_yes=current_proxy)
        else:
            proxy = current_proxy

    ttl = args.ttl
    if ttl is None and parsed["ttl"]:
        ttl = int(parsed["ttl"])
    if record_type == "TXT" and args.ttl is None and interactive:
        ttl_input = prompt("TTL override in seconds (blank = zone default)", default=str(ttl) if ttl else "")
        ttl = int(ttl_input) if ttl_input else None

    try:
        new_line = build_record_line(
            record_type, name, value, priority=priority, proxy=proxy, ttl=ttl
        )
    except ValueError as e:
        eprint(f"error: {e}")
        return 1

    if new_line.strip() == old_line.strip():
        print("No change - the new line is identical to the existing one.")
        return 0

    print(f"\nAbout to change this line in the {zone} block of {DNSCONFIG_FILE.name}:")
    print(f"  - {old_line.strip()}")
    print(f"  + {new_line.strip()}")
    if interactive and not prompt_yes_no("Apply this edit?", default_yes=True):
        print("Aborted.")
        return 1

    replace_line_at(idx, new_line)
    print(f"[ok] updated {DNSCONFIG_FILE.name}")
    offer_preview_and_submit(f"Edit {record_type} for {fqdn_for(name, zone)}", interactive=interactive)
    return 0


def cmd_record_remove(args) -> int:
    try:
        name, zone = parse_record_target(args.target, zone=args.zone)
    except ValueError as e:
        eprint(f"error: {e}")
        return 1

    interactive = not args.yes

    record_type = args.type.upper() if args.type else None
    matches = find_record_lines(name, zone, record_type)
    if not matches:
        eprint(
            f"error: no records found for '{fqdn_for(name, zone)}'"
            + (f" of type {record_type}" if record_type else "")
            + " in dnsconfig.js."
        )
        return 1

    if args.index is not None:
        try:
            selected = matches[args.index]
        except IndexError:
            eprint(f"error: --index {args.index} out of range (found {len(matches)} match(es)).")
            return 1
    elif len(matches) == 1:
        selected = matches[0]
    elif not interactive:
        eprint(
            f"error: {len(matches)} matches for '{fqdn_for(name, zone)}' and --yes was given - "
            "pass --index (see `record list`) or --type to disambiguate."
        )
        for i, (_, line) in enumerate(matches):
            eprint(f"  [{i}] {line.strip()}")
        return 1
    else:
        print(f"Found {len(matches)} matching records:")
        for i, (_, line) in enumerate(matches):
            print(f"  [{i}] {line.strip()}")
        choice = prompt("Which one to remove? (index)")
        try:
            selected = matches[int(choice)]
        except (ValueError, IndexError):
            eprint("error: invalid selection.")
            return 1

    idx, line = selected
    print(f"\nAbout to remove this line from the {zone} block of {DNSCONFIG_FILE.name}:")
    print(f"  {line.strip()}")
    if interactive and not prompt_yes_no("Remove it?", default_yes=False):
        print("Aborted.")
        return 1

    remove_line_at(idx)
    print(f"[ok] removed from {DNSCONFIG_FILE.name}")
    offer_preview_and_submit(f"Remove record for {fqdn_for(name, zone)}", interactive=interactive)
    return 0


def cmd_record_list(args) -> int:
    name_filter = None
    zone_filter = None
    if args.name:
        try:
            name_filter, zone_filter = parse_record_target(args.name, zone=args.zone)
        except ValueError as e:
            eprint(f"error: {e}")
            return 1
    elif args.zone:
        if args.zone not in ZONES:
            eprint(f"error: '{args.zone}' is not a zone this project manages ({', '.join(ZONES)}).")
            return 1
        zone_filter = args.zone

    lines = DNSCONFIG_FILE.read_text(encoding="utf-8").splitlines()
    zones_to_show = [zone_filter] if zone_filter else ZONES

    found = False
    for zone in zones_to_show:
        try:
            start, end = find_zone_block(zone)
        except RuntimeError as e:
            eprint(f"error: {e}")
            continue
        zone_found = False
        for i in range(start + 1, end):
            m = RECORD_LINE_PATTERN.match(lines[i])
            if not m:
                continue
            if name_filter and m.group(2) != name_filter:
                continue
            if not zone_found:
                print(f"# {zone}")
                zone_found = True
            print(lines[i].strip())
            found = True

    if not found:
        print("No matching records." if name_filter else "No records found.")
    return 0


def cmd_record_update_ip(args) -> int:
    if args.zone and args.zone not in ZONES:
        eprint(f"error: '{args.zone}' is not a zone this project manages ({', '.join(ZONES)}).")
        return 1
    zones_to_check = [args.zone] if args.zone else ZONES

    matches = []  # (zone, idx, line, parsed)
    for zone in zones_to_check:
        start, end = find_zone_block(zone)
        lines = DNSCONFIG_FILE.read_text(encoding="utf-8").splitlines()
        for i in range(start + 1, end):
            parsed = parse_record_line_full(lines[i])
            if not parsed:
                continue
            if parsed["type"] == "A" and parsed["value"] == args.old_ip:
                matches.append((zone, i, lines[i], parsed))

    if not matches:
        eprint(f"error: no A records found with value {args.old_ip}.")
        return 1

    interactive = not args.yes

    print(f"Found {len(matches)} A record(s) pointing at {args.old_ip}:")
    for zone, _, line, parsed in matches:
        print(f"  {fqdn_for(parsed['name'], zone)}  ({zone})")
    print(f"\nWould change all of them to {args.new_ip}.")
    if interactive and not prompt_yes_no("Apply this change?", default_yes=True):
        print("Aborted.")
        return 1

    for zone, idx, _line, parsed in matches:
        ttl = int(parsed["ttl"]) if parsed["ttl"] else None
        new_line = build_record_line(
            "A", parsed["name"], args.new_ip,
            proxy=(parsed["proxied"] == "yes"), ttl=ttl,
        )
        replace_line_at(idx, new_line)

    print(f"[ok] updated {len(matches)} record(s) in {DNSCONFIG_FILE.name}")
    offer_preview_and_submit(
        f"Update A records from {args.old_ip} to {args.new_ip}", interactive=interactive
    )
    return 0


def cmd_record_prune_acme(args) -> int:
    if args.zone and args.zone not in ZONES:
        eprint(f"error: '{args.zone}' is not a zone this project manages ({', '.join(ZONES)}).")
        return 1
    zones_to_check = [args.zone] if args.zone else ZONES

    entries = []  # (zone, idx, line, parsed)
    for zone in zones_to_check:
        start, end = find_zone_block(zone)
        lines = DNSCONFIG_FILE.read_text(encoding="utf-8").splitlines()
        for i in range(start + 1, end):
            parsed = parse_record_line_full(lines[i])
            if not parsed:
                continue
            if parsed["type"] == "TXT" and (
                parsed["name"] == "_acme-challenge" or parsed["name"].startswith("_acme-challenge.")
            ):
                entries.append((zone, i, lines[i], parsed))

    if not entries:
        print("No _acme-challenge TXT records found.")
        return 0

    groups = collections.defaultdict(list)
    for e in entries:
        groups[(e[0], e[3]["name"])].append(e)

    flat = []
    print("_acme-challenge TXT records (ACME/Let's Encrypt validation tokens):")
    for (zone, name), items in groups.items():
        flag = (
            f" - {len(items)} tokens, likely includes stale ones from past renewals"
            if len(items) > 1 else ""
        )
        print(f"\n{fqdn_for(name, zone)}{flag}")
        for e in items:
            print(f"  [{len(flat)}] {e[2].strip()}")
            flat.append(e)

    if not args.remove:
        print(
            "\nThese aren't deleted automatically - only your cert issuer/reverse proxy "
            "knows which tokens are still live. Re-run with --remove <index> [<index> ...] "
            "once you've confirmed which ones are stale."
        )
        return 0

    interactive = not args.yes
    selected = []
    for i in args.remove:
        if i < 0 or i >= len(flat):
            eprint(f"error: --remove index {i} out of range (0-{len(flat) - 1}).")
            return 1
        selected.append(flat[i])

    print(f"\nAbout to remove {len(selected)} record(s):")
    for e in selected:
        print(f"  {e[2].strip()}")
    if interactive and not prompt_yes_no("Remove these?", default_yes=False):
        print("Aborted.")
        return 1

    for _zone, idx, _line, _parsed in sorted(selected, key=lambda e: e[1], reverse=True):
        remove_line_at(idx)

    print(f"[ok] removed {len(selected)} record(s) from {DNSCONFIG_FILE.name}")
    offer_preview_and_submit("Prune stale _acme-challenge TXT records", interactive=interactive)
    return 0


def cmd_lint(args) -> int:
    if args.zone and args.zone not in ZONES:
        eprint(f"error: '{args.zone}' is not a zone this project manages ({', '.join(ZONES)}).")
        return 1
    zones_to_check = [args.zone] if args.zone else ZONES

    issues = []  # (level, message)
    seen_exact = set()
    name_types = collections.defaultdict(lambda: collections.defaultdict(list))

    for zone in zones_to_check:
        try:
            start, end = find_zone_block(zone)
        except RuntimeError as e:
            issues.append(("error", str(e)))
            continue
        lines = DNSCONFIG_FILE.read_text(encoding="utf-8").splitlines()
        for i in range(start + 1, end):
            raw = lines[i].strip()
            if not raw:
                continue
            parsed = parse_record_line_full(lines[i])
            if not parsed:
                continue
            key = (zone, raw)
            if key in seen_exact:
                issues.append(("error", f"{zone}: duplicate line (appears more than once): {raw}"))
            seen_exact.add(key)
            name_types[zone][parsed["name"]].append((parsed["type"], i, raw))
            if parsed["type"] in ("CNAME", "MX") and parsed["value"] and not parsed["value"].endswith("."):
                issues.append(("error", f"{zone}: {parsed['type']} target missing trailing dot: {raw}"))

    for zone, names in name_types.items():
        for name, records in names.items():
            types = {t for t, _, _ in records}
            cname_entries = [r for r in records if r[0] == "CNAME"]
            if cname_entries and len(types) > 1:
                issues.append((
                    "error",
                    f"{zone}: {fqdn_for(name, zone)} has CNAME alongside other record type(s) "
                    f"({', '.join(sorted(types))}) - CNAME can't coexist with anything else on the same name.",
                ))
            if len(cname_entries) > 1:
                issues.append((
                    "error",
                    f"{zone}: {fqdn_for(name, zone)} has more than one CNAME record - "
                    "only one CNAME is allowed per name.",
                ))

    if not issues:
        print("[ok] lint passed - no issues found.")
        return 0

    errors = [m for level, m in issues if level == "error"]
    warnings = [m for level, m in issues if level == "warn"]
    for m in errors:
        print(f"[error] {m}")
    for m in warnings:
        print(f"[warn] {m}")
    print(f"\n{len(errors)} error(s), {len(warnings)} warning(s).")
    return 1 if errors else 0


SHOW_HEADERS = ["Zone", "Type", "Name", "FQDN", "Value", "Priority", "TTL", "Proxied"]


def collect_show_rows(zone_filter: str | None) -> list[list[str]]:
    zones_to_show = [zone_filter] if zone_filter else ZONES
    rows = []
    for zone in zones_to_show:
        try:
            start, end = find_zone_block(zone)
        except RuntimeError as e:
            eprint(f"error: {e}")
            continue
        lines = DNSCONFIG_FILE.read_text(encoding="utf-8").splitlines()
        for i in range(start + 1, end):
            parsed = parse_record_line_full(lines[i])
            if not parsed:
                continue
            rows.append([
                zone,
                parsed["type"],
                parsed["name"],
                fqdn_for(parsed["name"], zone),
                parsed["value"],
                parsed["priority"],
                parsed["ttl"],
                parsed["proxied"],
            ])
    return rows


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = []
    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    lines.append(header_line)
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def render_csv(headers: list[str], rows: list[list[str]]) -> str:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue()


def render_markdown(headers: list[str], rows: list[list[str]]) -> str:
    def esc(cell: str) -> str:
        return cell.replace("|", "\\|") if cell else ""

    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(esc(c) for c in row) + " |")
    return "\n".join(lines)


def cmd_show(args) -> int:
    zone_filter = None
    if args.zone:
        if args.zone not in ZONES:
            eprint(f"error: '{args.zone}' is not a zone this project manages ({', '.join(ZONES)}).")
            return 1
        zone_filter = args.zone

    rows = collect_show_rows(zone_filter)
    if args.grep:
        needle = args.grep.lower()
        rows = [
            row for row in rows
            if any(needle in cell.lower() for cell in row)
        ]
    if not rows:
        print("No records found." if not args.grep else f"No records match '{args.grep}'.")
        return 0

    if args.output == "csv":
        text = render_csv(SHOW_HEADERS, rows)
    elif args.output == "md":
        text = render_markdown(SHOW_HEADERS, rows)
    else:
        text = render_table(SHOW_HEADERS, rows)

    if args.file:
        Path(args.file).write_text(text, encoding="utf-8", newline="")
        print(f"Wrote {args.output} output to {args.file}")
    else:
        print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cross-platform helper for this dnscontrol/Cloudflare project."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_doctor = sub.add_parser("doctor", help="Check local environment setup.")
    p_doctor.set_defaults(func=cmd_doctor)

    p_setup = sub.add_parser("setup", help="One-time local setup (git hook + .env).")
    p_setup.set_defaults(func=cmd_setup)

    p_install = sub.add_parser(
        "install-dnscontrol", help="Download the pinned dnscontrol release binary."
    )
    p_install.add_argument(
        "--version", default=DNSCONTROL_VERSION, help="dnscontrol version to install."
    )
    p_install.add_argument(
        "--dest",
        default=str(Path.home() / ".local" / "bin"),
        help="Directory to install the binary into (default: ~/.local/bin).",
    )
    p_install.set_defaults(func=cmd_install_dnscontrol)

    p_preview = sub.add_parser("preview", help="Run `dnscontrol preview`.")
    p_preview.set_defaults(func=cmd_preview)

    p_push = sub.add_parser("push", help="Run `dnscontrol push` (asks for confirmation).")
    p_push.add_argument(
        "--yes", action="store_true", help="Skip the interactive confirmation prompt."
    )
    p_push.set_defaults(func=cmd_push)

    p_import = sub.add_parser(
        "import", help="Snapshot a live zone to a JS file for manual merging."
    )
    p_import.add_argument(
        "--zone", choices=ZONES, default=None,
        help=f"Which zone to snapshot (required if more than one: {', '.join(ZONES)}).",
    )
    p_import.add_argument(
        "--out", default="zone_import.js", help="Output file (default: zone_import.js)."
    )
    p_import.set_defaults(func=cmd_import)

    p_submit = sub.add_parser(
        "submit", help="Commit your dnsconfig.js change, push a branch, and open a PR."
    )
    p_submit.add_argument("message", help="Commit message / PR title.")
    p_submit.add_argument(
        "--files", nargs="+", default=None,
        help="Files to stage (default: dnsconfig.js).",
    )
    p_submit.add_argument("--branch", default=None, help="Branch name (default: auto from message).")
    p_submit.add_argument("--base", default="main", help="Base branch to PR against (default: main).")
    p_submit.add_argument("--body", default=None, help="PR body text.")
    p_submit.add_argument(
        "--skip-preview", action="store_true",
        help="Skip running `dnscontrol preview` before committing.",
    )
    p_submit.add_argument(
        "--yes", action="store_true", help="Skip the interactive confirmation prompt."
    )
    p_submit.set_defaults(func=cmd_submit)

    p_status = sub.add_parser(
        "status", help="List open pull requests and their DNS Preview check status."
    )
    p_status.set_defaults(func=cmd_status)

    p_review = sub.add_parser(
        "review", help="Show a PR's dnsconfig.js diff and its DNS Preview comment."
    )
    p_review.add_argument("pr", type=int, help="Pull request number.")
    p_review.set_defaults(func=cmd_review)

    p_approve = sub.add_parser(
        "approve", help="Approve a PR (fails on your own PR - GitHub restriction)."
    )
    p_approve.add_argument("pr", type=int, help="Pull request number.")
    p_approve.add_argument("--body", default=None, help="Review comment body.")
    p_approve.set_defaults(func=cmd_approve)

    p_merge = sub.add_parser(
        "merge", help="Merge a PR once its DNS Preview check has passed."
    )
    p_merge.add_argument("pr", type=int, help="Pull request number.")
    p_merge.add_argument(
        "--yes", action="store_true", help="Skip the interactive confirmation prompt."
    )
    p_merge.add_argument(
        "--force", action="store_true",
        help="Merge even if the DNS Preview check failed or hasn't run.",
    )
    p_merge.set_defaults(func=cmd_merge)

    p_record = sub.add_parser("record", help="Add, remove, or list records in dnsconfig.js.")
    record_sub = p_record.add_subparsers(dest="record_command", required=True)

    p_record_add = record_sub.add_parser(
        "add", help='Add a record, e.g. `record add plex.example.com`.'
    )
    p_record_add.add_argument(
        "target",
        help='Record name - fully-qualified (e.g. "plex.example.com") or, with '
             '--zone, a bare relative name (e.g. "plex").',
    )
    p_record_add.add_argument(
        "--zone", choices=ZONES, default=None,
        help=f"Zone, required for a bare relative name ({', '.join(ZONES)}). "
             "Inferred automatically from a fully-qualified target.",
    )
    p_record_add.add_argument(
        "--type", choices=["A", "CNAME", "MX", "TXT"], default=None,
        help="Record type. Prompted for if omitted.",
    )
    p_record_add.add_argument("--value", default=None, help="Target/value. Prompted for if omitted.")
    p_record_add.add_argument("--priority", type=int, default=None, help="MX priority.")
    p_record_add.add_argument(
        "--proxy", dest="proxy", action="store_true", default=None,
        help="Enable the Cloudflare proxy (A/CNAME only).",
    )
    p_record_add.add_argument(
        "--no-proxy", dest="proxy", action="store_false",
        help="Disable the Cloudflare proxy (A/CNAME only).",
    )
    p_record_add.add_argument("--ttl", type=int, default=None, help="TTL override in seconds.")
    p_record_add.add_argument(
        "--yes", action="store_true",
        help="Skip confirmation prompts and the preview/submit offer (needs --type and --value).",
    )
    p_record_add.set_defaults(func=cmd_record_add)

    p_record_edit = record_sub.add_parser(
        "edit",
        help='Change the value/priority/proxy/TTL of an existing record in place '
             '(instead of remove + add).',
    )
    p_record_edit.add_argument(
        "target",
        help='Record name - fully-qualified (e.g. "plex.example.com") or, with '
             '--zone, a bare relative name (e.g. "plex").',
    )
    p_record_edit.add_argument(
        "--zone", choices=ZONES, default=None,
        help=f"Zone, required for a bare relative name ({', '.join(ZONES)}). "
             "Inferred automatically from a fully-qualified target.",
    )
    p_record_edit.add_argument(
        "--type", default=None, help="Restrict to this record type if there are multiple matches."
    )
    p_record_edit.add_argument(
        "--index", type=int, default=None,
        help="Pick a specific match by index (see `record list`) when there are multiple.",
    )
    p_record_edit.add_argument("--value", default=None, help="New target/value. Prompted for if omitted.")
    p_record_edit.add_argument("--priority", type=int, default=None, help="New MX priority.")
    p_record_edit.add_argument(
        "--proxy", dest="proxy", action="store_true", default=None,
        help="Enable the Cloudflare proxy (A/CNAME only).",
    )
    p_record_edit.add_argument(
        "--no-proxy", dest="proxy", action="store_false",
        help="Disable the Cloudflare proxy (A/CNAME only).",
    )
    p_record_edit.add_argument("--ttl", type=int, default=None, help="New TTL override in seconds.")
    p_record_edit.add_argument(
        "--yes", action="store_true",
        help="Skip confirmation prompts and the preview/submit offer (needs --value).",
    )
    p_record_edit.set_defaults(func=cmd_record_edit)

    p_record_remove = record_sub.add_parser(
        "remove", help='Remove a record, e.g. `record remove plex.example.com`.'
    )
    p_record_remove.add_argument(
        "target",
        help='Record name - fully-qualified (e.g. "plex.example.com") or, with '
             '--zone, a bare relative name (e.g. "plex").',
    )
    p_record_remove.add_argument(
        "--zone", choices=ZONES, default=None,
        help=f"Zone, required for a bare relative name ({', '.join(ZONES)}). "
             "Inferred automatically from a fully-qualified target.",
    )
    p_record_remove.add_argument(
        "--type", default=None, help="Restrict to this record type if there are multiple matches."
    )
    p_record_remove.add_argument(
        "--index", type=int, default=None,
        help="Pick a specific match by index (see `record list`) when there are multiple.",
    )
    p_record_remove.add_argument(
        "--yes", action="store_true",
        help="Skip confirmation prompts and the preview/submit offer.",
    )
    p_record_remove.set_defaults(func=cmd_record_remove)

    p_record_list = record_sub.add_parser(
        "list", help="List records, optionally filtered by zone and/or name."
    )
    p_record_list.add_argument(
        "name", nargs="?", default=None,
        help='Filter by name, e.g. "plex" or "plex.example.com". Omit to list all zones.',
    )
    p_record_list.add_argument(
        "--zone", choices=ZONES, default=None,
        help=f"Restrict to one zone ({', '.join(ZONES)}). Required with a bare relative `name`.",
    )
    p_record_list.set_defaults(func=cmd_record_list)

    p_record_update_ip = record_sub.add_parser(
        "update-ip",
        help="Bulk-replace an IP across every A record that currently points at it, "
             "e.g. after a residential IP change.",
    )
    p_record_update_ip.add_argument("old_ip", help="The current IP to find, e.g. 203.0.113.10.")
    p_record_update_ip.add_argument("new_ip", help="The new IP to replace it with.")
    p_record_update_ip.add_argument(
        "--zone", choices=ZONES, default=None,
        help=f"Restrict to one zone ({', '.join(ZONES)}). Default: all zones.",
    )
    p_record_update_ip.add_argument(
        "--yes", action="store_true",
        help="Skip the confirmation prompt and the preview/submit offer.",
    )
    p_record_update_ip.set_defaults(func=cmd_record_update_ip)

    p_record_prune_acme = record_sub.add_parser(
        "prune-acme",
        help="List _acme-challenge TXT records (grouped by name, flagging names with "
             "multiple tokens) and optionally remove specific ones.",
    )
    p_record_prune_acme.add_argument(
        "--zone", choices=ZONES, default=None,
        help=f"Restrict to one zone ({', '.join(ZONES)}). Default: all zones.",
    )
    p_record_prune_acme.add_argument(
        "--remove", type=int, nargs="+", default=None, metavar="INDEX",
        help="Remove the record(s) at these indices (from the listing) instead of just reporting.",
    )
    p_record_prune_acme.add_argument(
        "--yes", action="store_true",
        help="Skip the confirmation prompt and the preview/submit offer (with --remove).",
    )
    p_record_prune_acme.set_defaults(func=cmd_record_prune_acme)

    p_lint = sub.add_parser(
        "lint",
        help="Fast, offline sanity checks on dnsconfig.js (no dnscontrol/network call).",
    )
    p_lint.add_argument(
        "--zone", choices=ZONES, default=None,
        help=f"Restrict to one zone ({', '.join(ZONES)}). Default: all zones.",
    )
    p_lint.set_defaults(func=cmd_lint)

    p_show = sub.add_parser(
        "show",
        help="Show a table of all DNS records across managed zones "
             "(terminal, CSV, or Markdown).",
    )
    p_show.add_argument(
        "--zone", choices=ZONES, default=None,
        help=f"Restrict to one zone ({', '.join(ZONES)}). Default: all zones.",
    )
    p_show.add_argument(
        "--output", choices=["table", "csv", "md"], default="table",
        help="Output format (default: table).",
    )
    p_show.add_argument(
        "--file", default=None,
        help="Write output to this file instead of printing to the terminal.",
    )
    p_show.add_argument(
        "--grep", default=None,
        help="Filter rows to those where this substring appears in any column "
             "(case-insensitive) - e.g. an IP or hostname.",
    )
    p_show.set_defaults(func=cmd_show)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
