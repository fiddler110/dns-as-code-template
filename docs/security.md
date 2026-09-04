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

## Why branch protection is a local hook, not a GitHub setting

GitHub's server-side required-status-check branch protection needs GitHub Pro (or a paid org plan) on a private repository. This repo is private and on the free tier, so instead `.githooks/pre-push` enforces "don't push a broken config to `main`" locally. See [ci-cd-pipeline.md](ci-cd-pipeline.md) for exactly what it does and its limitations (notably: it doesn't fire on a GitHub-web-UI merge, only on a local `git push`).

If this repo is ever made public, or the account upgraded to GitHub Pro, switching to real server-side branch protection (requiring the `DNS Preview` check to pass before merge) would be strictly stronger and is worth doing at that point.

## Token rotation

Zone-scoped tokens limit the damage of a leak, but don't eliminate the value of rotating them periodically (roughly every 90 days is a reasonable default) — see [operations.md](operations.md#rotating-cloudflare-api-tokens) for the exact steps.

## Longer-term hardening path

Static, long-lived secrets stored as GitHub Actions secrets are a reasonable Phase 1, but they're not the strongest option available. If this project grows in importance or team size, consider:

- **GitHub OIDC + a cloud secret manager** (HashiCorp Vault, AWS Secrets Manager, etc.) — the write token is fetched at workflow runtime and never stored as a static GitHub secret at all, closing the window where a compromised repo admin (or a GitHub platform incident) could read it directly out of secret storage.
- **Real branch protection** once GitHub Pro or a public repo is in play (see above).
- **A `production` Environment with required reviewers** once there's more than one maintainer (see above).
