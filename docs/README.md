# Documentation index

This project manages DNS for `example.com` and `example.org` as code, using [dnscontrol](https://docs.dnscontrol.org/) against Cloudflare. Start with whichever doc matches what you're trying to do:

| Doc | Read this when... |
|---|---|
| [getting-started.md](getting-started.md) | You're setting this project up for the first time, or cloning it on a new machine. |
| [making-changes.md](making-changes.md) | You need to add, edit, or remove a DNS record (the day-to-day workflow). |
| [record-types.md](record-types.md) | You need syntax/examples for a specific record type (A, CNAME, MX, TXT, etc.). |
| [ci-cd-pipeline.md](ci-cd-pipeline.md) | You want to understand exactly what the GitHub Actions workflows do and why. |
| [operations.md](operations.md) | You're rotating tokens, re-importing the zone, recovering from a mistake, or debugging a failure. |
| [security.md](security.md) | You want the threat model and reasoning behind the security choices in this repo. |
| [dnsctl-cli.md](dnsctl-cli.md) | You want to use `scripts/dnsctl.py`, the cross-platform helper script, instead of running dnscontrol commands by hand. |

## The one-sentence version

`dnsconfig.js` is the source of truth for the DNS records of every zone this project manages (`example.com`, `example.org`); every change goes through a pull request, gets an automatic dry-run diff from `dnscontrol preview`, and only reaches Cloudflare via `dnscontrol push` after merging to `main`.
