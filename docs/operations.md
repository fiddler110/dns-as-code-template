# Operations

Runbooks for things that come up occasionally: rotating tokens, re-baselining the zone, recovering from a bad apply, and troubleshooting known errors.

## Rotating Cloudflare API tokens

Recommended cadence: every ~90 days, or immediately if you suspect a leak.

1. In the Cloudflare dashboard, create a **new** token with the same scope as the one you're replacing (see [getting-started.md](getting-started.md#cloudflare-api-tokens) for the exact permissions table).
2. Update the corresponding secret:
   ```sh
   gh secret set CLOUDFLARE_API_TOKEN_READONLY --repo YOUR_USERNAME/YOUR_REPO
   gh secret set CLOUDFLARE_API_TOKEN_WRITE --repo YOUR_USERNAME/YOUR_REPO
   ```
3. Update your local `.env` if you rotated the read-only token (never store the write token locally — see [security.md](security.md)).
4. Revoke the old token in the Cloudflare dashboard once you've confirmed the new one works (open a trivial PR and check the `DNS Preview` comment succeeds).

## Re-baselining a zone

If DNS was ever changed directly in the Cloudflare dashboard (out-of-band, not through this repo), `dnsconfig.js` and live state will drift apart for that zone, and the next `preview` will show corrections that try to undo the dashboard change. To fold real, intentional dashboard changes back into `dnsconfig.js` for, say, `example.com`:

```sh
python scripts/dnsctl.py import --zone example.com
# or manually: set -a && source .env && set +a && \
#   dnscontrol get-zones --format=js --out=zone_import.js cloudflare example.com
```

(Swap in `--zone example.org` for that zone instead — `--zone` is required since this project manages more than one.)

Note the argument order if doing it manually: `get-zones [flags] <credkey> <zone>` — `cloudflare` here is the credential key from `creds.json`, not repeated. (An earlier draft of this project's docs got this wrong and doubled it up — if you see `get-zones ... cloudflare cloudflare example.com` anywhere, it's a leftover mistake. `scripts/dnsctl.py import` always uses the correct order.)

Then diff `zone_import.js` against that zone's current `D("example.com", ...)` block in `dnsconfig.js`, merge in whatever changed, delete `zone_import.js`, and confirm:

```sh
python scripts/dnsctl.py preview
# should report 0 corrections
```

Commit the result. Treat this the same as any other change — through a PR, with the preview diff reviewed — since a manual re-baseline can just as easily hide a mistake as fix one.

## Cleaning up stale ACME challenge TXT records

`_acme-challenge` TXT records accumulate over time as certificates renew — old validation tokens are usually safe to remove once the renewal they were for has completed, but nothing here tracks that automatically.

```sh
python scripts/dnsctl.py record prune-acme                # live cross-check by default (needs .env), report only
python scripts/dnsctl.py record prune-acme --offline       # skip the live check, heuristic only, no .env/network needed
python scripts/dnsctl.py record prune-acme --remove 2 3    # remove specific ones by index, with confirmation
python scripts/dnsctl.py record prune-acme --remove 1-6    # ranges/combos work too: 1,2,4-6
```

This is report-first and never guesses which token is still "current" on faith alone — with `.env` configured it cross-checks against real Cloudflare state automatically (falling back to the token-count heuristic if no token is set or the fetch fails), but still cross-check against whatever issued the certificate (e.g. your reverse proxy's ACME client logs) before removing one, since "gone from Cloudflare" only means gone *now*, not that it wasn't mid-renewal until just recently. The live check only annotates the report; it never edits `dnsconfig.js`. Note the direction of drift it can also surface: if an ACME client has its own separate Cloudflare credential and writes/deletes `_acme-challenge` records directly (bypassing this repo), `dnsconfig.js` and Cloudflare can diverge in *either* direction — a record deleted upstream but still listed locally will get silently recreated by the next unrelated `apply` (dnscontrol always pushes `dnsconfig.js` onto Cloudflare), while a record added upstream but missing locally would get deleted by that same apply. The live check flags both cases; only actually removing the stale line via `--remove` (through the normal PR flow) fixes the former, and folding a new live-only record into `dnsconfig.js` via [re-baselining](#re-baselining-a-zone) fixes the latter. See [dnsctl-cli.md](dnsctl-cli.md#record-prune-acme).

If an ACME client genuinely owns these records end-to-end (e.g. a reverse proxy renewing certs with its own Cloudflare token, as opposed to occasional manual dashboard edits), treating Cloudflare as the source of truth for just `_acme-challenge` and folding it into `dnsconfig.js` wholesale is usually less friction than reviewing individual add/remove drift every renewal:

```sh
python scripts/dnsctl.py record sync-acme
```

This adds whatever's live but not yet in `dnsconfig.js` and removes whatever's local but no longer live, after showing the diff and asking to confirm — then goes through the normal preview/PR/apply flow like any other change. It only ever touches `_acme-challenge` TXT lines; everything else in `dnsconfig.js` keeps `dnsconfig.js` → Cloudflare as the direction of truth. See [dnsctl-cli.md](dnsctl-cli.md#record-sync-acme).

## Responding to detected drift

`.github/workflows/drift.yml` (once its trigger is enabled — see [getting-started.md](getting-started.md)) runs `dnscontrol preview --expect-no-changes` against every managed zone on a weekly schedule using the **read-only** token. If it finds a difference between `dnsconfig.js` and live state, it fails and files (or updates) a single persistent GitHub issue titled "DNS drift detected" with the diff, rather than opening a new issue every run. You can also trigger it manually from the Actions tab (`workflow_dispatch`).

When that issue appears, decide which case you're in before doing anything else:

- **Legitimate dashboard change** (e.g. an emergency fix made directly in Cloudflare because the pipeline was down) — reconcile it into `dnsconfig.js` via the [re-baselining](#re-baselining-a-zone) process above, so the repo goes back to being the actual source of truth. Do this through a PR like any other change.
- **Unauthorized or unexplained change** — treat the write token as potentially compromised and [rotate it](#rotating-cloudflare-api-tokens) immediately, then investigate (Cloudflare's audit log will show who/what made the change).

Close the tracking issue once the drift is resolved either way — the workflow will reopen or refile it automatically if it recurs.

**Known limitation:** GitHub disables scheduled workflows on a repository with no push activity for 60 days. For a low-traffic config repo, drift detection can silently stop running if nothing else has been pushed in a while — a manual `workflow_dispatch` run periodically (or any other commit to the repo) resets that clock.

## Responding to a reachability failure

`.github/workflows/reachability.yml` runs after every successful `DNS Apply` run and `curl`s each proxied hostname over HTTPS. It only reports a *connection-level* failure — DNS resolution failure, TLS handshake failure, connection refused/timeout — not an HTTP error status, since a 4xx/5xx still proves the request reached something behind Cloudflare's edge. If it finds one, it files or updates a single persistent GitHub issue titled "DNS reachability check failed" (same dedup-by-marker pattern as drift/apply issues), listing which hostname(s) failed and why.

When that issue appears:

- **Confirm it's real** — try the hostname yourself (`curl -v https://<host>/` or a browser). Transient network blips on the runner are possible but should be rare given the built-in retries.
- **Wrong DNS target** — check the corresponding record in `dnsconfig.js` actually points at the right place (e.g. a `CNAME` to a tunnel/load-balancer target that no longer exists, or to the wrong backend). Fix it through a PR like any other change.
- **Backend/origin down** — if the DNS record itself looks correct, the problem is likely the origin (a stopped tunnel, a downed app, a misconfigured reverse proxy) rather than anything this repo manages — this check exists precisely to surface that class of problem, which the post-apply record-level verification can't see.

Close the tracking issue once the hostname is reachable again — the workflow will reopen or refile it automatically if it recurs.

## Recovering from a bad `apply`

If `dnscontrol push` applies something wrong (bad merge, typo that passed review, etc.):

1. **Don't panic-edit directly in the Cloudflare dashboard** — that just creates more drift to reconcile later.
2. Open a new PR that reverts the bad change in `dnsconfig.js` (`git revert <bad-commit>` is usually cleanest).
3. Confirm the `DNS Preview` comment on that PR shows exactly the correction that undoes the mistake.
4. Merge — `apply.yml` will push the fix to Cloudflare.

For anything affecting mail (MX/SPF/DKIM/DMARC) or the apex A record, this round-trip can take a few minutes to propagate depending on TTLs and Cloudflare's own caching — don't assume it's still broken just because a check a few seconds after merge still shows the old behavior.

## Enable DNSSEC (one-time, per zone)

This is entirely a manual, out-of-band process — **nothing in `dnsconfig.js` or the `apply` flow
touches it**. dnscontrol has a generic `AUTODNSSEC_ON` domain modifier, but dnscontrol's own
provider docs mark Cloudflare's support for it as unverified, and in practice Cloudflare doesn't
expose "sign this zone" as a DNS record dnscontrol can push — it's a zone-level toggle in
Cloudflare's dashboard/API, same as `REG` here being `NewRegistrar("none")` means DS-record
placement at the actual registrar is already outside this repo's automation.

To enable DNSSEC for real:

1. Cloudflare dashboard → the zone → **DNS** → **Settings** → **DNSSEC** → **Enable DNSSEC**, for
   each zone this project manages.
2. Confirm Cloudflare has signed the zone:
   ```sh
   dig DNSKEY example.com +short
   ```
   Should return one or more `DNSKEY` records once Cloudflare finishes signing (this can take a few
   minutes after enabling).
3. Get the DS record to submit at the registrar — the same Cloudflare dashboard **DNSSEC** page
   from step 1 shows the exact DS record (key tag, algorithm, digest type, digest) to give the
   registrar.
4. Log in to the actual registrar for that domain (not Cloudflare) and add the DS record in its
   DNSSEC/DS-record management page. The exact UI varies by registrar.
5. Wait for the registrar's change to propagate up to the parent zone (can take anywhere from
   minutes to a day depending on the registrar and TLD), then verify the full chain of trust at
   <https://dnsviz.net/> — enter the zone name and confirm it shows a validated chain with no
   errors. This check has to happen for real, after the DS record is live at the registrar; it
   can't be done from a sandbox or CI.
6. As a second check, `dig +dnssec example.com` should show the `ad` (authenticated data) flag once
   a validating resolver has picked up the change.

If step 5 ever shows a broken chain (e.g. after a key rollover at Cloudflare), the fix is almost
always to re-check and update the DS record at the registrar to match Cloudflare's current
DNSKEY — this repo's `dnsconfig.js` and `apply` flow don't touch either side of DNSSEC at all.

## Manually triggering an apply

`apply.yml` normally only runs on a push to `main`, and (if you've wired up the merged-PR gate
described in [security.md](security.md#why-the-pr-gate-is-enforced-inside-applyyml-not-by-github-branch-protection))
refuses to apply unless that commit is associated with a merged pull request. For the rare case
where you need to re-run an apply without a new commit — e.g. retrying after a transient Cloudflare
API error or an expired token, rather than pushing an empty commit — trigger it manually instead:

```sh
gh workflow run "DNS Apply" --repo YOUR_USERNAME/YOUR_REPO
```

or from the GitHub UI: Actions → DNS Apply → Run workflow. A manual (`workflow_dispatch`) run skips
the merged-PR guard, since triggering it is itself a deliberate, authenticated, and logged action —
use this instead of weakening or bypassing the guard for an emergency direct apply. It will be a
no-op (dnscontrol reports no changes) if `main` is already in sync with Cloudflare.

## Troubleshooting

### `if cloudflare apitoken is not set, apikey and apiuser must be provided`

`creds.json`'s JSON key must be the **literal string `apitoken`** (lowercase) — dnscontrol's Cloudflare provider looks for that exact field name. It's easy to instead name the key after your env var (e.g. `"CLOUDFLARE_API_TOKEN": "$CLOUDFLARE_API_TOKEN"`), which looks reasonable but doesn't work. Correct form:
```json
{
  "cloudflare": {
    "TYPE": "CLOUDFLAREAPI",
    "apitoken": "$CLOUDFLARE_API_TOKEN"
  }
}
```
The `$CLOUDFLARE_API_TOKEN` part (the value) is the env var reference and can be named anything as long as it matches what you export/source.

### `failed GetZone gzr: zone not found` when running `get-zones`

Usually means the command's positional arguments are wrong. The syntax is:
```sh
dnscontrol get-zones [flags] <credkey> <zone> [<zone2> ...]
```
`credkey` is the key name from `creds.json` (`cloudflare` in this project) — pass it **once**, not once as the credkey and again as if it were a zone.

### `preview` shows dozens of unexpected `DELETE` corrections

This almost always means `dnsconfig.js` doesn't actually reflect the live zone — e.g. it's still the empty skeleton, or a big chunk of records got accidentally deleted from the file. **Do not merge/push in this state** — a `push` here would delete real records. Re-run the [re-baselining](#re-baselining-the-zone) process and compare carefully before committing.

### GitHub Actions Docker build fails with `"/linux/amd64/dnscontrol": not found`

You're using the `DNSControl/dnscontrol@main` marketplace Action, which doesn't ship a binary on the `main` branch. See [ci-cd-pipeline.md](ci-cd-pipeline.md#known-gotcha-dont-use-the-dnscontroldnscontrolmain-marketplace-action) — this repo's workflows avoid that Action entirely in favor of downloading a pinned release binary.

### `git push` to `main` is blocked with `pre-push: dnscontrol preview failed`

The local safety hook caught a real problem — read the printed `dnscontrol preview` output above that line for the actual error (most often one of the two above). Fix it, re-run `dnscontrol preview` manually until it's clean, then push again. If you're certain the failure is spurious (e.g. `dnscontrol` genuinely isn't installed on this machine and you want to bypass just this once), you can `git push --no-verify`, but that skips the safety check entirely — prefer fixing the underlying issue.

### `Zone.Zone:Read` / `Zone.DNS:Edit` permission errors

The Cloudflare API token in use doesn't have the right scope for the operation attempted. Double-check the token's permissions against the table in [getting-started.md](getting-started.md#cloudflare-api-tokens) — in particular, confirm you're not accidentally using the read-only token where the write token is needed (or vice versa).
