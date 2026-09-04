# Getting started

## What this project is

DNS records for every zone this project manages — currently `example.com` and `example.org`, each its own `D(...)` block — live in `dnsconfig.js`, a JavaScript file read by [dnscontrol](https://docs.dnscontrol.org/). dnscontrol compares that file against what's actually live on Cloudflare and can show you the diff (`preview`) or apply it (`push`) for all managed zones at once. This repo wires that up so:

- Anyone proposing a DNS change does it as a pull request.
- GitHub Actions runs `dnscontrol preview` on every PR and posts the exact diff as a comment — no one has to guess what a change will do before it's reviewed.
- Merging to `main` triggers `dnscontrol push`, which applies the change to Cloudflare using a separate, write-scoped token.

**Note:** in this template, both `.github/workflows/preview.yml` and `apply.yml` have their triggers commented out (`on: {}`) — they'd just fail with no real zone or credentials configured. Once you've replaced the example zones in `dnsconfig.js` and set up the two Cloudflare API token secrets (below), uncomment the `on:` block in each workflow file to turn them back on.

## Prerequisites

- [Go](https://go.dev/) (to install dnscontrol) — or download a prebuilt binary from the [dnscontrol releases page](https://github.com/DNSControl/dnscontrol/releases) instead.
- [GitHub CLI](https://cli.github.com/) (`gh`) — convenient for managing secrets and PRs, not strictly required.
- Access to the Cloudflare account that owns the `example.com` and `example.org` zones.

## Cloning this repo for the first time (new machine)

Using the [cross-platform helper script](dnsctl-cli.md) (works the same on Windows/macOS/Linux):

```sh
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO

python scripts/dnsctl.py setup                # enables the git hook, scaffolds .env
python scripts/dnsctl.py install-dnscontrol   # downloads the pinned dnscontrol binary
# edit .env and fill in CLOUDFLARE_API_TOKEN with the READ-ONLY token (see below) —
# never put the write token in a local .env
python scripts/dnsctl.py doctor               # confirm everything is set up correctly
```

Or the equivalent manual steps, if you'd rather not use the script:

```sh
git config core.hooksPath .githooks

go install github.com/DNSControl/dnscontrol/v4@latest
export PATH="$PATH:$(go env GOPATH)/bin"

cp .env.example .env
```

`core.hooksPath` is a local git config setting — it is **not** inherited automatically from the repo, so every clone (including reclones on the same machine) needs to run that command once.

## Cloudflare API tokens

This project uses two separate, zone-scoped Cloudflare API tokens instead of one broad account-wide token. Both are created in the Cloudflare dashboard under **My Profile → API Tokens → Create Custom Token**, each with its Zone Resources restricted to exactly the zones this project manages (`example.com` and `example.org`) — not "all zones" on the account:

| Token     | Permissions                       | Where it lives                                                           | Used by                                         |
| --------- | --------------------------------- | ------------------------------------------------------------------------ | ----------------------------------------------- |
| Read-only | `Zone.DNS:Read`, `Zone.Zone:Read` | Your local `.env`, and the GitHub secret `CLOUDFLARE_API_TOKEN_READONLY` | `dnscontrol preview` (locally and in CI)        |
| Write     | `Zone.DNS:Edit`, `Zone.Zone:Read` | Only the GitHub secret `CLOUDFLARE_API_TOKEN_WRITE`                      | `dnscontrol push` (CI only, on merge to `main`) |

Never put the write token anywhere on your local machine. It should exist in exactly one place: the GitHub Actions secret. See [security.md](security.md) for the reasoning.

### Adding/rotating the GitHub secrets

```sh
gh secret set CLOUDFLARE_API_TOKEN_READONLY --repo YOUR_USERNAME/YOUR_REPO
gh secret set CLOUDFLARE_API_TOKEN_WRITE --repo YOUR_USERNAME/YOUR_REPO
```

Each prompts for the value (or pipe it in with `--body`). See [operations.md](operations.md) for a rotation runbook.

### Adding another zone later

Add the new domain to both tokens' Zone Resources in the Cloudflare dashboard (no new tokens or GitHub secrets needed), then see [dnsctl-cli.md](dnsctl-cli.md#import) for the `import` step that pulls its existing records into a new `D(...)` block in `dnsconfig.js` — the GitHub Actions workflows and the pre-push hook need no changes, since they operate on every zone `dnsconfig.js` defines.

## Verifying your setup

Once `.env` is filled in and dnscontrol is installed:

```sh
python scripts/dnsctl.py preview
# or manually: set -a && source .env && set +a && dnscontrol preview
```

You should see `Done. 0 corrections.` — that means your local config matches what's live on Cloudflare. If you see a large number of `DELETE` corrections, your `.env` token or `dnsconfig.js` is likely misconfigured — **do not proceed to `push`** — see [operations.md](operations.md#troubleshooting).

## Where things live

```
your-repo/
├── dnsconfig.js               # DNS zone definitions (one D(...) block per zone) — the file you edit for changes
├── creds.json                 # tells dnscontrol which env var holds the Cloudflare token (no secrets)
├── .env                       # your local read-only token (gitignored, never committed)
├── .env.example                # documents what .env should contain
├── .githooks/pre-push          # local safety check before pushing to main
├── .github/workflows/
│   ├── preview.yml             # runs on every PR
│   └── apply.yml                # runs on merge to main
├── scripts/dnsctl.py            # cross-platform helper script (see docs/dnsctl-cli.md)
└── docs/                        # you are here
```
