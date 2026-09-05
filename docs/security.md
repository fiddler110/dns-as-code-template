# Security model

## Threat model, briefly

The main things this project defends against:
1. **A leaked credential** shouldn't be able to affect anything beyond the specific Cloudflare zones this project manages.
2. **A bad or malicious PR** shouldn't be able to reach Cloudflare without a human seeing the exact diff first.
3. **Secrets shouldn't end up in git history**, ever, even accidentally.

## Least privilege: two scoped tokens instead of one broad one

Cloudflare's legacy **Global API Key** grants full account access — every zone, every setting. This project never uses it. Instead there are two **custom, zone-scoped** tokens, each with Zone Resources restricted to exactly the zones this project manages (`example.com` and `example.org`) — not "all zones" on the account, and not any other zone that might exist there:

| Token | Scope | Blast radius if leaked |
|---|---|---|
| Read-only | `Zone.DNS:Read`, `Zone.Zone:Read` | Attacker can see these zones' DNS records. Can't change anything, can't touch other zones on the account or account settings. |
| Write | `Zone.DNS:Edit`, `Zone.Zone:Read` | Attacker can edit these zones' DNS records. Still can't touch other zones, billing, other account settings, or Cloudflare features unrelated to DNS. |

Adding a zone to this project means adding it to both tokens' Zone Resources — a deliberate, visible step in the Cloudflare dashboard, not something that happens implicitly. A zone never listed there stays completely unreachable by either token, no matter what `dnsconfig.js` says.

Separating read from write further limits exposure: the read-only token is the one that ever runs on your local machine and in the PR-triggered `preview` workflow — the two places most exposed to human error or (in principle) a malicious PR. The write token exists in exactly one place: the GitHub Actions secret used by `apply.yml`, which only runs on `main` after a merge.

## No secrets in git, ever

- `creds.json` is committed, but it contains **zero secret material** — its value is a literal env-var reference (`"$CLOUDFLARE_API_TOKEN"`), not a token.
- `.env` (the actual local secret) is gitignored from the repo's very first commit.
- `.env.example` documents the shape without a real value, so a new clone knows what to fill in.

If you ever suspect a real token was committed, treat it as compromised: revoke it in the Cloudflare dashboard immediately (a `git revert` or history rewrite does **not** undo the exposure — assume anyone who cloned the repo in that window has it), then issue a new one and update the corresponding GitHub secret.

## Fork safety in the preview workflow

`preview.yml` uses the `pull_request` trigger, not `pull_request_target`. This matters because `pull_request_target` runs with the **base** repo's secrets even for PRs from forks — a classic way for a malicious fork PR to exfiltrate secrets via a modified workflow file. `pull_request` instead runs with the PR branch's own workflow file and no access to repo secrets when the PR comes from a fork. This repo has no external contributors today, but the setting costs nothing and removes an entire class of future risk.

## Why no Environment approval gate

GitHub Environments with required reviewers let you force a manual approval click between a merge and the workflow that acts on it — a common pattern for gating production deploys. This project intentionally does **not** use one for `apply.yml`, because the project is solo-maintained: a required-reviewer rule would need a second person to approve, which isn't possible to satisfy meaningfully when there's only one maintainer. The PR review + reading the `DNS Preview` diff comment before merging serves as the actual checkpoint instead.

**If collaborators join this project**, revisit this: move `CLOUDFLARE_API_TOKEN_WRITE` into a `production` GitHub Environment (Settings → Environments) with required reviewers, and reference `environment: production` in `apply.yml`'s job — this forces a second pair of eyes between "PR merged" and "DNS actually changed," which matters more once more than one person can merge.

## Why the PR gate is enforced inside `apply.yml`, not by GitHub branch protection

GitHub's server-side required-status-check branch protection needs GitHub Pro (or a paid org plan)
on a private repository, and repository rulesets need the same. If you're on the free tier of a
private repo, check directly rather than assuming either is available:

```
$ gh api repos/<owner>/<repo>/rulesets
$ gh api repos/<owner>/<repo>/branches/main/protection
```

A `403 Upgrade to GitHub Pro or make this repository public` response means neither is available to
you. Rulesets are commonly assumed to be a free-tier workaround for classic branch protection — they
are not.

Where that's the case, the primary gate can instead be enforced **inside `apply.yml` itself**: the
first step of the `apply` job calls the GitHub API to confirm the commit being applied is
associated with a merged pull request, and fails the run otherwise. This runs server-side on
GitHub's runners, so unlike a local git hook it cannot be bypassed with `--no-verify`, skipped by a
web-UI merge, or skipped by a clone that never opted into `core.hooksPath`. See
[ci-cd-pipeline.md](ci-cd-pipeline.md) for the exact check and its one documented limitation.

`.githooks/pre-push` remains in place as a **local convenience** — it gives faster feedback (a
`dnscontrol preview` before you even push) without waiting for CI — but it is not the thing actually
preventing an unreviewed apply once the in-workflow check above is in place. Without that check
(e.g. if you remove it), the pre-push hook is the only thing standing between a local `git push` and
an unreviewed change, and it has real limits: it doesn't fire on a GitHub-web-UI merge, only on a
local `git push`, and is bypassable with `--no-verify`.

If this repo is ever made public, or the account upgraded to GitHub Pro, switching to real
server-side branch protection (requiring the `DNS Preview` check to pass before merge) would be a
strictly stronger *additional* layer and is worth doing at that point — it would sit in front of the
in-workflow check, not replace it.

## Key Vault-backed tokens

For a solo operator, the read-only token living in a local `.env` (gitignored, never committed) is
a reasonable tradeoff. It's a worse one once other people are involved — a static token copied to
someone's machine outlives their engagement unless someone remembers to rotate it, and "remember to
rotate a token" is exactly the kind of manual step that gets missed.

`scripts/dnsctl.py` supports fetching `CLOUDFLARE_API_TOKEN` from **Azure Key Vault** instead, via
the Azure CLI (`az keyvault secret show`) — no new Python dependency, `az` is just another external
tool in the same category as `gh`/`dnscontrol`. Set `CLOUDFLARE_KEYVAULT_NAME` (and optionally
`CLOUDFLARE_KEYVAULT_SECRET_NAME`, default `cloudflare-api-token-readonly`) with **no**
`CLOUDFLARE_API_TOKEN` in `.env`, and every command that needs it fetches the current value at
runtime, holds it only in memory for that process, and never writes it to disk. `.env`/the
environment still take priority when a literal token is present, so a solo operator who hasn't set
up a vault sees no change in behavior — see `.env.example`.

This makes onboarding/offboarding a collaborator mostly a matter of their **Azure AD (Entra ID)**
identity rather than a separate credential to track:

- **Onboard**: grant their identity the **Key Vault Secrets User** role, scoped to just this
  secret (not the whole vault, if it holds anything else) — no token is ever typed into a file or
  chat message.
- **Offboard**: disable their Azure AD account (as you would for any other system access) or remove
  the role assignment — the next `az keyvault secret show` on their machine fails immediately, with
  no separate "did we rotate the Cloudflare token too?" step to remember.

This only covers the **read-only** token. The write token still lives in exactly one place (the
GitHub Actions secret used by `apply.yml`) regardless of whether Key Vault is in use.

For the concrete "how do I grant/revoke this" steps (exact `az` commands, role scoping, verifying a
grant, auditing who currently has access), see [keyvault-access.md](keyvault-access.md). For what a
collaborator runs on their own machine to consume it, see
[contractor-setup-guide.md](contractor-setup-guide.md) (written with a contracted/external
collaborator in mind, but applicable to any new team member).

## Onboarding and offboarding: what actually needs to happen

Handing someone write access to this **repo** is not, by itself, handing them the ability to change
production DNS unsupervised — see the access-boundary list in
[DNS_Change_Process.md](DNS_Change_Process.md#access-boundaries-that-make-this-safe-for-contractors-by-construction).
So most of onboarding/offboarding really is just repo + identity access, not a pile of DNS-specific
secrets to separately track:

- **Repo access** (GitHub team membership / collaborator list) — controls who can open PRs and,
  where real branch protection is available, who can merge.
- **Azure AD identity**, if Key Vault is in use — controls who can fetch the read-only Cloudflare
  token at all. Disabling this account on offboarding is the one non-GitHub step.
- **GitHub Actions secrets** (`CLOUDFLARE_API_TOKEN_WRITE`, `CLOUDFLARE_API_TOKEN_READONLY`) are
  never contractor-visible in the first place — nothing to revoke per-person there.

The one thing that **isn't** automatically tied to access removal: if someone's `.env` ever held a
literal `CLOUDFLARE_API_TOKEN` (the non-Key-Vault path), that value doesn't stop working just
because their GitHub/Azure AD access is gone — it's a bare string on their disk until the token
itself is rotated. This is exactly why Key Vault is the recommended setup once more than one person
needs the token: disabling their identity actually revokes access, whereas revoking a
hand-distributed static token requires a separate rotation step every time. If Key Vault isn't in
use, add "rotate the read-only token" to your offboarding checklist explicitly — don't assume
removing repo access is sufficient.

## Token rotation

Zone-scoped tokens limit the damage of a leak, but don't eliminate the value of rotating them periodically (roughly every 90 days is a reasonable default) — see [operations.md](operations.md#rotating-cloudflare-api-tokens) for the exact steps.

## Longer-term hardening path

Static, long-lived secrets stored as GitHub Actions secrets are a reasonable Phase 1, but they're not the strongest option available. If this project grows in importance or team size, consider:

- **GitHub OIDC + a cloud secret manager** (HashiCorp Vault, AWS Secrets Manager, etc.) — the write token is fetched at workflow runtime and never stored as a static GitHub secret at all, closing the window where a compromised repo admin (or a GitHub platform incident) could read it directly out of secret storage.
- **Real branch protection** once GitHub Pro or a public repo is in play (see above).
- **A `production` Environment with required reviewers** once there's more than one maintainer (see above).
