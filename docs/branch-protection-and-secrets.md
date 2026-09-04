# Branch policies and secrets: secure setup (GitHub and Azure DevOps)

This project's security model (see [security.md](security.md)) depends on two mechanical things actually being configured correctly: the Cloudflare credentials must be stored so that only the right workflow can read the right one, and merges to `main` must be gated on the `DNS Preview` check actually passing. This doc is the concrete "how" for both, on GitHub (what this template uses) and on Azure DevOps (if you're porting this pattern there instead).

## GitHub: configuring secrets securely

### Repository secrets vs. Environment secrets

GitHub Actions secrets can live at two levels:

| Level | Where | Who/what can read it |
|---|---|---|
| **Repository secret** | Settings → Secrets and variables → Actions → Repository secrets | Any workflow run in this repo, on any branch, for any job — no extra gate. |
| **Environment secret** | Settings → Environments → `<name>` → Environment secrets | Only a job that declares `environment: <name>` — and only after any protection rules on that Environment (required reviewers, wait timer, branch restriction) are satisfied. |

This template uses **repository secrets** for both `CLOUDFLARE_API_TOKEN_READONLY` and `CLOUDFLARE_API_TOKEN_WRITE`, since both are already scoped down to least-privilege Cloudflare tokens (see [security.md](security.md#least-privilege-two-scoped-tokens-instead-of-one-broad-one)) and there's no second maintainer to act as a required reviewer. **If more than one person can merge to `main`**, move `CLOUDFLARE_API_TOKEN_WRITE` into an Environment (e.g. `production`) with required reviewers instead — this forces a manual approval click between "PR merged" and "`dnscontrol push` actually runs," which a plain repository secret can't do.

### Setting secrets

Via the GitHub CLI (recommended — never types the secret into a browser form that could be autofilled/logged, and scriptable for rotation):

```sh
gh secret set CLOUDFLARE_API_TOKEN_READONLY --repo YOUR_USERNAME/YOUR_REPO
gh secret set CLOUDFLARE_API_TOKEN_WRITE --repo YOUR_USERNAME/YOUR_REPO
```

Each prompts for the value on stdin (nothing is echoed to the terminal). To set an Environment-scoped secret instead: `gh secret set NAME --repo YOUR_USERNAME/YOUR_REPO --env production`.

Avoid `--body <value>` on the command line for anything sensitive — command-line arguments can end up in shell history and process listings. Piping is safer if you're scripting this: `printf '%s' "$TOKEN" | gh secret set CLOUDFLARE_API_TOKEN_WRITE --repo YOUR_USERNAME/YOUR_REPO`.

### What actually keeps a GitHub Actions secret safe

- **Never referenced in a workflow triggered by `pull_request_target` without care.** `pull_request_target` runs with the base repo's secrets even for a fork's PR — this template's `preview.yml` deliberately uses `pull_request` instead, which gets no secrets at all when the PR is from a fork. See [security.md](security.md#fork-safety-in-the-preview-workflow).
- **Never printed to workflow logs.** GitHub automatically masks a secret's exact value in logs once it's referenced via `secrets.*`, but this only works for exact-match substrings — don't `base64`, split, or otherwise transform a secret before an accidental `echo`, or the mask won't catch it.
- **Never passed as a `workflow_dispatch` or `pull_request` input** — inputs are visible in the Actions UI and API to anyone who can view the run; only `env: SOMETHING: ${{ secrets.X }}` on the job/step keeps it out of that surface.
- **Scoped to the narrowest token possible before it's ever pasted into GitHub** — a leaked GitHub secret is exactly as dangerous as the credential it holds. This is why the Cloudflare tokens are zone-scoped read/write splits in the first place (see [security.md](security.md)), not because GitHub's secret storage itself is weak.
- **Rotated periodically** — see [operations.md](operations.md#rotating-cloudflare-api-tokens). A secret's value having lived in GitHub's encrypted-at-rest storage for a long time isn't itself a problem, but rotation limits the damage window if it's ever exposed some other way (a compromised runner, a workflow bug that echoes it, a screen-share).
- **Never committed as a fallback "just in case."** If a secret is ever accidentally committed (even for one commit, even in a private repo), treat it as compromised immediately — see [security.md](security.md#no-secrets-in-git-ever). Revoke and reissue; don't rely on history rewriting.

## GitHub: configuring branch protection for `DNS Preview`

Real server-side branch protection — refusing to even offer the merge button until the `DNS Preview` check passes — needs either a public repository or GitHub Pro/Team/Enterprise on a private one. (This template ships a local `.githooks/pre-push` fallback for the case where neither is available — see [ci-cd-pipeline.md](ci-cd-pipeline.md) and [security.md](security.md#why-branch-protection-is-a-local-hook-not-a-github-setting) for what that hook does and doesn't cover.)

**Via the GitHub web UI:** Settings → Branches → Add branch protection rule → branch name pattern `main` → enable:
- **Require a pull request before merging**
- **Require status checks to pass before merging**, then search for and select the `DNS Preview` check (it only appears in the list after it's run at least once, so open one PR first)
- Optionally **Require branches to be up to date before merging** if you want to force a rebase/merge of `main` before allowing the merge

**Via the GitHub CLI / API** (useful for scripting this into a repo-setup script rather than doing it by hand each time):

```sh
gh api repos/YOUR_USERNAME/YOUR_REPO/branches/main/protection \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  -f 'required_status_checks[strict]=true' \
  -f 'required_status_checks[contexts][]=DNS Preview' \
  -F 'enforce_admins=true' \
  -f 'required_pull_request_reviews[required_approving_review_count]=0' \
  -F 'restrictions='
```

Set `required_approving_review_count` to `1` (or higher) instead of `0` once there's more than one maintainer who can review someone else's PR — a solo maintainer can't satisfy a required-reviewer rule on their own PRs (GitHub blocks approving your own PR), so `0` is the correct solo-maintainer value, not a placeholder to "fix later" by itself.

## Azure DevOps: the equivalent setup

If you're adapting this project to Azure DevOps (Azure Repos + Azure Pipelines) instead of GitHub, the same two concerns map onto Azure's own primitives — the names differ, the principles don't.

### Secure variables and credentials

- **Pipeline variables marked "secret"** (the lock icon in the pipeline editor, or `isSecret: true` via the API) are the direct equivalent of a GitHub Actions repository secret — encrypted at rest, masked in logs, not readable back out through the UI once saved.
- **Variable groups** (Pipelines → Library) let you group related secrets (e.g. both Cloudflare tokens) and, importantly, can be linked to **Azure Key Vault** instead of storing the value directly in Azure DevOps — the pipeline fetches the current value from Key Vault at run time, which is the closer analogue to the "GitHub OIDC + external secret manager" hardening path mentioned in [security.md](security.md#longer-term-hardening-path).
- **Service connections** with least-privilege scoping are Azure DevOps' equivalent of a scoped API token when the target is Azure itself (e.g. an Azure DNS zone) — for a third-party API like Cloudflare's, a secret pipeline variable (or Key Vault-backed variable group) holding the Cloudflare token is the right mechanism, not a service connection.
- **Environment approvals and checks** (Pipelines → Environments → `<name>` → Approvals and checks) are the Azure DevOps equivalent of a GitHub Environment's required reviewers — attach the environment to the deploy stage that runs `dnscontrol push` so a human has to approve before the write-scoped credential is ever used, mirroring the GitHub Environment approach in [security.md](security.md#why-no-environment-approval-gate).
- Same rule as GitHub: never echo a secret variable in a script step, never pass it as a parameter to a task that logs its inputs, and scope the underlying Cloudflare token itself down to the zones this project manages regardless of which CI platform holds it.

### Branch policies (Azure Repos)

Project Settings → Repos → Branches → find `main` → Branch policies:

- **Require a minimum number of reviewers** — the direct equivalent of GitHub's required-approving-review-count; set to `0`-equivalent (Azure Repos actually requires at least 1 by default reviewer policy UI, but you can disable the policy entirely) for a solo maintainer, or `1+` once there's a second person.
- **Check for linked work items** / **Check for comment resolution** — optional, not part of this project's threat model, but available if useful.
- **Build validation** — this is the one that matters for parity with `DNS Preview`: add a build validation policy pointing at an Azure Pipeline that runs `dnscontrol preview` (you'd need an `azure-pipelines.yml` equivalent of this template's `preview.yml`, triggered on PR) and set it as **required**, not optional. A PR cannot complete until that pipeline run succeeds — functionally identical to GitHub's required status check.
- **Automatically included reviewers** can substitute for CODEOWNERS-style enforcement if this ever moves beyond a solo project.

This template ships GitHub Actions workflows (`preview.yml`/`apply.yml`), not Azure Pipelines YAML — porting them is mostly a syntax translation (the `dnscontrol` install step and the `preview`/`push` commands are identical either way), but that translation isn't included here.
