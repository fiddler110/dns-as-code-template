# Azure Key Vault access for contractors

This is the admin-facing runbook for granting and revoking a contractor's access to the read-only
Cloudflare token stored in Azure Key Vault. For *why* Key Vault is used at all, see
[security.md#key-vault-backed-tokens](security.md#key-vault-backed-tokens). For what a contractor
themselves runs, see [contractor-setup-guide.md](contractor-setup-guide.md). This doc is the
"how does the vault owner actually grant/revoke it" piece connecting the two.

## What's in the vault

One vault holds one secret this project cares about:

| Secret name (default) | Value | Who reads it |
|---|---|---|
| `cloudflare-api-token-readonly` | The **read-only** Cloudflare API token (`Zone.DNS:Read`, `Zone.Zone:Read`) | Anyone granted the role below — contractors and staff alike |

The **write**-scoped token is never in Key Vault, never on any contractor's machine — it lives only
as the `CLOUDFLARE_API_TOKEN_WRITE` GitHub Actions secret used by `apply.yml`. Key Vault only ever
holds the token that can read/preview, never the one that can change DNS. Losing control of a
contractor's vault access is bounded by that fact.

## Prerequisites (one-time, per vault)

1. The vault's **permission model** must be **Azure RBAC**, not the legacy "Vault access policy"
   model — only RBAC supports scoping a role assignment down to a single secret instead of the
   whole vault. Check/set this under the vault's **Access configuration** blade, or:
   ```sh
   az keyvault update --name <vault-name> --resource-group <rg> --enable-rbac-authorization true
   ```
2. The `cloudflare-api-token-readonly` secret already exists in the vault (create it once with the
   current read-only Cloudflare token as its value — same value that would otherwise go in a local
   `.env`).
3. You (the person granting access) have a role capable of assigning roles on the vault —
   `Owner`, `User Access Administrator`, or equivalent — on the subscription/resource group/vault.

## Onboarding a contractor

1. **Get their Azure AD (Entra ID) identity.** A contractor needs an account in the tenant that
   owns this vault — either a native account you create for them, or (more commonly) their own
   organization's account added as a **guest (B2B)** user. Either way, use a real, individually
   attributable identity — never a shared login.

2. **Grant the narrowest role that works, scoped to the secret, not the vault:**

   ```sh
   az role assignment create \
     --role "Key Vault Secrets User" \
     --assignee <contractor-upn-or-object-id> \
     --scope "/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.KeyVault/vaults/<vault-name>/secrets/cloudflare-api-token-readonly"
   ```

   `Key Vault Secrets User` grants `get`/`list` on secrets only — no write, no delete, no access to
   any other secret you might later add to the same vault. Scoping to the specific secret (rather
   than the whole vault) means a contractor's access can never accidentally cover something else
   you store there later. If the vault holds nothing but this one secret, scoping to the vault
   itself (drop `/secrets/cloudflare-api-token-readonly` from the scope) is an acceptable
   simplification — but the secret-level scope costs nothing extra and is worth doing by default.

   **Never** grant `Key Vault Secrets Officer` (adds write/delete) or `Key Vault Administrator` to a
   contractor — those are for whoever manages the vault itself, not for someone who only needs to
   read one value at runtime.

3. **Tell the contractor the vault name** (`CLOUDFLARE_KEYVAULT_NAME`) — that, plus their own Azure
   login, is everything [contractor-setup-guide.md](contractor-setup-guide.md) needs from you. You
   do not need to send them a token, a connection string, or any other secret material — the role
   assignment on their identity is the credential.

4. **Verify the grant actually works** before considering onboarding done:
   ```sh
   az login   # as the contractor, or ask them to confirm this step themselves
   az keyvault secret show --vault-name <vault-name> --name cloudflare-api-token-readonly --query value -o tsv
   ```
   A successful run prints the token value; a `Forbidden`/`Access denied` error means the role
   assignment hasn't propagated yet (can take a few minutes) or was scoped incorrectly.

## Offboarding a contractor

Do this the moment an engagement ends — this is the whole point of using Key Vault over a static
token in the first place (see [security.md](security.md#key-vault-backed-tokens)):

1. **Remove the role assignment:**
   ```sh
   az role assignment delete \
     --role "Key Vault Secrets User" \
     --assignee <contractor-upn-or-object-id> \
     --scope "/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.KeyVault/vaults/<vault-name>/secrets/cloudflare-api-token-readonly"
   ```
   List current assignments first if you don't have the exact scope/assignee handy:
   ```sh
   az role assignment list --scope "/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.KeyVault/vaults/<vault-name>" --output table
   ```
2. **Disable or remove their Azure AD identity** (or, for a guest account, remove them from the
   tenant) if the vault was the only reason they had that identity at all — belt-and-suspenders on
   top of the role removal.
3. **Also revoke repo access** (GitHub team membership / collaborator removal) — see
   [DNS_Change_Process.md#access-boundaries-that-make-this-safe-for-contractors-by-construction](DNS_Change_Process.md#access-boundaries-that-make-this-safe-for-contractors-by-construction).
   Vault access and repo access are separate controls; offboarding means removing both.
4. **Confirm it actually took effect** — have the contractor (or, once they're gone, re-run from a
   test identity with the same former role) attempt the `az keyvault secret show` command above and
   confirm it now fails. Don't assume the removal worked without checking; role-assignment
   propagation delay works in both directions.

**You do not need to rotate the underlying Cloudflare token on a normal offboarding** — the whole
value of this setup is that removing the role assignment immediately cuts off that person's ability
to fetch it, with nothing left over to remember. Rotate the token anyway if you have reason to
believe it was copied out of the vault into a file, screen-shared, or otherwise potentially exposed
beyond the vault's own access control — see [operations.md#rotating-cloudflare-api-tokens](operations.md#rotating-cloudflare-api-tokens).

## Auditing who currently has access

```sh
az role assignment list \
  --scope "/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.KeyVault/vaults/<vault-name>/secrets/cloudflare-api-token-readonly" \
  --output table
```

Review this periodically (e.g. alongside the [token rotation cadence](operations.md#rotating-cloudflare-api-tokens))
and whenever a contractor's engagement status is in question — it's the ground truth for "who can
currently pull the Cloudflare token," independent of whatever your ticketing/HR system believes.

## Troubleshooting

**`(Forbidden) The user, group or application ... does not have secrets get permission`** — the role
assignment either hasn't propagated yet (wait a few minutes and retry), was scoped to the wrong
vault/secret, or was never actually created — re-run the `az role assignment create` command above
and confirm with `az role assignment list`.

**Contractor's `az login` opens the wrong tenant** — if they have accounts in multiple Azure AD
tenants (their own employer's plus this one as a guest), they need
`az login --tenant <tenant-id>` — give them the tenant ID as part of onboarding, alongside the vault
name.

**`az` reports success but `dnsctl.py doctor` still says the token isn't available** — confirm
`CLOUDFLARE_KEYVAULT_NAME` is actually set (in `.env` or the shell environment) and that
`CLOUDFLARE_API_TOKEN` is **not** also set anywhere — see `load_cloudflare_env` in
`scripts/dnsctl.py`: a literal `CLOUDFLARE_API_TOKEN` in `.env`/the environment always takes
priority over the vault and will silently mask a vault misconfiguration.
