# CI/CD pipeline

Two GitHub Actions workflows plus two local git hooks make up the core pipeline. Three more
workflows (`test.yml`, `drift.yml`, `dnscontrol-version-check.yml`) add background checks that
aren't strictly required but are worth turning on. This doc explains what each one does and why,
in enough detail to debug or modify them.

**Note on this template**: `preview.yml`, `apply.yml`, and `drift.yml` ship with their triggers
commented out (`on: {}`) because this template has no real Cloudflare credentials to run against.
Uncomment the trigger block in each once you've replaced the example zones in `dnsconfig.js` and
set up the secrets — see [getting-started.md](getting-started.md) and
[branch-protection-and-secrets.md](branch-protection-and-secrets.md).

## `preview.yml` — runs on every pull request

Trigger (once enabled): `pull_request`, and only when `dnsconfig.js`, `creds.json`, or the workflow
file itself changes.

What it does:
1. Checks out the PR branch.
2. Downloads a pinned dnscontrol release binary directly from GitHub (`DNSCONTROL_VERSION` env var
   in the workflow — bump this to upgrade dnscontrol), verifies its SHA-256 against the release's
   published `checksums.txt`, and aborts if it doesn't match.
3. Runs `dnscontrol preview`, capturing all output to `preview_output.txt`.
4. Posts that output as a comment on the PR via `actions/github-script`, updating the same comment
   on re-runs instead of piling up a new one every push.

Key security property: this job's `env` only ever sets `CLOUDFLARE_API_TOKEN` to the **read-only**
secret (`secrets.CLOUDFLARE_API_TOKEN_READONLY`). It uses the `pull_request` trigger, not
`pull_request_target` — this means a PR from a fork runs with the fork's workflow file and
**without** access to repo secrets, so it can't exfiltrate the token even if the PR itself is
malicious. Worth keeping even on a repo with no external contributors today — the trigger choice
costs nothing and keeps the door closed.

## `apply.yml` — runs on push to `main`

Trigger (once enabled): `push` to `main` (same path filters as `preview.yml`), plus
`workflow_dispatch` for a manual/emergency apply.

Concurrency: the workflow sets `concurrency: {group: dns-apply, cancel-in-progress: false}`, so if
two pushes to `main` land close together, the second `apply` run queues behind the first instead of
racing it (both would otherwise diff against the same starting state and could reapply a change the
other already superseded). `cancel-in-progress` is deliberately `false` — cancelling a
half-finished `dnscontrol push` would be worse than waiting.

What it does:
1. **Requires this commit to come from a merged pull request.** This is the server-side PR gate —
   see [security.md](security.md#why-the-pr-gate-is-enforced-inside-applyyml-not-by-github-branch-protection)
   for the full rationale. It's skipped when the run was triggered manually via
   `workflow_dispatch`, since that's already a deliberate authenticated action.
2. Checks out `main`.
3. Installs the same pinned, checksum-verified dnscontrol binary as `preview.yml`.
4. Runs `dnscontrol preview` as a fail-fast dry run, so a config that errors never reaches a partial
   `push`.
5. Runs `dnscontrol push`, which diffs `dnsconfig.js` against live Cloudflare state and applies the
   corrections.

This job's `env` sets `CLOUDFLARE_API_TOKEN` to the **write** secret
(`secrets.CLOUDFLARE_API_TOKEN_WRITE`). This is the only place in the entire pipeline the write
token is ever used. There is deliberately no manual-approval gate (GitHub Environment + required
reviewers) in front of it for a solo maintainer — see
[security.md](security.md#why-no-environment-approval-gate) for why, and what to change once more
than one person can merge.

## `test.yml` — runs on every push/PR that touches the tooling

Trigger: `push`/`pull_request` when `dnsconfig.js`, `scripts/**`, `tests/**`, or the workflow file
itself changes (broader than `preview.yml`'s filter on purpose — this needs to run whenever
`scripts/dnsctl.py` changes, which is exactly when it's most likely to catch something). No
Cloudflare credentials needed, so it runs unconditionally, even in a fresh clone of this template.

Two jobs:
1. `lint` — runs `python scripts/dnsctl.py lint`, the fast offline sanity check.
2. `test` — installs `pytest` and runs the suite under `tests/`.

## `dnscontrol-version-check.yml` — weekly check for a newer dnscontrol release

Trigger: weekly `schedule`, plus `workflow_dispatch`. Compares the `PINNED_VERSION` in the workflow
against the latest dnscontrol GitHub release and files (or updates) a tracking issue if the pinned
version has fallen behind. Needs no Cloudflare credentials — only `gh api` against the public
dnscontrol repo and this repo's own issues — so it's safe to leave enabled as-is.

## `drift.yml` — scheduled check that live Cloudflare still matches `dnsconfig.js`

Trigger (once enabled): weekly `schedule`, plus `workflow_dispatch`. Runs
`dnscontrol preview --expect-no-changes` with the **read-only** token and files (or updates) a
tracking issue if it finds a difference — catches an out-of-band dashboard edit or a renewal that
didn't get folded back into `dnsconfig.js`. See
[operations.md#responding-to-detected-drift](operations.md#responding-to-detected-drift).

## `.githooks/pre-commit` — earliest local feedback

`.githooks/pre-commit` runs at commit time, before `.githooks/pre-push` ever gets a chance to. It:

1. Refuses to commit any staged file that looks like a local secrets file (`.env`, `.env.*`,
   anywhere in the tree) — a backstop in case one is ever force-added past `.gitignore`.
2. Scans the staged diff for a `*_TOKEN`/`*_KEY`/`*_SECRET`/`*_PASSWORD`-named variable assigned a
   long opaque value — catches a real credential pasted into the wrong file before it's even
   committed. This is a heuristic, not a full secret scanner: it's scoped to variable-assignment
   context specifically so it doesn't fire on a base64-looking `_acme-challenge` TXT value already
   in `dnsconfig.js`, and it ignores values starting with `$` so `creds.json`'s legitimate
   `"$CLOUDFLARE_API_TOKEN"` env-var reference doesn't trip it.
3. Runs `dnsctl lint` if `dnsconfig.js` is staged — the same offline, no-token check `test.yml`'s
   `lint` job runs, just one step earlier.

Like `pre-push`, this is bypassable with `--no-verify` and only fires on a machine with
`core.hooksPath` set — it's a convenience, not a substitute for CI.

## `.githooks/pre-push` — local convenience, not the enforcement gate

`.githooks/pre-push`:

1. Only activates for pushes where the destination ref is `refs/heads/main` (pushing feature
   branches is unaffected).
2. Skips straight through for a ref deletion (`git push --delete`) — there's nothing to preview.
3. Diffs the commit range being pushed against `dnsconfig.js` and `creds.json` (the same files
   `preview.yml`/`apply.yml` filter on — see the comments at both locations, they must stay in
   sync). If neither changed, it prints a skip message and exits without touching Cloudflare at
   all. For a brand-new branch push (no remote ref yet) it checks every commit being introduced
   instead of diffing against an empty tree.
4. Otherwise, loads `.env` if present and runs `dnscontrol preview`.
5. Blocks the push (non-zero exit) if `dnscontrol preview` errors out — e.g. bad credentials, a
   syntax error in `dnsconfig.js`.

The skip step matters because without it, every push to `main` — including a docs typo — required
a valid Cloudflare token and a network round-trip, which is slow, needlessly credential-hungry, and
fails closed on transient API/token problems that couldn't possibly affect DNS. That friction is
also what pushes people toward `--no-verify`, which then disables the check for the pushes that
*do* matter. This only makes the common case fast — it doesn't change the hook's fundamental limits
(see below).

This is enabled per-clone with `git config core.hooksPath .githooks` (see
[getting-started.md](getting-started.md)) — it is **not** automatic just from cloning the repo.

**Important limitation**: this hook only fires on `git push` from a machine that has it configured,
and is bypassable with `--no-verify`. Merging a PR through the GitHub web UI does **not** invoke it
either — that merge becomes a server-side commit that GitHub pushes to `main` on your behalf,
bypassing any local hook entirely. Because of this, the hook is **not** the thing actually
preventing an unreviewed apply — on a private repo without GitHub Pro's branch-protection features,
that job belongs to the merged-PR check inside `apply.yml` itself (see `apply.yml` above and
[security.md](security.md)), which runs server-side and can't be bypassed the same way. The hook
remains useful purely as faster local feedback: it catches a bad config before you even push,
without waiting on CI.

## The pipeline end-to-end

```
Local edit → dnscontrol preview (manual sanity check)
    ↓
git commit → pre-commit hook (secret scan + lint)
    ↓
git push (feature branch) → pre-push hook doesn't fire (not main)
    ↓
Open PR → preview.yml runs → diff posted as PR comment (read-only token)
    ↓
Merge PR (GitHub web UI — hooks do not fire here)
    ↓
push to main → apply.yml runs → merged-PR check → dnscontrol preview → dnscontrol push (write token) → Cloudflare updated
```

## Modifying the pipeline

- **Bumping the dnscontrol version**: update `DNSCONTROL_VERSION` in `preview.yml`, `apply.yml`,
  `drift.yml`, and `scripts/dnsctl.py` (and `PINNED_VERSION` in
  `dnscontrol-version-check.yml`). The checksum verification fetches `checksums.txt` from the
  matching release at run time, so bumping the version needs **no separate hash update** — the
  check is always against whatever `checksums.txt` the new release publishes. Also update the
  `go install` command in [getting-started.md](getting-started.md) and the version note in
  [operations.md](operations.md) if you keep one.
- **Adding a new CI check** (e.g. a linter on `dnsconfig.js`): add a step to `preview.yml` before
  the `dnscontrol preview` step; keep it read-only-token-only.
- **Adding more path triggers**: if you split zone config into multiple files, update the `paths:`
  filters in both workflow files or they simply won't run.

## Known gotcha: don't use the `DNSControl/dnscontrol@main` marketplace Action

Both workflows install dnscontrol via a direct `curl`+`tar` download of a pinned release, not the
`DNSControl/dnscontrol@<ref>` Docker-based GitHub Action. That action's Dockerfile expects a
prebuilt `linux/amd64/dnscontrol` binary that is only produced for tagged releases (via
goreleaser) — pointing it at `@main` fails the Docker build with
`"/linux/amd64/dnscontrol": not found`. If you ever want to switch to using that Action instead,
pin it to an actual release tag (e.g. `DNSControl/dnscontrol@v4.46.0`), not `@main`.
