# DNS Change Process

Standard process for anyone — contractor or staff — making a DNS change in this repo. This is the
process document to hand a new contractor once they've completed
[contractor-setup-guide.md](contractor-setup-guide.md); [making-changes.md](making-changes.md) and
[dnsctl-cli.md](dnsctl-cli.md) are the technical references it points into.

## Roles and access

| Role | Repo access | Can do |
|---|---|---|
| **Requester** | none required | Opens a ticket describing the desired DNS change (what record, why, by when). |
| **Contractor / operator** | Write access to the repo, own fork or branch — **not** a maintainer/admin | Runs `begin` → edits `dnsconfig.js` → `submit`. Cannot merge their own PR unless also an approver (see below). Never holds the write-scoped Cloudflare token. |
| **Approver** (internal staff) | Maintainer on the repo | Reviews the PR's `DNS Preview` diff, merges. This is the control that catches a mistake before it reaches Cloudflare. |

**Important caveat, checked directly against this repo** (see
[security.md#why-the-pr-gate-is-enforced-inside-applyyml-not-by-github-branch-protection](security.md#why-the-pr-gate-is-enforced-inside-applyyml-not-by-github-branch-protection)):
GitHub's server-side branch protection and rulesets both return `403 Upgrade to GitHub Pro or make
this repository public` on this repo's current plan. That means **"require 1 approving review
before merge" cannot actually be enforced by GitHub today** — it would be a checkbox that doesn't
exist, not a control you can rely on. Two ways to get real enforcement:

1. **Upgrade to GitHub Pro/Team** (or make the repo public, which isn't appropriate here) — at that
   point turn on: require a PR before merging, **require ≥1 approving review from someone other
   than the author**, and require the `DNS Preview` status check to pass, all as *required* branch
   protection rules. Once real contractors are being paid to operate this, the cost of a paid GitHub
   plan is trivial next to the risk of an unreviewed DNS change reaching production. This is the
   recommended path.
2. **Until/if that upgrade happens**, `apply.yml` already enforces "someone else reviewed this" the
   same way it enforces "this came from a merged PR" — server-side, inside the workflow, so it can't
   be bypassed by a web UI merge or a local `--no-verify`. It calls the GitHub API for the merged
   PR's reviews and refuses to apply (not refuse to *merge* — merging still can't be blocked without
   paid branch protection) unless there's an `APPROVED` review from someone other than the author.
   **This check is off by default** — gated by `REQUIRE_NON_AUTHOR_REVIEW` in `apply.yml`'s
   job-level `env:`, `"false"` out of the box because a solo-operator setup has exactly one
   contributor and an always-on requirement would just permanently route every apply through the
   `workflow_dispatch` escape hatch. **Set it to `"true"` before onboarding the first contractor or
   anyone else who'll merge DNS changes** — until then, a merge-without-review is still only a
   process rule, not a technical one. That check only runs *after* merge, though — a self-merged PR
   fails `DNS Apply` rather than being blocked from merging at all, leaving `main` merged but not
   applied until it's fixed. `dnsctl merge` can catch the same problem *before* merging: set
   `DNSCTL_REQUIRE_NON_AUTHOR_REVIEW=true` in `.env` (see `.env.example`) and it refuses to merge a
   PR with no non-author approval, same rule, cheaper failure. This is a local convenience only, not
   the enforcement — `.env` never reaches CI, so `REQUIRE_NON_AUTHOR_REVIEW` in `apply.yml` must
   still be `"true"` for this to actually be required rather than optional per-contributor.
- Consider a **CODEOWNERS** entry on `dnsconfig.js` naming specific internal approvers once real
  branch protection is available — it auto-requests the right reviewer instead of leaving it to
  whoever notices the PR.
- Consider requiring approval specifically for MX/SPF/DKIM/DMARC/CAA/apex changes (mail and root
  domain are the highest-blast-radius record types) — a CODEOWNERS rule scoped to lines containing
  those tokens isn't expressible directly, but a PR template checklist item (below) covers the same
  ground procedurally regardless of which enforcement path above is in place.
- **Restrict who can push to `main`** to nobody (all changes via PR) — already the default here,
  and not affected by the Pro/Team limitation above.

## Access boundaries that make this safe for contractors by construction

These already exist in this repo (see [security.md](security.md)) and are why a contractor can be
handed write access to the repo without being handed the ability to actually change production DNS
unsupervised:

- The **write-scoped Cloudflare token** (`CLOUDFLARE_API_TOKEN_WRITE`) lives only as a GitHub
  Actions secret used by `apply.yml` — it is never in a contractor's `.env`, never on their
  machine. A contractor's local setup only ever needs the **read-only** token, which can preview
  and diff but cannot change anything on Cloudflare.
- That read-only token doesn't need to sit in a contractor's local `.env` at all — set
  `CLOUDFLARE_KEYVAULT_NAME` and it's fetched from Azure Key Vault at runtime instead, scoped to
  their Azure AD identity, never written to disk. See
  [security.md#key-vault-backed-tokens](security.md#key-vault-backed-tokens). This is the
  recommended setup for contractors specifically because it ties offboarding to disabling one
  identity, rather than also requiring a manual token rotation. See
  [keyvault-access.md](keyvault-access.md) for the exact grant/revoke steps and
  [contractor-setup-guide.md](contractor-setup-guide.md) for what the contractor does with it.
- `dnsctl.py push` (the one local command that *can* apply directly) requires typing `APPLY` and a
  token most contractors won't have — treat it as staff/emergency-only, and consider not
  distributing that token to contractors at all if it's never actually needed.
- A server-side gate rejects direct pushes to `apply.yml` itself, so the pipeline that holds the
  write token can't be quietly modified from a feature branch.
- Every change is legible before it lands: the `DNS Preview` bot comment on the PR is the literal
  diff that will be applied to Cloudflare, not a summary of one.

If you're onboarding contractors for real, also do the mundane but load-bearing things: unique
GitHub accounts per contractor (never a shared login), 2FA required org-wide, an offboarding
checklist that revokes repo access immediately when a contractor's engagement ends (a stale
credential is a bigger risk in practice than almost anything in this pipeline), **and set
`REQUIRE_NON_AUTHOR_REVIEW: "true"` in `apply.yml`** (see above) — without that flip, the "Approver
reviews and merges" step below is still only a written process rule, not something `apply.yml`
actually enforces. Also set **`REQUIRE_TICKET_REFERENCE: "true"` in `preview.yml`** at the same
time, so every PR is required to trace back to the ticket that authorized it — without that flip,
step 1's "requester opens a ticket" is likewise only a written process rule.

## The process

### 1. Requester opens a ticket

State the record, the zone, the desired value, and why. For anything touching mail (MX, SPF, DKIM,
DMARC) or the domain apex, say so explicitly — these need the extra care called out below. Use the
[DNS change request issue template](../../issues/new?template=dns_change_request.yml)
(`.github/ISSUE_TEMPLATE/dns_change_request.yml`) if there's no existing external ticket (Jira,
ServiceNow, etc.) to use instead — either way, the resulting ticket ID is what the contractor
references on the PR in step 4.

### 2. Contractor starts the change

```sh
python scripts/dnsctl.py begin "short description of the change"
```

This is the "idiot-proof" entry point — it exists so a contractor never starts editing on a stale
or drifted checkout. It does all of this automatically, in order:

1. Refuses to run if you have uncommitted changes (nothing gets silently overwritten).
2. Syncs `main` to `origin/main` (fast-forward only — never force-overwrites local work).
3. Checks live Cloudflare state against `dnsconfig.js` (`dnscontrol preview --expect-no-changes`)
   and warns if they've drifted apart — e.g. someone edited the dashboard directly, or a previous
   apply didn't fully land. **Don't build a new change on top of a warning you haven't
   investigated.**
4. Creates a fresh branch for the change.
5. Folds in any `_acme-challenge` TXT record changes from the out-of-band ACME client, so that
   noise doesn't show up mixed into the diff of an unrelated PR later.

See [dnsctl-cli.md#begin](dnsctl-cli.md#begin) for the full option reference.

### 3. Contractor makes the edit

Either the guided wizard or a direct edit:

```sh
python scripts/dnsctl.py record add myapp.example.com
# or edit dnsconfig.js directly
```

Then, always:

```sh
python scripts/dnsctl.py lint       # fast, offline sanity check
python scripts/dnsctl.py preview    # the actual diff against live Cloudflare
```

**Do not proceed past `preview` if the diff shows anything beyond the intended record(s).** An
unexpected `DELETE` almost always means a mistake in the edit (missing comma, duplicated block) —
fix it before submitting, don't submit and hope review catches it.

### 4. Contractor submits

```sh
python scripts/dnsctl.py submit "[INC0012345] Add CNAME for myapp"
```

Opens the PR. From here the contractor's part is done except for responding to review feedback.

**Include the ticket ID from step 1** (bracketed, e.g. `[INC0012345]` or `[JIRA-1234]`) in the PR
title or body — required whenever `REQUIRE_TICKET_REFERENCE` is `"true"` in
`.github/workflows/preview.yml` (see
[making-changes.md#team-enterprise-mode-referencing-a-ticket](making-changes.md#teamenterprise-mode-referencing-a-ticket)).
It's off by default for the same solo-operator reason `REQUIRE_NON_AUTHOR_REVIEW` is — **flip it on
alongside `REQUIRE_NON_AUTHOR_REVIEW` before onboarding contractors**, so every merged change
traces back to the ticket that authorized it.

### 5. Approver reviews and merges

```sh
python scripts/dnsctl.py status                 # confirm DNS Preview passed
python scripts/dnsctl.py review <PR#>           # read the diff
python scripts/dnsctl.py merge <PR#> --wait     # merge -> triggers DNS Apply, then wait and confirm it's live
```

Review checklist (also see [making-changes.md#reviewing-someone-elses-pr](making-changes.md#reviewing-someone-elses-pr)):

- [ ] The diff contains only the correction(s) the ticket asked for — nothing else.
- [ ] Proxy status (`CF_PROXY_ON` vs unproxied) and TTL match intent.
- [ ] **Mail records (MX/SPF/DKIM/DMARC) or the apex domain**: double- and triple-checked — a
      mistake here can break email deliverability or take the whole domain down. Consider a second
      approver for these specifically.
- [ ] If the diff shows anything the contractor's ticket didn't mention, stop and ask before
      merging.

### 6. Verify it went live

`merge --wait` already did this automatically (it's what step 5's command runs above); if you
merged without `--wait`, or want to re-confirm later, run:

```sh
python scripts/dnsctl.py validate <PR#>
```

This waits for `DNS Apply` to finish and re-runs `dnscontrol preview --expect-no-changes` to
confirm live Cloudflare matches `dnsconfig.js` exactly — see
[dnsctl-cli.md#validate](dnsctl-cli.md#validate). Optionally also confirm with `dig` or the
Cloudflare dashboard for visibility outside this repo's own tooling. This closes the loop on the
ticket.

## Rolling back a change

Because every change lands through a PR, undoing one is also just a PR — never a direct edit or
push to `main`. Use this instead of hand-reverting `dnsconfig.js`, which risks reverting it to the
wrong prior state.

```sh
python scripts/dnsctl.py history              # list recent merges touching dnsconfig.js
python scripts/dnsctl.py rollback <PR#>        # or a commit SHA from `history`
```

`rollback` creates a new branch, reverts the target commit (handles both squash- and true-merge
history), runs `preview` so you can confirm the diff is the exact inverse of the original change,
then opens a PR — which then goes through the **same** review → merge → `DNS Apply` pipeline as any
other change. There is no fast path that skips review for a rollback; an emergency is exactly the
situation where a wrong revert is most costly, so the diff still gets read before it applies.

See [dnsctl-cli.md#rollback](dnsctl-cli.md#rollback) for the full mechanics, and
[operations.md](operations.md) for recovering from a bad change that reached Cloudflare via a route
other than this pipeline (e.g. a manual `push`).

## Concurrency at scale

This process works fine with one contributor at a time. With many operators all editing the same
`dnsconfig.js` — often from tickets, often at the same time — two things get much more common than
they are for a solo maintainer, and the defenses below layer rather than any one of them being
sufficient alone.

**Primary mitigation: keep PRs small and single-purpose.** One ticket = one PR = ideally one
record or a tightly related group (e.g. a CNAME plus its ACME challenge TXT). This is the cheapest
lever available: the smaller and more localized a PR's diff, the less likely it overlaps another
in-flight PR's lines at all, and the easier it is for an approver to actually understand what
they're merging (also required by the review checklist above). Don't batch unrelated changes into
one PR to save round-trips — that trades a small amount of contributor convenience for a much
larger collision and review-quality cost once many people are editing concurrently.

**Two distinct collision shapes, at scale:**

1. **Two PRs touching *different* lines, but the second one to merge is now stale.** Both can pass
   `DNS Preview` individually, yet the second PR's reviewed diff was computed against a `main` that
   no longer matches what the first PR just merged. Left unhandled, merging the second PR could
   silently re-apply or clobber something the first PR just changed.
2. **Two PRs touching *overlapping* lines** — e.g. both adding a CNAME in the same
   alphabetically-sorted block in `dnsconfig.js`. This is an ordinary git merge conflict; GitHub
   simply won't offer a clean merge for the second PR until it's rebased, no repo-specific handling
   needed or possible here.

**Mechanical backstop for (1): GitHub Merge Queue.** Once the team is big enough to justify the
plan cost, enable GitHub's native Merge Queue for `main` — see
[branch-protection-and-secrets.md#github-enabling-merge-queue-for-main](branch-protection-and-secrets.md#github-enabling-merge-queue-for-main)
for the setup steps and plan requirement (GitHub Team/Enterprise Cloud, or a public repo — not
available on Free/Pro for a private repo, same tier restriction as real branch protection
elsewhere in this doc). Merge queue serializes merges to `main`, re-running `DNS Preview` against
each PR combined with the *latest* `main` immediately before it actually merges — so a PR that's
gone stale relative to something that just landed is caught and rejected from the queue instead of
being merged blind. It does not build or require any custom bot; it's a GitHub repo setting.

**Safety net regardless of whether a merge queue is configured.** Even without a merge queue (e.g.
the plan doesn't support it yet, or it's simply not turned on), `apply.yml`'s "Verify applied diff
matches reviewed diff" step still catches problem (1) at apply time: right before `dnscontrol push`
runs, it re-runs `preview` and fails loudly — without applying — if that output no longer matches
the artifact captured when the merged PR's `DNS Preview` check ran. This is strictly a later,
coarser catch than a merge queue (it fails after merge, not before, and the PR is already sitting
in `main` when it fires), but it means a stale merge is never silently double-applied even with no
merge queue in place at all.

**Read together:** convention (small PRs) shrinks how often collisions happen in the first place;
merge queue (once affordable) catches a staleness collision mechanically before it merges; the
post-merge verify step catches the same class of problem after merge, regardless of the other two,
as the last line of defense. None of these three replaces either of the others.

## Common mistakes to avoid

See [making-changes.md#common-mistakes-to-avoid](making-changes.md#common-mistakes-to-avoid) — it's
short and applies to every contractor exactly as written. The two worth repeating here: never edit
the Cloudflare dashboard directly (it becomes a phantom correction on the next `preview`, since
`dnsconfig.js` is the enforced source of truth), and never merge a PR whose diff you don't fully
understand.
