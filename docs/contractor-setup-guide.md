# New contractor setup guide

Welcome — this is the one-time setup for a new contractor working on this repo. Do this once per
machine, before your first change. After setup, [DNS_Change_Process.md](DNS_Change_Process.md) is
the process you'll follow for every actual change.

This assumes whoever onboarded you has already:
- Added you to the GitHub repo/team with write access.
- Granted your Azure AD identity read access to the Cloudflare token in Azure Key Vault (see
  [keyvault-access.md](keyvault-access.md) — that's their doc, not yours, but worth skimming if
  something below doesn't work).
- Given you the **vault name** and, if you're a guest in their Azure AD tenant, the **tenant ID**.

If any of those three haven't happened yet, get them sorted before continuing — steps 4+ below
won't work without them.

## 1. Install the tools

| Tool | Why | Install |
|---|---|---|
| **Git** | Clone the repo, commit changes | [git-scm.com](https://git-scm.com/) |
| **Python 3** | Runs `dnsctl.py` (stdlib only, nothing to `pip install`) | Usually already present; [python.org](https://www.python.org/) otherwise |
| **GitHub CLI (`gh`)** | Opening/reviewing/merging PRs from the command line | [cli.github.com](https://cli.github.com/) |
| **Azure CLI (`az`)** | Fetches the Cloudflare token from Key Vault at runtime | [learn.microsoft.com/cli/azure/install-azure-cli](https://learn.microsoft.com/cli/azure/install-azure-cli) |
| **dnscontrol** | Does the actual DNS diff/apply | installed by `dnsctl.py` in step 3 below — no separate install needed |

Confirm `gh` and `az` are authenticated:

```sh
gh auth login
az login
# if you're a guest in a tenant other than your default one:
az login --tenant <tenant-id-you-were-given>
```

## 2. Clone the repo

```sh
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO
```

## 3. Run the scaffolding script

```sh
python scripts/dnsctl.py setup                # enables the local git safety hook, scaffolds .env
python scripts/dnsctl.py install-dnscontrol   # downloads the pinned dnscontrol binary
```

## 4. Point `.env` at Key Vault, not a local token

Open the freshly-scaffolded `.env` and set **only** the vault name — leave `CLOUDFLARE_API_TOKEN`
commented out/unset. As a contractor you should never need to paste an actual Cloudflare token
anywhere:

```sh
# .env
CLOUDFLARE_KEYVAULT_NAME=<vault-name-you-were-given>
# CLOUDFLARE_KEYVAULT_SECRET_NAME=cloudflare-api-token-readonly   # only if you were told a different name
```

Why this instead of a real token in `.env`: see
[security.md#key-vault-backed-tokens](security.md#key-vault-backed-tokens). Short version — a token
in a file outlives your engagement unless someone remembers to rotate it; your Azure identity being
disabled at offboarding cuts off vault access immediately, with nothing left over to clean up.

## 5. Verify everything works

```sh
python scripts/dnsctl.py doctor
```

Expect:

```
[ok]   dnscontrol found: ...
[ok]   CLOUDFLARE_API_TOKEN available (length NN, source: Azure Key Vault '<vault-name>').
[ok]   creds.json uses the correct 'apitoken' key.
[ok]   git hooks path is set to '.githooks'.

Environment looks good.
```

If it instead reports the token isn't available, see the **Troubleshooting** section in
[keyvault-access.md](keyvault-access.md#troubleshooting) — most commonly it's `az login` not having
run yet, or the role grant not having propagated.

Then confirm you can actually see live state:

```sh
python scripts/dnsctl.py preview
```

This should run without error (some correction count is fine and expected if the repo currently has
drift — the point of this step is confirming the *token* works, not that the count is zero).

## 6. Read the process doc before your first change

[DNS_Change_Process.md](DNS_Change_Process.md) is the actual workflow — roles, the
`begin` → edit → `lint`/`preview` → `submit` → review → `merge` sequence, and what to do if
something needs to be rolled back. [making-changes.md](making-changes.md) and
[dnsctl-cli.md](dnsctl-cli.md) are the technical references it points into when you need the exact
syntax for a command.

The two things worth internalizing before you touch `dnsconfig.js` for the first time:

- **You never hold the write-scoped Cloudflare token.** Every change you make goes through a PR;
  merging by someone else (or CI-verified re-run) is what actually applies it. You cannot
  accidentally push a bad record straight to production from your machine.
- **`preview` is the safety net — read it, every time.** If it shows anything beyond the record(s)
  you intended to touch, stop and fix the edit before submitting. Don't rely on review to catch a
  mistake `preview` already showed you.

## Quick reference

```sh
python scripts/dnsctl.py doctor      # confirm your local setup + Key Vault access
python scripts/dnsctl.py begin "..." # start a change (see DNS_Change_Process.md)
python scripts/dnsctl.py lint        # fast offline check after editing dnsconfig.js
python scripts/dnsctl.py preview     # the real diff against live Cloudflare
python scripts/dnsctl.py submit "..." # commit, push, open PR
python scripts/dnsctl.py status      # check DNS Preview passed on your PR
```

## Getting help

- Setup or Key Vault access itself not working: ask whoever onboarded you — that's covered by their
  side of the process in [keyvault-access.md](keyvault-access.md).
- Everything else (a command's exact behavior, a `dnscontrol` error, how to structure an edit): see
  [operations.md#troubleshooting](operations.md#troubleshooting) first, then
  [dnsctl-cli.md](dnsctl-cli.md) for command reference.
