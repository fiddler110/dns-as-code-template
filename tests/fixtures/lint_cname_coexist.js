var CF = NewDnsProvider("cloudflare");
var REG = NewRegistrar("none");

D("example.com", REG,
	DnsProvider(CF),
	DefaultTTL(1),
	CNAME("dup", "example.com.", CF_PROXY_ON),
	A("dup", "203.0.113.5", CF_PROXY_ON),
);
