# CI/CD pipeline

Two GitHub Actions workflows plus one local git hook make up the full pipeline. This doc explains what each one does and why, in enough detail to debug or modify them.

## `preview.yml` — runs on every pull request

Trigger: `pull_request`, and only when `dnsconfig.js`, `creds.json`, or the workflow file itself changes.

What it does:
1. Checks out the PR branch.
2. Downloads a pinned dnscontrol release binary directly from GitHub (`DNSCONTROL_VERSION` env var in the workflow — bump this to upgrade dnscontrol).
3. Runs `dnscontrol preview`, capturing all output to `preview_output.txt`.
4. Posts that output as a comment on the PR via `actions/github-script`.

Key security property: this job's `env` only ever sets `CLOUDFLARE_API_TOKEN` to the **read-only** secret (`secrets.CLOUDFLARE_API_TOKEN_READONLY`). It uses the `pull_request` trigger, not `pull_request_target` — this means a PR from a fork runs with the fork's workflow file and **without** access to repo secrets, so it can't exfiltrate the token even if the PR itself is malicious. (In this solo-maintained repo there are no external forks today, but the trigger choice costs nothing and keeps the door closed.)

## `apply.yml` — runs on push to `main`

Trigger: `push` to `main`, same path filters as `preview.yml`.

What it does:
1. Checks out `main`.
2. Installs the same pinned dnscontrol binary.
3. Runs `dnscontrol push`, which diffs `dnsconfig.js` against live Cloudflare state and applies the corrections.

This job's `env` sets `CLOUDFLARE_API_TOKEN` to the **write** secret (`secrets.CLOUDFLARE_API_TOKEN_WRITE`). This is the only place in the entire pipeline the write token is ever used. There is deliberately no manual-approval gate (GitHub Environment + required reviewers) in front of it — see [security.md](security.md#why-no-environment-approval-gate) for why, and what to change if that stops being true.

## `.githooks/pre-push` — local safety net

GitHub's branch-protection feature (required status checks before merge) needs GitHub Pro on a private repository, which this repo isn't on. As a substitute, `.githooks/pre-push`:

1. Only activates for pushes where the destination ref is `refs/heads/main` (pushing feature branches is unaffected).
2. Loads `.env` if present.
3. Runs `dnscontrol preview`.
4. Blocks the push (non-zero exit) if `dnscontrol preview` errors out — e.g. bad credentials, a syntax error in `dnsconfig.js`.

This is enabled per-clone with `git config core.hooksPath .githooks` (see [getting-started.md](getting-started.md)) — it is **not** automatic just from cloning the repo.

**Important limitation**: this hook only fires on `git push` from a machine that has it configured. Merging a PR through the GitHub web UI does **not** invoke it — that merge becomes a server-side commit that GitHub pushes to `main` on your behalf, bypassing any local hook entirely. The hook's real job is to catch a mistake if you ever push directly to `main` from your own machine (e.g. `git push origin HEAD:main`) or push a local merge commit. The actual gate against a bad merge is: read the `DNS Preview` comment on the PR before clicking merge.

## The pipeline end-to-end

```
Local edit → dnscontrol preview (manual sanity check)
    ↓
git push (feature branch) → hook doesn't fire (not main)
    ↓
Open PR → preview.yml runs → diff posted as PR comment (read-only token)
    ↓
Merge PR (GitHub web UI — hook does not fire here)
    ↓
push to main → apply.yml runs → dnscontrol push (write token) → Cloudflare updated
```

## Modifying the pipeline

- **Bumping the dnscontrol version**: update `DNSCONTROL_VERSION` in both `preview.yml` and `apply.yml`. Also update the `go install` command in [getting-started.md](getting-started.md) and the version note in [operations.md](operations.md) if you keep one.
- **Adding a new CI check** (e.g. a linter on `dnsconfig.js`): add a step to `preview.yml` before the `dnscontrol preview` step; keep it read-only-token-only.
- **Adding more path triggers**: if you split zone config into multiple files, update the `paths:` filters in both workflow files or they simply won't run.

## Known gotcha: don't use the `DNSControl/dnscontrol@main` marketplace Action

Both workflows install dnscontrol via a direct `curl`+`tar` download of a pinned release, not the `DNSControl/dnscontrol@<ref>` Docker-based GitHub Action. That action's Dockerfile expects a prebuilt `linux/amd64/dnscontrol` binary that is only produced for tagged releases (via goreleaser) — pointing it at `@main` fails the Docker build with `"/linux/amd64/dnscontrol": not found`. If you ever want to switch to using that Action instead, pin it to an actual release tag (e.g. `DNSControl/dnscontrol@v4.46.0`), not `@main`.
