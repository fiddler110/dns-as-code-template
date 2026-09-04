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
python scripts/dnsctl.py record prune-acme                # report only, flags names with multiple tokens
python scripts/dnsctl.py record prune-acme --remove 2 3    # remove specific ones by index, with confirmation
```

This is report-first and never guesses which token is still "current" — cross-check against whatever issued the certificate (e.g. your reverse proxy's ACME client logs) before removing one. See [dnsctl-cli.md](dnsctl-cli.md#record-prune-acme).

## Recovering from a bad `apply`

If `dnscontrol push` applies something wrong (bad merge, typo that passed review, etc.):

1. **Don't panic-edit directly in the Cloudflare dashboard** — that just creates more drift to reconcile later.
2. Open a new PR that reverts the bad change in `dnsconfig.js` (`git revert <bad-commit>` is usually cleanest).
3. Confirm the `DNS Preview` comment on that PR shows exactly the correction that undoes the mistake.
4. Merge — `apply.yml` will push the fix to Cloudflare.

For anything affecting mail (MX/SPF/DKIM/DMARC) or the apex A record, this round-trip can take a few minutes to propagate depending on TTLs and Cloudflare's own caching — don't assume it's still broken just because a check a few seconds after merge still shows the old behavior.

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
