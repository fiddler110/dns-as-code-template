# Security

This is a DNS-as-code repository, typically for a small number of Cloudflare zones. For the full
threat model — scoped tokens, the PR-gated apply flow, fork safety, and the Environment
approval-gate tradeoffs — see [docs/security.md](docs/security.md).

## Reporting a security concern

This is for a concern about **this repo's own tooling or process** — e.g. a way `dnsctl.py`,
the CI workflows, or the branch/PR flow could leak a credential or let an unreviewed change reach
Cloudflare. It is **not** the place to report a live DNS incident (unexpected records, a hijacked
zone, etc.) — treat that as an operational incident per [docs/operations.md](docs/operations.md)
instead.

Report privately — email a maintainer or security contact directly, or use your organization's
standard vulnerability-reporting channel — rather than opening a public issue or PR. Replace this
paragraph with your team's actual reporting address once this template is adopted. Please include
enough detail to reproduce the concern (which file/workflow, what you expected vs. what happens).
Do not include real Cloudflare tokens or other secrets in the report.
