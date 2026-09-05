var CF = NewDnsProvider("cloudflare");
var REG = NewRegistrar("none");

D("example.com", REG,
	DnsProvider(CF),
	DefaultTTL(1),
	A("@", "203.0.113.5", CF_PROXY_ON),
	CNAME("www", "example.com.", CF_PROXY_ON),
	MX("@", 50, "amir.mx.cloudflare.net."),
	TXT("_acme-challenge", "token-a", TTL(120)),
);

D("example.org", REG,
	DnsProvider(CF),
	DefaultTTL(1),
	A("@", "203.0.113.10", CF_PROXY_ON),
	CNAME("www", "example.org.", CF_PROXY_ON),
);
