# DNS-as-Code (dnscontrol + Cloudflare)

A starter template for managing DNS as code via [dnscontrol](https://docs.dnscontrol.org/), with Cloudflare as the DNS provider: propose changes as pull requests, see the exact diff before anything is applied, and let CI apply merged changes automatically.

- Zone state lives in `dnsconfig.js`, one `D(...)` block per zone — this template ships with two example zones (`example.com`, `example.org`) to show what multi-zone support looks like. Replace them with your own domain(s).
- Every pull request triggers a `dnscontrol preview` run in GitHub Actions that comments the exact diff on the PR.
- Merging to `main` triggers a `dnscontrol push` run using a separate, write-scoped Cloudflare token.

## Using this template

Click **Use this template** on GitHub to create your own copy, then:

1. Replace the example `D(...)` blocks in `dnsconfig.js` with your own domain(s) — see [Getting started](docs/getting-started.md).
2. Create the two Cloudflare API tokens and GitHub secrets described there.
3. Everywhere in `docs/` and this README that still says `example.com`/`example.org`/`YOUR_USERNAME`/`YOUR_REPO` is a placeholder — swap in your actual values.

## Documentation

Full docs live in [`docs/`](docs/README.md):

- [Getting started](docs/getting-started.md) — one-time setup, cloning on a new machine, Cloudflare tokens.
- [Making changes](docs/making-changes.md) — the day-to-day workflow for adding/editing/removing a DNS record.
- [Record types](docs/record-types.md) — syntax reference for A/CNAME/MX/TXT/etc.
- [CI/CD pipeline](docs/ci-cd-pipeline.md) — what the GitHub Actions workflows and the local pre-push hook actually do.
- [Operations](docs/operations.md) — token rotation, re-baselining the zone, recovering from mistakes, troubleshooting.
- [Security model](docs/security.md) — threat model and the reasoning behind the security choices here.
- [dnsctl CLI](docs/dnsctl-cli.md) — `scripts/dnsctl.py`, a cross-platform (Windows/macOS/Linux) helper script for common operations.

## Quick start

```sh
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO
python scripts/dnsctl.py setup                # enables the git hook, scaffolds .env
python scripts/dnsctl.py install-dnscontrol   # downloads dnscontrol for your OS
# edit .env and fill in the read-only Cloudflare API token
python scripts/dnsctl.py doctor
```

See [docs/getting-started.md](docs/getting-started.md) for the full setup, including creating the Cloudflare API tokens and GitHub secrets.

## License

[MIT](LICENSE)
