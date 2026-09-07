# Validation testing

Some of this pipeline's guarantees can only really be proven by triggering the failure condition
for real and watching the check catch it — a workflow "running successfully" on the happy path
isn't evidence it actually blocks the bad case it exists to block. These are the manual procedures
for that, useful the first time you stand this template up for real (after replacing the example
zones and enabling the workflow triggers) and worth re-running after a meaningful change to the
corresponding workflow.

Use a genuinely throwaway change for all of these — they touch either a live-looking CI run or, in
one case, live DNS twice. Clean up afterward every time.

---

## Verifying server-side secret scanning

**Goal:** prove Gitleaks (`.github/workflows/secret-scan.yml`) actually catches a fake-token-shaped
push, not just that the workflow runs.

1. Start a test branch: `python scripts/dnsctl.py begin "test: verify gitleaks secret scan"` (or
   `git checkout -b test/gitleaks-verify` from `main`).
2. Add a fake, token-shaped secret to a throwaway file — **do not use AWS's own documentation
   example credentials** (`AKIAIOSFODNN7EXAMPLE` /
   `wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY`). Gitleaks' default ruleset specifically allowlists
   those exact strings since they appear in essentially every AWS SDK/doc example on the internet —
   using them will make the check pass with "no leaks found" even though it's working correctly.
   Use a random, made-up value in the right shape instead, e.g.:
   ```yaml
   AWS_ACCESS_KEY_ID: AKIAT5ZOMLQMFAKEX123 # gitleaks:allow
   AWS_SECRET_ACCESS_KEY: 8xQ3mK9vLpR2wZ7yN4bC6dF1gH5jT0sA9uE3iO8k # gitleaks:allow
   AWS_DEFAULT_REGION: us-east-1
   ```
3. Commit and push: `git add <file> && git commit -m "test: gitleaks verification" && git push -u origin <branch>`.
4. Open a PR (`gh pr create --fill`) so both the `push` and `pull_request` triggers fire.
5. Confirm the **Secret Scan** check fails with a Gitleaks finding (`gh run list --workflow "Secret Scan"`).
6. Clean up: close the PR without merging, delete the branch.

If the first attempt passes with "no leaks found," check whether the value you used happens to
match gitleaks' built-in allowlist (the AWS example credentials above are the most common trap) —
that's a false pass caused by the allowlist, not evidence the check is broken. Retry with a
genuinely random value in the right shape before concluding anything.

---

## Verifying the applied-diff-matches-reviewed-diff guard

**Goal:** prove `apply.yml` refuses to push when live Cloudflare state changed after a PR's
`preview` ran but before merge — this only matters once triggers are enabled and real credentials
are wired up.

1. Start a real (or trivial/reversible) DNS change: `python scripts/dnsctl.py begin "test: verify diff-mismatch guard"`,
   edit `dnsconfig.js` (e.g. add a throwaway TXT record), then
   `python scripts/dnsctl.py lint && python scripts/dnsctl.py preview`.
2. Commit, push, open the PR. Wait for the **DNS Preview** check to complete — this run's
   `preview_output.txt` artifact is what `apply.yml` will later diff against.
3. **Before merging**, make an unrelated, throwaway edit directly in the Cloudflare dashboard (e.g.
   a harmless TXT record in one of your zones, not touched by your PR). This simulates out-of-band
   drift between review and apply.
4. Merge the PR.
5. Watch the **DNS Apply** run. It re-runs `preview` right before push and diffs it against the
   artifact from step 2 — since live state now differs, it should fail at "Verify applied diff
   matches reviewed diff" with an `::error::` about the mismatch, and must **not** call
   `dnscontrol push`.
6. Clean up: remove the throwaway dashboard record from step 3, then handle your test PR's own
   change through the normal flow (revert via a follow-up PR, or fold it in properly).

**Sequencing matters.** The drift must be introduced strictly *after* the PR's reviewed preview
completes and *before* merge — drift present before the PR opens gets captured identically in both
the reviewed and apply-time previews and produces a false pass (the guard looks like it worked, but
it was never actually exercised). Confirm the drift is genuinely live via `dnscontrol get-zones`
before merging, not just DNS resolution, which can lag behind a dashboard edit.

---

## Verifying DNSSEC

Entirely manual/out-of-band — nothing in `dnsconfig.js` or the `apply` flow touches it. See
[operations.md#enable-dnssec-one-time-per-zone](operations.md#enable-dnssec-one-time-per-zone) for
the full enable + verify procedure per zone. There's no CI-side check to run here; the only "test"
is the chain-of-trust validation at <https://dnsviz.net/> after the DS record is live at the
registrar.
