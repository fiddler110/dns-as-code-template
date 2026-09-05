# `dnsctl.py` — the cross-platform helper script

`scripts/dnsctl.py` wraps the common local operations from [getting-started.md](getting-started.md), [making-changes.md](making-changes.md), and [operations.md](operations.md) behind one command that works identically on Windows, macOS, and Linux. It's Python 3 standard library only — no `pip install` required, just a Python 3 interpreter.

```sh
python scripts/dnsctl.py <command> [options]
```

(On some systems the interpreter is `python3` instead of `python` — use whichever runs Python 3 on your machine.)

## Commands

### `doctor`

Checks that your local environment is ready to work with this project: dnscontrol is installed and on PATH (or found in the usual `go install` location), a `CLOUDFLARE_API_TOKEN` is available from `.env`, the environment, or Azure Key Vault (see [security.md#key-vault-backed-tokens](security.md#key-vault-backed-tokens)), `creds.json` has the correct `apitoken` key, and the local git hook is enabled. Reports which of those three sources actually provided the token. Exits non-zero if anything fails.

```sh
python scripts/dnsctl.py doctor
```

Run this first any time something seems off, or right after cloning on a new machine.

### `setup`

One-time local setup: enables the `.githooks` pre-push safety check (`git config core.hooksPath .githooks`) and creates `.env` from `.env.example` if it doesn't already exist.

```sh
python scripts/dnsctl.py setup
```

You still need to edit `.env` afterward and fill in your read-only Cloudflare API token.

### `install-dnscontrol`

Downloads the pinned dnscontrol release binary for your OS and CPU architecture directly from GitHub releases (no Go toolchain required).

```sh
python scripts/dnsctl.py install-dnscontrol
python scripts/dnsctl.py install-dnscontrol --dest ~/bin --version 4.46.0
```

- `--dest` (default `~/.local/bin`): where to place the binary. Make sure this directory is on your `PATH`.
- `--version` (default matches `DNSCONTROL_VERSION` at the top of `dnsctl.py`, kept in sync with the version pinned in the GitHub Actions workflows).

### `preview`

Loads `.env` and runs `dnscontrol preview` — the dry-run diff against live Cloudflare state. Equivalent to the manual `source .env && dnscontrol preview` steps in the other docs, just shorter.

```sh
python scripts/dnsctl.py preview
```

### `push`

Runs `dnscontrol push` — **this applies changes directly to live Cloudflare DNS.** The normal way changes reach production is a PR merge triggering the `apply.yml` GitHub Actions workflow with the write-scoped token (see [ci-cd-pipeline.md](ci-cd-pipeline.md)); this command exists for exceptional/manual situations (e.g. an emergency fix, or testing) and always prints a warning and requires typing `APPLY` to confirm, unless you pass `--yes`.

```sh
python scripts/dnsctl.py push
python scripts/dnsctl.py push --yes    # skip the confirmation prompt (use with care)
```

It uses `CLOUDFLARE_API_TOKEN_WRITE` if present in `.env` or the environment (falling back to whatever `CLOUDFLARE_API_TOKEN` is set to otherwise). Per [security.md](security.md), the write token should not normally live in your local `.env` — set it as a one-off shell variable for this command instead of storing it:

```sh
CLOUDFLARE_API_TOKEN_WRITE=<write token> python scripts/dnsctl.py push
```

### `import`

Runs `dnscontrol get-zones` with the correct arguments (see [operations.md](operations.md#re-baselining-the-zone) for why the argument order matters) to snapshot a live zone into a JS file for manual merging back into `dnsconfig.js`.

```sh
python scripts/dnsctl.py import --zone example.com
python scripts/dnsctl.py import --zone example.org --out somewhere-else.js
```

`--zone` is required since this project manages more than one zone (there's no default to fall back to). Defaults to `zone_import.js` for `--out`. This does **not** touch `dnsconfig.js` automatically — merge the relevant records into that zone's `D(...)` block yourself, delete the generated file, and confirm `python scripts/dnsctl.py preview` reports 0 corrections before committing, exactly as described in [operations.md](operations.md#re-baselining-the-zone).

This is also how a brand new zone gets onboarded: `import --zone newzone.com` snapshots what's already live on Cloudflare, you paste the result in as a new `D("newzone.com", REG, DnsProvider(CF), ...)` block in `dnsconfig.js`, add `"newzone.com"` to the `ZONES` list near the top of `scripts/dnsctl.py`, and confirm `preview` reports 0 corrections before committing — the GitHub Actions workflows and the pre-push hook need no changes at all, since they just run `dnscontrol preview`/`push` against whatever zones are defined in `dnsconfig.js`.

### `begin`

Starts a DNS change the same way every time — the recommended first command for anyone (especially a contractor) about to touch `dnsconfig.js`. Refuses to run over uncommitted changes, so it never clobbers work in progress.

```sh
python scripts/dnsctl.py begin "add cname for myapp"
```

What it does, in order:
1. Aborts if your working tree isn't clean (commit/stash first).
2. Switches to `main` (or `--base`) and fast-forwards it to `origin/main` — aborts rather than force-updating if your local branch has diverged.
3. Runs `dnscontrol preview --expect-no-changes` against the freshly-synced branch to catch drift *before* you start (an out-of-band dashboard edit, or an apply that hasn't landed) — warns and asks to continue rather than silently building your change on top of an inconsistent baseline.
4. Creates a new branch (`dns/<slug-of-description>` by default, or `--branch <name>`).
5. Checks `_acme-challenge` records against live Cloudflare and folds in any drift (same logic as `record sync-acme`, without its own premature "open a PR?" prompt) — these tokens rotate outside this repo's control, so this keeps that housekeeping out of your way instead of showing up as a surprise in your diff later.
6. Prints the next steps (edit → `lint` → `preview` → `submit`).

Options:
- `description` — short text used for the branch name; prompted if omitted.
- `--branch <name>` — explicit branch name.
- `--base <branch>` — branch to sync from (default `main`).
- `--yes` — don't prompt on the drift/ACME-sync checks (auto-continue past drift warnings, auto-apply the ACME sync). Use with care — this is meant for scripting/CI, not for skipping past a warning you haven't read.

If `.env` has no `CLOUDFLARE_API_TOKEN`, steps 3 and 5 are skipped with a note (they need a token to talk to Cloudflare) — the sync/branch steps still happen.

### `submit`

Automates the whole "propose a change" workflow from [making-changes.md](making-changes.md): runs `dnscontrol preview` as a sanity check, creates a branch, commits your staged file(s), pushes, and opens a PR — in one command.

```sh
python scripts/dnsctl.py submit "Add CNAME for myapp"
```

What it does, in order:
1. Confirms there are local changes to commit.
2. Runs `dnscontrol preview` and shows you the diff (unless `--skip-preview`), then asks "does this look right?" before continuing (unless `--yes`).
3. If you're on `main` (or `--base`), creates a new branch — by default named `dns/<slug-of-your-message>`, or pass `--branch <name>` yourself. If you're already on a feature branch, it just uses that branch and commits onto it.
4. Stages `dnsconfig.js` (or whatever `--files` lists), commits with your message as the commit message and PR title.
5. Pushes the branch and opens a PR (`gh pr create`) targeting `--base` (default `main`).

Options:
- `--files <path> [<path> ...]` — which file(s) to stage (default: `dnsconfig.js`).
- `--branch <name>` — explicit branch name instead of the auto-generated slug.
- `--base <branch>` — PR base branch (default `main`).
- `--body <text>` — PR description.
- `--skip-preview` — don't run `dnscontrol preview` first (not recommended).
- `--yes` — skip the "does this look right?" confirmation prompt (useful for scripting).

```sh
# fully non-interactive, e.g. from another script
python scripts/dnsctl.py submit "Add CNAME for myapp" --yes --body "Points myapp at the origin."
```

### `record add` / `record remove` / `record list`

Adds or removes a single record in `dnsconfig.js` without hand-editing the file — parses a fully-qualified name (`plex.example.com`) or a bare relative name with `--zone`, prompts interactively for whatever else it needs, shows you the exact line before touching anything, and only writes the file after you confirm.

```sh
python scripts/dnsctl.py record add plex.example.com
```

**This project manages more than one zone** (currently `example.com` and `example.org` — see `ZONES` near the top of `scripts/dnsctl.py`), so every `record` command needs to know which zone a name belongs to:

- **Fully-qualified name** (recommended, and required if you don't pass `--zone`): give the whole thing, e.g. `plex.example.com` or `www.plex.example.com` — the zone is inferred by matching the longest known zone suffix, and stripped to get the record name (`plex`, `www.plex`). Multi-label subdomains work the same way; there's nothing special about "nested" - `www.plex.example.com` is just the two-label relative name `www.plex` under `example.com`, matching how compound names already exist in `dnsconfig.js` (e.g. `s1._domainkey`).
- **Bare relative name** (e.g. `plex` alone): you must also pass `--zone example.com` (or `--zone example.org`) — with more than one zone managed here, a bare name alone is ambiguous and the command refuses to guess.
- **Apex/naked domain**: give the zone name itself (`example.com`) or `--zone example.com` with target left off - both map to the apex record (`@`).
- **Wildcard**: `*.example.com` maps to the name `*`, the usual DNS convention for a catch-all subdomain.
- A trailing dot on the input (`plex.example.com.`) is tolerated and stripped; matching is case-insensitive.
- A name that doesn't end in any zone this project manages (e.g. `plex.some-other-domain.net`) is rejected outright — this project can't create records outside its own zones, so this is almost always a typo.

Interactive walkthrough example:

```
$ python scripts/dnsctl.py record add plex.example.com
Record type (A, CNAME, MX, TXT) [CNAME]:
target hostname for plex.example.com: example.com
'example.com' has no trailing dot - add one? [Y/n]:
Proxy through Cloudflare (orange cloud)? [Y/n]:

Full name: plex.example.com
About to add this line to the example.com block of dnsconfig.js:
  CNAME("plex", "example.com.", CF_PROXY_ON),
Add it? [Y/n]: y
[ok] added to dnsconfig.js

Run `dnscontrol preview` now to verify? [Y/n]: y
... (preview output) ...

Open a pull request for this change now? [y/N]: y
Commit message / PR title [Add CNAME for plex.example.com]:
... (runs submit for you) ...
```

Every step it can infer or default is still shown and confirmable — nothing is written to `dnsconfig.js` until you approve the exact line (which also always shows the full FQDN so a zone-detection mistake is easy to catch), and nothing is committed/pushed unless you say yes to that separately.

Non-interactive form (for scripting, or when you already know every value) — pass everything as flags plus `--yes` so it never prompts:

```sh
python scripts/dnsctl.py record add plex.example.com --type CNAME --value example.com --yes
python scripts/dnsctl.py record add mail.example.com --type MX --value mx.example.com --priority 10 --yes
python scripts/dnsctl.py record add api.example.com --type A --value 203.0.113.10 --no-proxy --yes
python scripts/dnsctl.py record add www --zone example.org --type CNAME --value example.org --yes
```

With `--yes`, a missing required flag (`--type`, `--value`, `--priority` for MX) is a hard error rather than a prompt — it never silently blocks waiting for input. A missing trailing dot on a CNAME/MX target is auto-appended (printed as a note), and proxy defaults to ON for A/CNAME if `--proxy`/`--no-proxy` isn't given. It also refuses to add an exact duplicate of an existing line, and warns (without blocking) if you're about to create a CNAME alongside another record type on the same name, or vice versa — CNAME can't coexist with anything else on the same name (a DNS-wide rule, not specific to this project).

Removing a record works the same way:

```sh
python scripts/dnsctl.py record remove plex.example.com
python scripts/dnsctl.py record remove www --zone example.org --yes   # bare name needs --zone
```

If more than one record matches the name (e.g. several `TXT` records under the same name, or the same relative name existing in two different zones' blocks — matches are always scoped to one zone per call, never cross-zone), it lists them with an index and asks which one — or pass `--type` or `--index <n>` to disambiguate up front (required with `--yes` when there's more than one match).

Use `record list` to see what's there before adding/removing anything:

```sh
python scripts/dnsctl.py record list                          # every record, grouped by zone
python scripts/dnsctl.py record list --zone example.org        # just that zone
python scripts/dnsctl.py record list plex.example.com       # just records for this name
```

**Scope**: the wizard supports `A`, `CNAME`, `MX`, and `TXT` — the record types actually in use in these zones (see [record-types.md](record-types.md)). For `NS`/`CAA`/`SRV`/anything else, edit `dnsconfig.js` directly.

### `record edit`

Changes an existing record's value/priority/proxy/TTL **in place**, instead of the two-step `record remove` + `record add`. Same zone/name resolution and multiple-match disambiguation (`--type`/`--index`) as `record remove`.

```sh
python scripts/dnsctl.py record edit plex.example.com --value newtarget.example.com. --yes
python scripts/dnsctl.py record edit api.example.com --no-proxy --yes
```

Omitted fields (priority, proxy, TTL) keep their current value — interactively, the prompt shows the existing value as the default; non-interactively (`--yes`), whatever isn't passed as a flag is carried over unchanged. Shows a `-`/`+` diff of the exact line before writing it. Record type can't be changed this way (that's not really an "edit" — remove and re-add instead).

### `record update-ip`

Bulk-replaces an IP across **every** `A` record currently pointing at it — built for the common homelab situation of a residential IP changing, where several records (e.g. the apex plus a Cloudflare tunnel or two) all point at the same address and hand-editing each one is error-prone.

```sh
python scripts/dnsctl.py record update-ip 198.51.100.10 198.51.100.20
python scripts/dnsctl.py record update-ip 198.51.100.10 198.51.100.20 --zone example.com --yes
```

Lists every matching record's FQDN before asking for confirmation, preserves each record's existing proxy/TTL settings, and only touches `A` records with an exact value match. Defaults to checking all managed zones; `--zone` restricts to one.

### `record prune-acme`

Reports on `_acme-challenge` TXT records, grouped by name. By default (when `.env` is configured) it cross-checks each one against real Cloudflare state via `dnscontrol get-zones`, annotating every entry as `[confirmed gone from Cloudflare - safe to remove]` or `[still live on Cloudflare]` — falling back to the "more than one token under this name" heuristic only if no token is available or the live fetch fails. Report-only either way — it never deletes anything unless you explicitly say which ones.

```sh
python scripts/dnsctl.py record prune-acme                              # live cross-check by default, just list/flag
python scripts/dnsctl.py record prune-acme --offline                    # skip the live check, heuristic only, no network/.env needed
python scripts/dnsctl.py record prune-acme --zone example.com --remove 2 3 5
python scripts/dnsctl.py record prune-acme --remove 1-6                 # ranges and combos also work: 1,2,4-6
```

`--remove <index> [<index> ...]` deletes the specific records at those indices (from the listing) after a confirmation showing exactly which lines will go — pass `--yes` to skip that prompt too. Each `INDEX` accepts a plain number, a range (`1-6`), or a comma-separated combo (`1,2,4-6`), and multiple can be space-separated. There's no automatic "keep the newest N" heuristic: only your cert issuer/reverse-proxy config actually knows which token is still in use, so cross-check there before removing one.

The live cross-check also separately warns about any record that exists on Cloudflare but is missing from `dnsconfig.js` — that's the more dangerous direction of drift, since the next `apply` (from *any* merged PR, not just an ACME-related one) makes Cloudflare match `dnsconfig.js` and would delete that record, possibly a token from an in-flight renewal. Remember it only *reports* — deleting a stale line always still requires a separate `--remove`, going through the normal PR flow, since simply flagging something doesn't stop a future apply from resurrecting it.

### `record sync-acme`

For projects where an out-of-band ACME client (e.g. Caddy with its own separate Cloudflare credential) owns `_acme-challenge` records directly, treats live Cloudflare state as the source of truth for *just* those records and folds it into `dnsconfig.js` — adding whatever's live but missing locally, removing whatever's local but no longer live. Unlike `prune-acme`, this actually writes the file (after a confirmation).

```sh
python scripts/dnsctl.py record sync-acme
python scripts/dnsctl.py record sync-acme --zone example.com --yes
```

Requires `.env`. Shows the full add/remove diff before asking to apply — pass `--yes` to skip the prompt and the preview/submit offer. Only touches `_acme-challenge` TXT lines; every other record in `dnsconfig.js` is untouched and still treated as authoritative in the normal (`dnsconfig.js` → Cloudflare) direction. After it runs, `prune-acme`/`preview` should report 0 drift for ACME records until the next renewal.
### `lint`

Fast, offline sanity checks over `dnsconfig.js` — no dnscontrol binary, no network call, so it's cheap to run constantly (e.g. as a first check before `preview`, or wired into an editor's save hook). Catches:

- An exact duplicate line appearing more than once.
- A `CNAME`/`MX` target missing its required trailing dot.
- A `CNAME` coexisting with another record type on the same name, or more than one `CNAME` on the same name — both invalid under DNS, not just this project's convention.

```sh
python scripts/dnsctl.py lint
python scripts/dnsctl.py lint --zone example.org
```

Exits non-zero if any error-level issue is found. This is a static text check only — it doesn't know what's actually live on Cloudflare, so it complements `preview` rather than replacing it (a file can pass `lint` and still show unexpected corrections under `preview` if it drifted from live state).

### `show`

A reporting view across all managed zones — every record's type, name, FQDN, value, MX priority, TTL, and Cloudflare proxy status, as a table, CSV, or Markdown. Unlike `record list` (which just echoes the raw `dnsconfig.js` lines), `show` parses each line into columns.

```sh
python scripts/dnsctl.py show                                   # table, printed to the terminal
python scripts/dnsctl.py show --zone example.org                 # just one zone
python scripts/dnsctl.py show --output csv --file dns.csv        # write a CSV file
python scripts/dnsctl.py show --output md --file dns.md          # write a Markdown table file
python scripts/dnsctl.py show --output md                        # print Markdown to the terminal instead
python scripts/dnsctl.py show --grep 203.0.113.10                # find every record referencing this value
```

- `--zone` (default: all zones) — restrict to one zone.
- `--output {table,csv,md}` (default `table`) — `table` is aligned plain text for the terminal; `csv`/`md` are for piping or saving.
- `--file <path>` — write the output there instead of printing it. Without `--file`, output always goes to stdout (so `csv`/`md` can be redirected too, e.g. `python scripts/dnsctl.py show --output csv > dns.csv`).
- `--grep <text>` — case-insensitive substring filter across every column (zone, type, name, FQDN, value, etc.) — handy for finding every record that references a given IP or hostname before a bulk change like `record update-ip`.

Parsing is best-effort over the record types and modifiers actually used in this project's `dnsconfig.js` (A/AAAA/CNAME/MX/TXT/etc., `CF_PROXY_ON`/`CF_PROXY_OFF`, `TTL(n)`) — it reads the file, it doesn't call dnscontrol, so it reflects whatever's currently committed/staged, not necessarily what's live on Cloudflare (use `preview` for that comparison).

### `status`

Lists open pull requests and the state of each one's `DNS Preview` check, so you don't need to open GitHub in a browser just to see what's pending.

```sh
python scripts/dnsctl.py status
```

### `review`

Shows a given PR's `dnsconfig.js` diff and the latest `DNS Preview` comment (the actual dnscontrol diff) side by side, from the terminal.

```sh
python scripts/dnsctl.py review 4
```

Use this before merging — it's the same information the "review the diff before merging" step in [making-changes.md](making-changes.md) asks for, just without leaving the terminal.

### `approve`

Attempts to approve a PR (`gh pr review --approve`).

```sh
python scripts/dnsctl.py approve 4
```

On this solo-maintained repo, this will always fail with GitHub's "can not approve your own pull request" error — that's a GitHub platform restriction, not something this script can work around. The command detects that specific error and tells you to just use `merge` instead, since this repo has no required-review branch rule (see [security.md](security.md#why-no-environment-approval-gate)) — merging doesn't actually need an approval here. This command becomes useful once a second maintainer joins and can approve someone else's PR.

### `merge`

Merges a PR, but only after checking its `DNS Preview` check succeeded — refuses (unless `--force`) to merge a PR whose preview check failed or hasn't run yet.

```sh
python scripts/dnsctl.py merge 4
python scripts/dnsctl.py merge 4 --yes     # skip the confirmation prompt
python scripts/dnsctl.py merge 4 --force   # merge despite a failed/missing check (use with care)
python scripts/dnsctl.py merge 4 --wait    # also wait for DNS Apply and confirm live state (see `validate`)
```

This runs `gh pr merge --merge --delete-branch`, which triggers the same `DNS Apply` GitHub Actions workflow as merging through the GitHub web UI — the effect on Cloudflare is identical either way. Like the review-diff step, this is meant to save a trip to the browser, not to change what merging actually does. Without `--wait`, it prints a reminder to run `validate` yourself; with `--wait`, it runs the exact same check inline before returning — see [`validate`](#validate) below for exactly what that check does. `--timeout <seconds>` (default 300) controls how long `--wait` waits for `DNS Apply` to appear/complete.

### `history`

Lists commits on `main` that touched `dnsconfig.js`, newest first — the input to `rollback`. Deliberately not limited to two-parent merge commits: this repo's history has a mix of squash-merged and true-merge PRs, and both are valid `rollback` targets (each entry is tagged `(merge)` when it has more than one parent, which only changes which `git revert` form is used under the hood).

```sh
python scripts/dnsctl.py history
python scripts/dnsctl.py history --limit 50
```

### `rollback`

Reverts a previously-merged `dnsconfig.js` change by opening a normal PR — it never pushes to `main` directly, so an emergency rollback still goes through the same `DNS Preview` → review → `merge` → `DNS Apply` pipeline as any other change (see [security.md](security.md) for why direct pushes are blocked at all).

```sh
python scripts/dnsctl.py rollback 19       # by PR number
python scripts/dnsctl.py rollback 6bf3a48  # by commit SHA (see `history`)
```

What it does, in order:
1. Aborts if your working tree isn't clean, or if `main` can't fast-forward to `origin/main`.
2. Resolves the target: a PR number is looked up via `gh pr view --json mergeCommit` (works whether that PR was squash- or merge-committed); a raw SHA is used as-is.
3. Creates a branch (`dns/revert-<sha>`) and runs `git revert` on the target commit — `-m 1` is added automatically only if the target has more than one parent (a true merge commit).
4. If the revert hits a conflict, stops and tells you to resolve it manually, then finish with `preview` + `submit` yourself.
5. Runs `dnscontrol preview` and asks you to confirm the diff looks like the exact inverse of the original change before continuing (skip with `--yes`).
6. Pushes the branch and opens a PR titled `Revert: <original subject>`.

From there it's the normal flow: `status` / `review <PR#>` / `merge <PR#>` to let it apply. Use `history` first to find the right target — the entry's PR number (if the commit message names one) or its short SHA both work.

### `validate`

Confirms a merge actually landed: finds the `DNS Apply` run for a commit, waits for it to finish, then re-runs `dnscontrol preview --expect-no-changes` to confirm live Cloudflare now matches `dnsconfig.js`. This is what `merge` otherwise leaves you to do by hand ("watch the Actions tab yourself") — use it as the last step of every change, not just when something seems wrong.

```sh
python scripts/dnsctl.py validate            # validate the current tip of main
python scripts/dnsctl.py validate 19         # validate a specific PR's merge
python scripts/dnsctl.py validate 6bf3a48    # validate a specific commit SHA
python scripts/dnsctl.py validate --timeout 600   # wait up to 10 minutes instead of the 5-minute default
```

What it does, in order:
1. Aborts if your working tree isn't clean; otherwise switches to `main` and fast-forwards it to `origin/main` (same guard `begin`/`rollback` use) — it always checks live state against the *current* `main`, regardless of which past commit's apply run it's confirming, since that's the invariant that actually matters.
2. Resolves the target the same way `rollback` does — a PR number via `gh pr view`, a raw SHA used as-is, or (with no argument) the freshly-synced tip of `main`.
3. Polls `gh run list` for a `DNS Apply` run matching that commit's SHA, up to `--timeout` seconds.
4. Once found, streams its progress via `gh run watch --exit-status` until it completes; fails loudly (with a link to the run) if it didn't succeed.
5. Runs `dnscontrol preview --expect-no-changes`. Zero corrections means validated: the apply landed and live Cloudflare matches `dnsconfig.js` exactly. Any corrections here almost always mean a *second*, unrelated drift source (e.g. an out-of-band ACME renewal) rather than a problem with the apply itself — see [operations.md#responding-to-detected-drift](operations.md#responding-to-detected-drift).

`merge --wait` runs this automatically right after merging — see [`merge`](#merge) above.

## Why Python instead of separate shell/PowerShell scripts

Native `.sh`/`.ps1`/`.bat` scripts would need to be written and kept in sync three times, and this project already hit real bugs from that kind of duplication (e.g. the `get-zones` argument order being wrong in one doc but not another). A single Python script, using only the standard library, runs identically on all three platforms from one source of truth, at the cost of requiring a Python 3 interpreter — which is already installed on almost all macOS/Linux systems and is a common Windows dependency as well.
