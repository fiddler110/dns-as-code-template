# CI/CD pipeline

Two GitHub Actions workflows plus two local git hooks make up the core pipeline. Four more
workflows (`test.yml`, `drift.yml`, `reachability.yml`, `secret-scan.yml`,
`dnscontrol-version-check.yml`) add background checks that aren't strictly required but are worth
turning on. This doc explains what each one does and why, in enough detail to debug or modify them.

**Note on this template**: `preview.yml`, `apply.yml`, and `drift.yml` ship with their triggers
commented out (`on: {}`) because this template has no real Cloudflare credentials to run against.
Uncomment the trigger block in each once you've replaced the example zones in `dnsconfig.js` and
set up the secrets — see [getting-started.md](getting-started.md) and
[branch-protection-and-secrets.md](branch-protection-and-secrets.md). `test.yml`, `secret-scan.yml`,
and `dnscontrol-version-check.yml` need no Cloudflare credentials, so they run unconditionally, even
in a fresh clone of this template.

## `preview.yml` — runs on every pull request

Trigger (once enabled): `pull_request`, and only when `dnsconfig.js`, `creds.json`, or the workflow
file itself changes.

What it does:
1. Checks out the PR branch.
2. Optionally requires the PR title/body to reference a ticket ID (see "Team/enterprise mode
   toggles" below) — runs first, before anything Cloudflare-touching, so a missing ticket fails
   fast and cheaply.
3. Downloads a pinned dnscontrol release binary directly from GitHub (version read from
   `.dnscontrol-version` at the repo root — the single source of truth every workflow and
   `scripts/dnsctl.py` reads from — bump that file to upgrade dnscontrol), verifies its SHA-256
   against the release's published `checksums.txt`, and aborts if it doesn't match.
4. Runs `dnscontrol preview`, capturing all output to `preview_output.txt`.
5. Uploads `preview_output.txt` as a build artifact named `preview-output-<head-sha>` (14-day
   retention). This is what `apply.yml`'s "verify applied diff matches reviewed diff" step
   downloads later to confirm the diff it's about to push still matches this reviewed diff.
6. Posts that output as a comment on the PR via `actions/github-script`, updating the same comment
   on re-runs instead of piling up a new one every push.

Key security property: this job's `env` only ever sets `CLOUDFLARE_API_TOKEN` to the **read-only**
secret (`secrets.CLOUDFLARE_API_TOKEN_READONLY`). It uses the `pull_request` trigger, not
`pull_request_target` — this means a PR from a fork runs with the fork's workflow file and
**without** access to repo secrets, so it can't exfiltrate the token even if the PR itself is
malicious. Worth keeping even on a repo with no external contributors today — the trigger choice
costs nothing and keeps the door closed.

## `apply.yml` — runs on push to `main`

Trigger (once enabled): `push` to `main` (same path filters as `preview.yml`, plus the workflow
file itself), plus `workflow_dispatch` for a manual/emergency apply — see
[operations.md](operations.md#manually-triggering-an-apply).

Concurrency: the workflow sets `concurrency: {group: dns-apply, cancel-in-progress: false}`, so if
two pushes to `main` land close together, the second `apply` run queues behind the first instead of
racing it (both would otherwise diff against the same starting state and could reapply a change the
other already superseded). `cancel-in-progress` is deliberately `false` — cancelling a
half-finished `dnscontrol push` would be worse than waiting.

What it does:
1. **Requires this commit to come from a merged pull request.** This is the server-side PR gate —
   see [security.md](security.md#why-the-pr-gate-is-enforced-inside-applyyml-not-by-github-branch-protection)
   for the full rationale. It's skipped when the run was triggered manually via
   `workflow_dispatch`, since that's already a deliberate authenticated action. On success, records
   the merged PR's number and head commit SHA for the next two steps.
2. **Requires an `APPROVED` review from someone other than the PR author** — **only when
   `REQUIRE_NON_AUTHOR_REVIEW` is `"true"`** in the job's `env:` (default `"false"`; flip it before
   granting anyone else write access — see
   [security.md](security.md#onboarding-and-offboarding-what-actually-needs-to-happen)). When
   enabled: calls the GitHub API for that PR's reviews, takes each reviewer's most recent review (a
   later request-changes supersedes an earlier approval), and fails the run if none of those latest
   reviews is an approval from a non-author. Same "enforce server-side, inside the workflow"
   pattern as the merged-PR check above, for the same reason — see
   [security.md](security.md#why-the-pr-gate-is-enforced-inside-applyyml-not-by-github-branch-protection).
   Also skipped for `workflow_dispatch`.
3. **Finds the `DNS Preview` run for that PR's head commit and downloads its `preview_output.txt`
   artifact** — the diff a human actually reviewed. Also skipped for `workflow_dispatch`; fails
   loudly if no completed preview run/artifact can be found for a real merged PR, since that means
   there's nothing to verify the applied diff against.
4. Checks out `main`.
5. Installs the same pinned, checksum-verified dnscontrol binary as `preview.yml`.
6. Runs `dnscontrol preview` as a fail-fast dry run, so a config that errors never reaches a partial
   `push`, capturing its output.
7. **Diffs that fresh preview output against the downloaded reviewed artifact** and fails the job —
   without pushing anything — on any mismatch. This is what catches live Cloudflare state (or
   `dnsconfig.js`) having changed out-of-band between the PR's review and this apply; without it,
   `dnscontrol push` would silently reconcile that unreviewed difference too. Skipped for
   `workflow_dispatch`, same as the two steps above.
8. **Snapshots live zone state before push** (`dnscontrol get-zones` per managed zone, uploaded as
   an artifact) — `dnsconfig.js` only knows what it knows, so a live-only record has no
   representation to revert to if this push removes it unexpectedly; this gives something to
   recover from beyond "revert the file and hope."
9. Runs `dnscontrol push`, which diffs `dnsconfig.js` against live Cloudflare state and applies the
   corrections.
10. **Verifies the apply actually took effect** — retries `dnscontrol preview --expect-no-changes`
    for up to 5 minutes, since `push` exiting 0 doesn't guarantee live state matches `dnsconfig.js`
    afterward (a transient read-after-write hiccup shouldn't be indistinguishable from a real
    partial apply). Files or updates a tracking issue on failure at any step, with whatever output
    was captured.

This job's `env` sets `CLOUDFLARE_API_TOKEN` to the **write** secret
(`secrets.CLOUDFLARE_API_TOKEN_WRITE`). This is the only place in the entire pipeline the write
token is ever used. There is deliberately no manual-approval gate (GitHub Environment + required
reviewers) in front of it for a solo maintainer — see
[security.md](security.md#why-no-environment-approval-gate) for why, and what to change once more
than one person can merge.

### Team/enterprise mode toggles

Both `preview.yml` and `apply.yml` ship two operating-mode toggles, off by default so a solo setup
sees no change in behavior:

- **`REQUIRE_TICKET_REFERENCE`** (`preview.yml`) — requires the PR title or body to match
  `TICKET_REFERENCE_PATTERN` (default `\[[A-Z][A-Z0-9]*-?[0-9]+\]`, matching both a ServiceNow-style
  `[INC0012345]` and a Jira-style `[JIRA-1234]`) before the rest of the check runs. See
  [making-changes.md#team-enterprise-mode-referencing-a-ticket](making-changes.md#teamenterprise-mode-referencing-a-ticket).
- **`REQUIRE_NON_AUTHOR_REVIEW`** (`apply.yml`) — described above.

Both have a local, faster-feedback mirror in `dnsctl submit`/`dnsctl merge`
(`DNSCTL_REQUIRE_TICKET_REFERENCE`, `DNSCTL_REQUIRE_NON_AUTHOR_REVIEW` in `.env` — see
`.env.example`), but the `.env` mirrors are a convenience only, not the enforcement: nothing stops
someone from leaving them unset locally, so flip the CI-side toggle first when onboarding a team.

## `test.yml` — runs on every push/PR that touches the tooling

Trigger: `push`/`pull_request` when `dnsconfig.js`, `scripts/**`, `tests/**`, or the workflow file
itself changes (broader than `preview.yml`'s filter on purpose — this needs to run whenever
`scripts/dnsctl.py` changes, which is exactly when it's most likely to catch something). Also
`workflow_dispatch`. No Cloudflare credentials needed, so it runs unconditionally, even in a fresh
clone of this template.

Three independent jobs:
1. `lint` — runs `python scripts/dnsctl.py lint`, the same offline `dnsconfig.js` sanity check
   `.githooks/pre-commit` runs locally.
2. `lint-python` — runs `ruff check scripts/ tests/`.
3. `test` — installs `pytest` and runs the suite under `tests/`.

## `drift.yml` — scheduled check that live Cloudflare still matches `dnsconfig.js`

Trigger (once enabled): weekly `schedule` (Monday 13:00 UTC), plus `workflow_dispatch`. Runs
`dnscontrol preview --expect-no-changes` with the **read-only** token and files (or updates) a
tracking issue if it finds a difference — catches an out-of-band dashboard edit or a renewal that
didn't get folded back into `dnsconfig.js`. See
[operations.md#responding-to-detected-drift](operations.md#responding-to-detected-drift).

## `reachability.yml` — post-apply reachability smoke test

Trigger: `workflow_run` on the `DNS Apply` workflow, `types: [completed]`, gated on
`github.event.workflow_run.conclusion == 'success'` — so this only runs after a real apply
actually succeeded, never on every push and never in place of `apply.yml`'s own verify step. Also
`workflow_dispatch` for a manual run. No Cloudflare token at all — it only makes outbound HTTPS
requests to public hostnames. Since `DNS Apply`'s own triggers ship disabled in this template, this
workflow stays effectively dormant until you enable them, other than a manual `workflow_dispatch`.

Why it exists: `apply.yml`'s own verify step confirms the *DNS record state* took effect
(`dnscontrol preview --expect-no-changes`), but proves nothing about whether the proxied hostnames
those records point at are actually reachable — a wrong CNAME target, a broken tunnel, or a downed
backend app wouldn't fail any existing check. This matters more once more than one person can apply
a change: the person merging a ticket they didn't originate has less context to notice "that
hostname doesn't look right" than the original author.

What it does: `curl`s `https://<host>/` (15s timeout, 2 retries) for each proxied hostname in
`dnsconfig.js` (see the list in the workflow itself, commented to stay in sync with
`dnsconfig.js`'s `CF_PROXY_ON` records — keep it updated as you add/remove proxied hostnames). Any
HTTP status code — even a 4xx/5xx from the app itself — counts as reachable, since that means the
request got through Cloudflare's edge to something; only a connection-level failure (DNS resolution
failure, TLS handshake failure, connection refused/timeout — a nonzero `curl` exit with no HTTP
code at all) is treated as a real failure. This avoids false alarms from an app being legitimately
down for unrelated reasons while still catching "this hostname doesn't resolve to anything
reachable at all."

On any connection-level failure, files or updates a single tracking GitHub issue (same
dedup-by-marker pattern as `drift.yml`/`apply.yml`, marker
`<!-- dns-reachability-tracking-issue -->`) listing which hostnames failed and why. Purely
informational — it never gates or interacts with `apply.yml` itself. See
[operations.md](operations.md#responding-to-a-reachability-failure) for how to respond.

## `secret-scan.yml` — gitleaks on every push and PR

Trigger: `push`, `pull_request`, and `workflow_dispatch`, **deliberately with no `paths:` filter**
unlike every other workflow in this repo — a secret can land in any file, not just the DNS-relevant
paths those filters scope to. Runs `gitleaks/gitleaks-action` against the full checkout history
(`fetch-depth: 0`). No Cloudflare credentials needed, so it runs unconditionally.

## `dnscontrol-version-check.yml` — scheduled upstream version and token-expiry checks

Trigger: weekly `schedule` (same as `drift.yml`), plus `workflow_dispatch`. No Cloudflare
credentials needed for the version check itself, so it runs unconditionally. Two independent jobs:
1. **`check`** — compares the pinned version in `.dnscontrol-version` against dnscontrol's latest
   upstream GitHub release. If the pinned version is behind, files or updates a tracking issue
   (labeled `dnscontrol-version`) noting the upstream release — it deliberately does not bump
   anything automatically, since a version bump needs its own changelog review.
2. **`token-expiry`** — calls Cloudflare's `/user/tokens/verify` endpoint once per API token secret
   (using that token as its own Bearer auth, so no separate token ID needs to be stored), and files
   or updates a tracking issue if either token is within 30 days of its configured expiry. Exists
   because a calendar-driven, manual token-rotation runbook (see
   [operations.md#rotating-cloudflare-api-tokens](operations.md#rotating-cloudflare-api-tokens))
   has nothing else checking actual expiry. Skips a token cleanly (with a warning) if the
   corresponding secret isn't set yet, so this job doesn't need the write secret to already exist
   in a fresh clone of this template.

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
4. Runs `dnsctl fmt --check` if `dnsconfig.js` is staged — fails (without rewriting the staged
   file) if a zone block isn't in canonical record-type order, telling the developer to run
   `dnsctl fmt` themselves and re-stage.

Like `pre-push`, this is bypassable with `--no-verify` and only fires on a machine with
`core.hooksPath` set — it's a convenience, not a substitute for CI.

## `.githooks/pre-push` — local convenience, not the enforcement gate

`.githooks/pre-push`:

1. Only activates for pushes where the destination ref is `refs/heads/main` (pushing feature
   branches is unaffected).
2. Skips straight through for a ref deletion (`git push --delete`) — there's nothing to preview.
3. Diffs the commit range being pushed against `dnsconfig.js`, `creds.json`, and `apply.yml` (the
   same files `preview.yml`/`apply.yml` filter on — see the comments at both locations, they must
   stay in sync). If none changed, it prints a skip message and exits without touching Cloudflare
   at all. For a brand-new branch push (no remote ref yet) it checks every commit being introduced
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
that job belongs to the merged-PR (and, once enabled, non-author-review) checks inside `apply.yml`
itself (see `apply.yml` above and [security.md](security.md)), which run server-side and can't be
bypassed the same way. The hook remains useful purely as faster local feedback: it catches a bad
config before you even push, without waiting on CI.

## The pipeline end-to-end

```
Local edit → dnscontrol preview (manual sanity check)
    ↓
git commit → pre-commit hook (secret scan + lint + fmt --check)
    ↓
git push (feature branch) → pre-push hook doesn't fire (not main)
    ↓
Open PR → preview.yml runs → optional ticket-reference check → diff posted as PR comment (read-only token)
    ↓
Merge PR (GitHub web UI — hooks do not fire here)
    ↓
push to main → apply.yml runs → merged-PR check → optional non-author-review check → fetch reviewed
preview diff → dnscontrol preview → diff-match check → pre-apply snapshot → dnscontrol push
(write token) → post-apply verify → Cloudflare updated
    ↓
reachability.yml runs → confirms proxied hostnames are actually reachable
```

## Modifying the pipeline

- **Bumping the dnscontrol version**: update the single `.dnscontrol-version` file at the repo
  root — it's the single source of truth that `preview.yml`, `apply.yml`, `drift.yml`,
  `dnscontrol-version-check.yml`, and `scripts/dnsctl.py` all read from, and
  `tests/test_sync_invariants.py` guards that they keep reading from it rather than hardcoding
  their own copy. Never add a second, hardcoded version string to any workflow or script. The
  checksum verification fetches `checksums.txt` from the matching release at run time, so bumping
  the version needs **no separate hash update** — the check is always against whatever
  `checksums.txt` the new release publishes. Also update the `go install` command in
  [getting-started.md](getting-started.md) and the version note in
  [operations.md](operations.md) if you keep one.
- **Adding a new CI check** (e.g. a linter on `dnsconfig.js`): add a step to `preview.yml` before
  the `dnscontrol preview` step; keep it read-only-token-only.
- **Adding more path triggers**: if you split zone config into multiple files, update the `paths:`
  filters in every workflow that has one, and `.githooks/pre-push`'s `dns_relevant_paths`, or they
  simply won't run/fire — `tests/test_sync_invariants.py` guards the `apply.yml`/`pre-push`
  half of this.
- **Bumping a third-party Action** (`actions/checkout`, `actions/github-script`, etc.): these are
  pinned to a commit SHA rather than a mutable tag (`uses: owner/repo@<sha>  # vX.Y.Z`), so a
  compromised or moved tag can't silently change what runs alongside the write token. Resolve a new
  SHA with `gh api repos/<owner>/<repo>/git/ref/tags/<tag> --jq .object.sha` (dereference with
  `gh api repos/<owner>/<repo>/git/tags/<sha> --jq .object.sha` if `.object.type` is `tag` rather
  than `commit`) and update both the SHA and the trailing version comment.

## Known gotcha: don't use the `DNSControl/dnscontrol@main` marketplace Action

Both workflows install dnscontrol via a direct `curl`+`tar` download of a pinned release, not the
`DNSControl/dnscontrol@<ref>` Docker-based GitHub Action. That action's Dockerfile expects a
prebuilt `linux/amd64/dnscontrol` binary that is only produced for tagged releases (via
goreleaser) — pointing it at `@main` fails the Docker build with
`"/linux/amd64/dnscontrol": not found`. If you ever want to switch to using that Action instead,
pin it to an actual release tag (e.g. `DNSControl/dnscontrol@v4.46.0`), not `@main`.
