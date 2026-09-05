# Record type reference

Syntax for the record types used (or likely to be needed) in `dnsconfig.js`. Full reference: [dnscontrol.org DNS record types](https://docs.dnscontrol.org/language-reference/domain-modifiers).

All of these go inside a zone's `D("<zone>", REG, DnsProvider(CF), ...)` block, one per line, comma-separated — this project has one such block per zone it manages (currently `example.com` and `example.org`) in `dnsconfig.js`. The examples below use `example.com`, but the same syntax applies inside any zone's block.

## A — point a name at an IPv4 address

```js
A("@", "203.0.113.10", CF_PROXY_ON),
A("myserver", "203.0.113.20"),
```

- `"@"` means the bare apex domain (`example.com` itself).
- Third argument `CF_PROXY_ON` routes traffic through Cloudflare's proxy (orange cloud) — hides the origin IP, adds Cloudflare's CDN/WAF. Omit it (or use `CF_PROXY_OFF`) for a plain "grey cloud" DNS-only record.

## CNAME — alias one name to another hostname

```js
CNAME("www", "example.com.", CF_PROXY_ON),
CNAME("jf", "example.com."),
```

- The target **must end with a trailing dot** — it's a fully-qualified domain name, not relative.
- A CNAME can't coexist with other records on the same name (e.g. you can't have both a `CNAME` and a `TXT` on `www`) — this is a DNS-wide rule, not specific to this project.

## MX — mail routing

```js
MX("@", 10, "mx1.example.com."),
MX("@", 20, "mx2.example.com."),
```

- Second argument is priority (lower = preferred).
- Target needs the trailing dot.
- MX records control whether the zone can receive email at all — changing or removing them is high-risk. If you use Cloudflare's own Email Routing product, it will give you specific `*.mx.cloudflare.net` hostnames to put here instead of your own mail servers.

## TXT — arbitrary text (SPF, DKIM, DMARC, ACME challenges, verification codes)

```js
TXT("@", "v=spf1 -all"),
TXT("_dmarc", "v=DMARC1; p=reject; sp=reject; adkim=s; aspf=s; rua=mailto:..."),
TXT("_acme-challenge", "some-validation-token", TTL(120)),
```

(The SPF example above (`v=spf1 -all`) means "no servers are authorized to send mail for this domain" — a safe default for a domain that doesn't send email. If you do send mail, replace it with your actual mail provider's SPF include, e.g. `v=spf1 include:_spf.mx.cloudflare.net ~all` for Cloudflare Email Routing.)

- Multiple `TXT` records can exist on the same name (e.g. several `_acme-challenge` tokens) — just add multiple lines, one per value.
- `TTL(120)` sets a specific TTL in seconds for that one record, overriding the zone's `DefaultTTL`.
- SPF/DKIM/DMARC changes affect email deliverability and anti-spoofing — treat as high-risk, same as MX.

## NS — delegate a subdomain to different nameservers

```js
NS("subdomain", "ns1.otherprovider.com."),
```

Not currently used in this zone, but relevant if a subdomain needs to be delegated elsewhere (e.g. to a different DNS provider for testing).

## CAA — restrict which Certificate Authorities can issue TLS certs for this domain

```js
CAA("@", "issue", "letsencrypt.org"),
CAA("@", "issuewild", "letsencrypt.org"),
CAA("@", "iodef", "mailto:you@example.com"),
```

Not currently used, but worth adding if you want to lock down which CAs are allowed to issue
certificates for `example.com` — without it, any public CA can be persuaded to issue a cert for the
domain.

- **`issue`** — CAs allowed to issue any (non-wildcard) certificate. One `CAA` line per allowed
  issuer.
- **`issuewild`** — CAs allowed to issue *wildcard* certificates (e.g. `*.example.com`). Can be a
  different, narrower list than `issue`; use `CAA("@", "issuewild", ";")` to forbid wildcard
  issuance entirely.
- **`iodef`** — a `mailto:` (or `https:`) URL some CAs will notify if they receive a request for a
  certificate that violates this policy. Not universally honored, but harmless to set.
- `dnsctl record add` does not support CAA (it only covers A/CNAME/MX/TXT) — add or edit these by
  hand directly in `dnsconfig.js`.

## SRV — service discovery records

```js
SRV("_service._tcp", 10, 60, 5060, "target.example.com."),
```

Arguments: priority, weight, port, target. Not currently used in this zone.

## TTL and DefaultTTL

```js
DefaultTTL(1),
```

`DefaultTTL(1)` at the top of the `D(...)` block sets the zone-wide default. TTL `1` is Cloudflare-specific shorthand for **"automatic"** — Cloudflare manages the actual TTL itself (this is normal for proxied records and matches how this zone was originally configured in the dashboard). Override per-record with `TTL(seconds)` as a trailing argument, as seen on the `_acme-challenge` TXT records above (`TTL(120)`).

## Cloudflare-specific modifiers

- `CF_PROXY_ON` / `CF_PROXY_OFF` — toggle the orange-cloud proxy on `A`/`CNAME`/`AAAA` records. Proxied records get Cloudflare's CDN, WAF, and DDoS protection, but the record's TTL is effectively controlled by Cloudflare (hence `TTL(1)` above). Unproxied ("DNS only") records resolve directly to the target — needed for anything that can't sit behind Cloudflare's proxy (e.g. some mail or SSH-adjacent services).

## Where to look up anything not covered here

The full, authoritative list of record types and their arguments lives in the dnscontrol docs: <https://docs.dnscontrol.org/language-reference/domain-modifiers>. The Cloudflare-provider-specific extras (like `CF_PROXY_ON`) are documented at <https://docs.dnscontrol.org/provider/cloudflare>.
