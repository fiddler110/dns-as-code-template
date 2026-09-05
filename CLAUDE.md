# CLAUDE.md

Guidance for working on this repo. Full reasoning lives in `docs/` — this file is a short,
factual index of the conventions that must hold on every change, not a restatement of the docs.

## What this repo is

DNS-as-code for Cloudflare zones, managed with [dnscontrol](https://dnscontrol.org).
`dnsconfig.js` is the single source of truth for every DNS record in every zone it defines — the
live Cloudflare state is expected to match it exactly. This template ships with two example zones
(`example.com`, `example.org`) to demonstrate multi-zone support; replace them with your own
domain(s). `ZONES` near the top of `scripts/dnsctl.py` **must stay in sync** with the
`D("<zone>", ...)` blocks in `dnsconfig.js` — adding/removing a zone means updating both.

If you keep a backlog file for this repo's own hardening/reliability work (a `ROADMAP.md` with
stable item IDs, or similar), check it before starting unrelated work, since an item may already be
in flight on another branch. This template doesn't ship with one — add it if useful.

## Rules that must never be broken

- **Never commit secrets.** `.env` holds the real Cloudflare API token and is gitignored from the
  repo's first commit — never remove it from `.gitignore`, never `git add -f` it. `creds.json` is
  intentionally committed but holds zero secret material (a literal `"$CLOUDFLARE_API_TOKEN"`
  env-var reference, not a token) — that's correct, don't "fix" it into a real value.
- **Never apply a DNS change by pushing straight to `main`.** Every change goes through a PR:
  edit `dnsconfig.js` → `dnscontrol preview` locally → PR → read the automated preview diff
  comment → merge → the `DNS Apply` workflow applies it. See
  [docs/making-changes.md](docs/making-changes.md) for the exact steps and
  [docs/security.md](docs/security.md) for why. A workflow-level server-side gate that rejects
  applying anything not associated with a merged pull request is built into `apply.yml`.
- **The write-scoped Cloudflare token lives only as a GitHub Actions secret** used by
  `apply.yml`. Never add `CLOUDFLARE_API_TOKEN_WRITE` back into local `.env` — see
  [docs/security.md](docs/security.md).
- **This template's CI triggers ship disabled** (`on: {}` in `preview.yml`, `apply.yml`, and
  `drift.yml`) because there are no real Cloudflare credentials configured yet. Don't "fix" that by
  enabling them until the example zones have been replaced and the secrets are set up — see
  [docs/getting-started.md](docs/getting-started.md).

## The `dnsctl.py` helper

`scripts/dnsctl.py` (Python 3 stdlib only, no `pip install`) wraps the common operations —
`doctor`, `begin`, `lint`, `preview`, `show`, `record add`/`remove`/`edit`/`update-ip`/`prune-acme`/
`sync-acme`, `submit`, `status`, `review`, `merge`, `validate`, `history`, `rollback`. Run
`python scripts/dnsctl.py doctor` first in any new session to confirm the local environment is set
up correctly, `python scripts/dnsctl.py begin "<description>"` to start any new change (syncs
`main`, checks for drift, creates a branch), and `python scripts/dnsctl.py merge <PR#> --wait` (or
`validate` afterward) to confirm the change actually landed on live Cloudflare once merged. Full
command reference: [docs/dnsctl-cli.md](docs/dnsctl-cli.md).

Before proposing or reviewing a `dnsconfig.js` edit, run `python scripts/dnsctl.py lint` (fast,
offline, no token needed) and `python scripts/dnsctl.py preview` (needs `.env`) to confirm the
diff is exactly what's intended — nothing else. See
[docs/making-changes.md](docs/making-changes.md#common-mistakes-to-avoid).

## Tests

A `tests/` directory (pytest, stdlib fixtures under `tests/fixtures/`) covers
`scripts/dnsctl.py`'s record-parsing/rewriting logic — no network access or Cloudflare token
needed. Run the suite with:

```sh
pip install pytest
pytest tests/
```

## Other docs

- [docs/getting-started.md](docs/getting-started.md) — first-time clone/setup.
- [docs/contractor-setup-guide.md](docs/contractor-setup-guide.md) — one-time setup for a new
  contractor or collaborator: tools, repo access, and Key Vault.
- [docs/keyvault-access.md](docs/keyvault-access.md) — admin runbook for granting/revoking a
  collaborator's Azure Key Vault access to the Cloudflare token.
- [docs/DNS_Change_Process.md](docs/DNS_Change_Process.md) — a standardized process for a team of
  contractors: roles, recommended access controls, step-by-step flow, and rollback.
- [docs/record-types.md](docs/record-types.md) — record type syntax reference.
- [docs/operations.md](docs/operations.md) — recovery, token rotation, re-baselining a zone.
- [docs/ci-cd-pipeline.md](docs/ci-cd-pipeline.md) — what the GitHub Actions workflows and the
  local git hooks actually do.
- [docs/security.md](docs/security.md) — threat model and why the project is set up this way.
