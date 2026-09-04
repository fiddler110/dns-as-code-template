var CF = NewDnsProvider("cloudflare");

// Cloudflare is not treated as the registrar here — DNS-as-Code only manages
// the zone's DNS records via the Cloudflare API, not domain registration.
var REG = NewRegistrar("none");

// One D("<zone>", ...) block per zone this project manages. dnscontrol
// operates on every block here automatically - the GitHub Actions workflows
// and the pre-push hook need no changes when you add or remove a zone.
//
// This starter ships with two example zones (with a few illustrative records)
// to show what multi-zone support looks like. Replace them with your own
// domain(s) - see docs/getting-started.md for how to import an existing
// zone's live records instead of typing them by hand, and
// docs/record-types.md for the syntax of each record type below.

D("example.com", REG,
	DnsProvider(CF),
	DefaultTTL(1),
	A("@", "203.0.113.10", CF_PROXY_ON),
	CNAME("www", "example.com.", CF_PROXY_ON),
	CNAME("app", "example.com."),
	MX("@", 10, "mx1.example.com."),
	MX("@", 20, "mx2.example.com."),
	TXT("@", "v=spf1 -all"),
	TXT("_dmarc", "v=DMARC1; p=reject; sp=reject; adkim=s; aspf=s;"),
);

D("example.org", REG,
	DnsProvider(CF),
	DefaultTTL(1),
	TXT("@", "v=spf1 -all"),
	TXT("_dmarc", "v=DMARC1; p=reject; sp=reject; adkim=s; aspf=s;"),
);
