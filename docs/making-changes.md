# Making a DNS change

This is the day-to-day workflow for adding, editing, or removing a record on any zone this project manages (`example.com`, `example.org`). For record syntax details (A, CNAME, MX, TXT, etc.), see [record-types.md](record-types.md).

## The workflow

The short version, using the [`dnsctl.py` helper](dnsctl-cli.md):

```sh
# edit dnsconfig.js
python scripts/dnsctl.py submit "Add CNAME for myapp"   # preview, branch, commit, push, open PR
python scripts/dnsctl.py status                          # wait for the DNS Preview check
python scripts/dnsctl.py review <PR#>                     # read the diff before merging
python scripts/dnsctl.py merge <PR#>                      # merge once it looks right
```

Or the equivalent manual steps:

```sh
git checkout main
git pull
git checkout -b <short-description-of-change>

# edit dnsconfig.js

set -a && source .env && set +a
dnscontrol preview      # sanity-check the diff BEFORE pushing anything

git add dnsconfig.js
git commit -m "Add CNAME for <thing>"
git push -u origin <branch-name>
gh pr create --title "..." --body "..."
```

Then, either way:

1. Wait for the **DNS Preview** GitHub Actions check to finish on the PR — it posts a comment with the exact `dnscontrol preview` output.
2. **Read the diff comment carefully.** Confirm it shows only the record(s) you intended to change — nothing else. If it shows unrelated `DELETE`s, something is wrong with your `dnsconfig.js` edit (a missing comma, a duplicated block, etc.) — do not merge; fix it first. `python scripts/dnsctl.py lint` catches some of these mistakes (duplicate lines, missing trailing dots, CNAME conflicts) instantly, without waiting on CI.
3. Merge the PR.
4. The **DNS Apply** workflow runs automatically on `main` and applies the change to Cloudflare via `dnscontrol push`. Check the [Actions tab](../../actions) to confirm it succeeded.
5. Verify the record is live (e.g. `dig myapp.example.com` or check the Cloudflare dashboard).

`python scripts/dnsctl.py approve <PR#>` also exists, but on this solo-maintained repo it will always fail with GitHub's "can not approve your own pull request" restriction — see [dnsctl-cli.md](dnsctl-cli.md#approve). Merging doesn't require an approval here, so that's expected and not a blocker.

## Adding a new record

Easiest: use the interactive wizard, which parses the fully-qualified name, prompts for whatever it needs, and shows you the exact line before writing it:

```sh
python scripts/dnsctl.py record add myapp.example.com
python scripts/dnsctl.py record add myapp --zone example.org   # bare name needs --zone
```

See [dnsctl-cli.md](dnsctl-cli.md#record-add--record-remove--record-list) for the full walkthrough, multi-zone handling, and non-interactive flags. It currently supports A, CNAME, MX, and TXT.

Or edit `dnsconfig.js` directly — add a new line inside that zone's `D("example.com", ...)` block, near the other records of the same type (keeps the file scannable). Example — adding a CNAME:

```js
CNAME("myapp", "example.com.", CF_PROXY_ON),
```

See [record-types.md](record-types.md) for the full set of record types and their arguments.

## Editing an existing record

```sh
python scripts/dnsctl.py record edit myapp.example.com --value newtarget.example.com.
```

Or find the line for that record and change its value/TTL/flags in place by hand. Either way, dnscontrol diffs by exact match, so `preview` will show it as a paired `DELETE` (old value) + `CREATE` (new value) — that's expected and not a sign of a problem.

If your residential/public IP changed and several `A` records point at the old one, `record update-ip` handles all of them in one pass instead of editing each individually — see [dnsctl-cli.md](dnsctl-cli.md#record-update-ip).

## Removing a record

```sh
python scripts/dnsctl.py record remove myapp.example.com
```

Or delete the line from `dnsconfig.js` directly. Either way, `preview` will show a `DELETE` correction for it — confirm it's the only thing that shows up before merging.

## A note on TXT records with multiple values

Some names in this zone (e.g. `_acme-challenge`) intentionally have several `TXT` records with different values (ACME/Let's Encrypt validation tokens, etc.). Each is its own separate `TXT(...)` line in `dnsconfig.js` — don't try to combine them into one call. When ACME tokens rotate, old ones can usually be deleted; check with whatever issued them (e.g. your reverse proxy / cert manager) if unsure before removing one.

## Common mistakes to avoid

- **Forgetting the trailing dot** on CNAME/MX targets (`example.com` vs `example.com.`). dnscontrol will usually catch this as a config error at `preview` time, but always double check.
- **Editing `dnsconfig.js` without running `preview` first.** The whole point of this project is to see the diff before it happens — always run it locally before opening a PR, and always read the PR's automated diff comment before merging.
- **Merging a PR whose preview diff looks unexpected.** If you don't understand every line of the diff, don't merge — ask, or investigate first. See [operations.md](operations.md) for how to recover if a bad change does get applied.
- **Editing DNS directly in the Cloudflare dashboard.** Any out-of-band dashboard change will show up as a "phantom" correction the next time someone runs `preview` (dnscontrol will want to revert it back to match `dnsconfig.js`). If a dashboard change is intentional, reflect it in `dnsconfig.js` in the same PR, or re-run the zone import (see [operations.md](operations.md#re-baselining-the-zone)).

## Reviewing someone else's PR

Even on a solo project, get in the habit of treating the automated preview diff as the actual review artifact:

- Does the diff contain only the corrections you'd expect from the stated change?
- Are proxy status (`CF_PROXY_ON` vs unproxied) and TTL what you intended?
- For anything touching mail (MX, SPF/DKIM/DMARC TXT records) or the apex domain, double- and triple-check — mistakes there can break email deliverability or take the whole domain down.
