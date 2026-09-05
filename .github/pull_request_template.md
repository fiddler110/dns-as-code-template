## What changed and why

<!-- e.g. "Add CNAME for myapp" or "Fix jellyfin ACME record TTL" -->

## Review checklist

- [ ] `python scripts/dnsctl.py lint` passes locally
- [ ] The `DNS Preview` check's diff comment shows **only** the intended correction(s) — no
      unexpected `DELETE`s or unrelated records
- [ ] Record count/diff matches what I expected for this change
- [ ] If this touches mail (MX, SPF/DKIM/DMARC) or the apex domain, I double-checked it carefully
