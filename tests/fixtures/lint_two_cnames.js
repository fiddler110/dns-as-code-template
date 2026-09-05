var CF = NewDnsProvider("cloudflare");
var REG = NewRegistrar("none");

D("example.com", REG,
	DnsProvider(CF),
	DefaultTTL(1),
	CNAME("dup", "example.com.", CF_PROXY_ON),
	CNAME("dup", "example.org.", CF_PROXY_ON),
);
